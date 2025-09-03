import websocket
import json
import threading
import time
import numpy as np
import talib
from datetime import datetime
import ssl
from collections import deque
import requests
import logging
import sys

# Configurar logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("BOOM1000Bot")

class BOOM1000CandleAnalyzer:
    def __init__(self, token, app_id="88258", telegram_token=None, telegram_chat_id=None):
        # --- Configuración de Conexión ---
        self.ws_url = f"wss://ws.derivws.com/websockets/v3?app_id={app_id}"
        self.token = token
        self.ws = None
        self.connected = False
        self.authenticated = False
        self.reconnect_attempts = 0
        self.max_reconnect_attempts = 10
        
        # --- Configuración de Telegram ---
        self.telegram_token = telegram_token
        self.telegram_chat_id = telegram_chat_id
        self.telegram_enabled = telegram_token is not None and telegram_chat_id is not None

        # --- Configuración de Trading ---
        self.symbol = "BOOM1000"
        self.candle_interval_seconds = 60
        self.min_candles = 50

        # --- Parámetros de la Estrategia ---
        self.ema_fast_period = 9
        self.ema_slow_period = 21
        self.ema_trend_period = 50
        self.rsi_period = 14
        self.atr_period = 14
        self.sl_atr_multiplier = 1.5
        self.tp_atr_multiplier = 2.0

        # --- Almacenamiento de Datos ---
        self.ticks_for_current_candle = []
        self.candles = deque(maxlen=200)
        self.last_candle_timestamp = 0
        self.new_candle_ready = False

        # --- Estado de Señales ---
        self.last_signal_time = 0
        self.signal_cooldown = self.candle_interval_seconds * 2

    # --- Método para enviar mensajes a Telegram ---
    def send_telegram_message(self, message):
        if not self.telegram_enabled:
            logger.info("Telegram no está configurado. No se enviará mensaje.")
            return False
            
        try:
            url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
            payload = {
                "chat_id": self.telegram_chat_id,
                "text": message,
                "parse_mode": "HTML"
            }
            response = requests.post(url, json=payload, timeout=10)
            if response.status_code == 200:
                logger.info("Señal enviada a Telegram")
                return True
            else:
                logger.error(f"Error al enviar a Telegram: {response.status_code} - {response.text}")
                return False
        except Exception as e:
            logger.error(f"Excepción al enviar a Telegram: {e}")
            return False

    # --- Métodos de Conexión Mejorados ---
    def connect(self):
        logger.info("Conectando a Deriv API...")
        self.ws = websocket.WebSocketApp(
            self.ws_url,
            on_open=self.on_open,
            on_message=self.on_message,
            on_error=self.on_error,
            on_close=self.on_close
        )
        wst = threading.Thread(target=self.ws.run_forever, kwargs={
            'sslopt': {"cert_reqs": ssl.CERT_NONE}, 
            'ping_interval': 30, 
            'ping_timeout': 10,
            'reconnect': 5  # Intentar reconectar automáticamente
        })
        wst.daemon = True
        wst.start()
        
        # Esperar a que la conexión se establezca con timeout
        timeout = 30
        start_time = time.time()
        while not self.connected and (time.time() - start_time) < timeout:
            time.sleep(1)
            
        if self.connected:
            logger.info("Conexión establecida exitosamente")
            return True
        else:
            logger.error("Timeout al conectar con Deriv API")
            return False

    def on_open(self, ws):
        logger.info("Conexión WebSocket abierta")
        self.connected = True
        self.reconnect_attempts = 0  # Resetear contador de reconexiones
        ws.send(json.dumps({"authorize": self.token}))

    def on_close(self, ws, close_status_code, close_msg):
        logger.warning(f"Conexión cerrada: {close_status_code} - {close_msg}")
        self.connected = False
        self.authenticated = False
        
        # Intentar reconectar después de un delay
        if self.reconnect_attempts < self.max_reconnect_attempts:
            self.reconnect_attempts += 1
            reconnect_delay = min(2 ** self.reconnect_attempts, 60)  # Exponential backoff
            logger.info(f"Reconectando en {reconnect_delay} segundos (intento {self.reconnect_attempts}/{self.max_reconnect_attempts})")
            time.sleep(reconnect_delay)
            self.connect()

    def on_error(self, ws, error):
        logger.error(f"Error WebSocket: {error}")

    def on_message(self, ws, message):
        try:
            data = json.loads(message)
            if "error" in data:
                logger.error(f"Error: {data['error'].get('message', 'Error desconocido')}")
                return
            if "authorize" in data:
                if data["authorize"].get("error"):
                    logger.error(f"Error de autenticación: {data['authorize']['error']['message']}")
                    return
                self.authenticated = True
                logger.info("Autenticación exitosa.")
                self.subscribe_to_ticks()
            elif "tick" in data:
                self.handle_tick(data['tick'])
        except Exception as e:
            logger.error(f"Error procesando mensaje: {e}")

    def subscribe_to_ticks(self):
        logger.info(f"Suscribiendo a ticks de {self.symbol}...")
        self.ws.send(json.dumps({"ticks": self.symbol, "subscribe": 1}))
        logger.info("Recopilando datos para formar la primera vela...")

    def handle_tick(self, tick):
        try:
            price = float(tick['quote'])
            timestamp = int(tick['epoch'])

            current_candle_start_time = timestamp - (timestamp % self.candle_interval_seconds)

            if self.last_candle_timestamp == 0:
                self.last_candle_timestamp = current_candle_start_time

            if current_candle_start_time > self.last_candle_timestamp:
                self._finalize_candle()
                self.last_candle_timestamp = current_candle_start_time

            self.ticks_for_current_candle.append(price)

        except Exception as e:
            logger.error(f"Error en handle_tick: {e}")

    def _finalize_candle(self):
        if not self.ticks_for_current_candle:
            return

        prices = np.array(self.ticks_for_current_candle)
        candle = {
            'timestamp': self.last_candle_timestamp,
            'open': prices[0],
            'high': np.max(prices),
            'low': np.min(prices),
            'close': prices[-1],
            'volume': len(prices)
        }
        self.candles.append(candle)
        self.ticks_for_current_candle = []
        self.new_candle_ready = True

        if len(self.candles) >= self.min_candles:
            logger.info(f"Nueva vela cerrada. Total: {len(self.candles)}. Precio Cierre: {candle['close']:.2f}")

    def analyze_market(self):
        if len(self.candles) < self.min_candles:
            logger.info(f"Recopilando velas iniciales: {len(self.candles)}/{self.min_candles}")
            return

        opens = np.array([c['open'] for c in self.candles], dtype=float)
        highs = np.array([c['high'] for c in self.candles], dtype=float)
        lows = np.array([c['low'] for c in self.candles], dtype=float)
        closes = np.array([c['close'] for c in self.candles], dtype=float)

        try:
            ema_fast = talib.EMA(closes, timeperiod=self.ema_fast_period)
            ema_slow = talib.EMA(closes, timeperiod=self.ema_slow_period)
            ema_trend = talib.EMA(closes, timeperiod=self.ema_trend_period)
            rsi = talib.RSI(closes, timeperiod=self.rsi_period)
            atr = talib.ATR(highs, lows, closes, timeperiod=self.atr_period)
        except Exception as e:
            logger.error(f"Error calculando indicadores: {e}")
            return

        last_close = closes[-1]
        last_atr = atr[-1]

        is_uptrend = ema_fast[-1] > ema_slow[-1] and ema_slow[-1] > ema_trend[-1]
        is_downtrend = ema_fast[-1] < ema_slow[-1] and ema_slow[-1] < ema_trend[-1]

        signal = None
        current_time = time.time()

        if current_time - self.last_signal_time < self.signal_cooldown:
            return

        # Señal de COMPRA (BUY)
        if is_uptrend and ema_fast[-2] <= ema_slow[-2] and ema_fast[-1] > ema_slow[-1]:
            if rsi[-1] > 40 and rsi[-1] < 70:
                signal = "BUY"

        # Señal de VENTA (SELL)
        if is_downtrend and ema_fast[-2] >= ema_slow[-2] and ema_fast[-1] < ema_slow[-1]:
            if rsi[-1] < 60 and rsi[-1] > 30:
                signal = "SELL"

        if signal:
            self.last_signal_time = current_time
            self.display_signal(signal, last_close, last_atr, rsi[-1])
            
            # Enviar señal a Telegram
            if self.telegram_enabled:
                telegram_msg = self.format_telegram_message(signal, last_close, last_atr, rsi[-1])
                self.send_telegram_message(telegram_msg)

    def format_telegram_message(self, direction, price, atr_value, rsi_value):
        if direction == "BUY":
            sl = price - (atr_value * self.sl_atr_multiplier)
            tp = price + (atr_value * self.tp_atr_multiplier)
            direction_emoji = "📈"
        else:  # SELL
            sl = price + (atr_value * self.sl_atr_multiplier)
            tp = price - (atr_value * self.tp_atr_multiplier)
            direction_emoji = "📉"
            
        message = f"""
🚀 <b>SEÑAL DE TRADING - BOOM 1000</b> 🚀

{direction_emoji} <b>Dirección:</b> {direction}
💰 <b>Precio Entrada:</b> {price:.2f}
🎯 <b>Take Profit:</b> {tp:.2f}
🛑 <b>Stop Loss:</b> {sl:.2f}

📊 <b>Indicadores:</b>
   • RSI: {rsi_value:.1f}
   • ATR: {atr_value:.2f}

⏰ <b>Hora:</b> {datetime.now().strftime('%H:%M:%S')}

#Trading #Señal #BOOM1000
"""
        return message

    def display_signal(self, direction, price, atr_value, rsi_value):
        if direction == "BUY":
            sl = price - (atr_value * self.sl_atr_multiplier)
            tp = price + (atr_value * self.tp_atr_multiplier)
            color_code = "\033[92m"
        else:  # SELL
            sl = price + (atr_value * self.sl_atr_multiplier)
            tp = price - (atr_value * self.tp_atr_multiplier)
            color_code = "\033[91m"

        reset_code = "\033[0m"

        logger.info("\n" + "="*60)
        logger.info(f"🎯 {color_code}NUEVA SEÑAL DE TRADING - BOOM 1000{reset_code}")
        logger.info("="*60)
        logger.info(f"   📈 Dirección: {color_code}{direction}{reset_code}")
        logger.info(f"   💰 Precio de Entrada: {price:.2f}")
        logger.info(f"   🎯 Take Profit (TP): {tp:.2f} (Basado en ATR x{self.tp_atr_multiplier})")
        logger.info(f"   🛑 Stop Loss (SL): {sl:.2f} (Basado en ATR x{self.sl_atr_multiplier})")
        logger.info(f"   ⏰ Hora: {datetime.now().strftime('%H:%M:%S')}")
        logger.info(f"   📊 Info: RSI={rsi_value:.1f}, ATR={atr_value:.2f}")
        logger.info("="*60)

    def run(self):
        logger.info("\n" + "="*60)
        logger.info("🤖 ANALIZADOR BOOM 1000 v2.0 - ESTRATEGIA DE VELAS")
        logger.info("="*60)
        logger.info("🧠 ESTRATEGIA:")
        logger.info(f"   • Análisis en velas de {self.candle_interval_seconds} segundos.")
        logger.info(f"   • Filtro de tendencia con EMA {self.ema_trend_period}.")
        logger.info(f"   • Entrada por cruce de EMAs {self.ema_fast_period}/{self.ema_slow_period}.")
        logger.info(f"   • TP/SL dinámico con ATR({self.atr_period}) x{self.tp_atr_multiplier}/{self.sl_atr_multiplier}.")
        
        if self.telegram_enabled:
            logger.info("   📱 Notificaciones Telegram: ACTIVADAS")
        else:
            logger.info("   📱 Notificaciones Telegram: DESACTIVADAS")
            
        logger.info("="*60)

        while True:
            if self.connect():
                try:
                    while self.connected:
                        if self.new_candle_ready:
                            self.analyze_market()
                            self.new_candle_ready = False
                        time.sleep(1)
                except Exception as e:
                    logger.error(f"Error en el bucle principal: {e}")
                    time.sleep(10)  # Esperar antes de reintentar
            else:
                logger.error("No se pudo conectar a Deriv. Reintentando en 30 segundos...")
                time.sleep(30)

# --- Ejecución ---
if __name__ == "__main__":
    # Obtener variables de entorno para Railway
    import os
    
    DEMO_TOKEN = os.environ.get("DERIV_TOKEN", "a1-m63zGttjKYP6vUq8SIJdmySH8d3Jc")
    TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
    TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "-1003028922957")
    
    analyzer = BOOM1000CandleAnalyzer(
        DEMO_TOKEN, 
        telegram_token=TELEGRAM_BOT_TOKEN,
        telegram_chat_id=TELEGRAM_CHAT_ID
    )
    
    # Enviar mensaje de inicio a Telegram
    if analyzer.telegram_enabled:
        analyzer.send_telegram_message("🤖 <b>Bot de Trading BOOM1000 iniciado</b>\n\nEl bot está ahora funcionando 24/7 en Railway.")
    
    analyzer.run()