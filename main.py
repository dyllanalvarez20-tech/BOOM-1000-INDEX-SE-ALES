import websocket
import json
import threading
import time
import numpy as np
import pandas_ta as ta
from datetime import datetime
import ssl
from collections import deque
import requests

class BOOM1000CandleAnalyzer:
    def __init__(self, token, app_id="88258", telegram_token=None, telegram_chat_id=None):
        # --- Configuración de Conexión ---
        self.ws_url = f"wss://ws.derivws.com/websockets/v3?app_id={app_id}"
        self.token = token
        self.ws = None
        self.connected = False
        self.authenticated = False

        # --- Configuración de Telegram ---
        self.telegram_token = telegram_token
        self.telegram_chat_id = telegram_chat_id
        self.telegram_enabled = telegram_token is not None and telegram_chat_id is not None

        # --- Configuración de Trading ---
        self.symbol = "BOOM1000"
        self.candle_interval_seconds = 60
        self.min_candles = 1

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
            print("❌ Telegram no está configurado. No se enviará mensaje.")
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
                print("✅ Señal enviada a Telegram")
                return True
            else:
                print(f"❌ Error al enviar a Telegram: {response.status_code} - {response.text}")
                return False
        except Exception as e:
            print(f"❌ Excepción al enviar a Telegram: {e}")
            return False

    # --- Métodos de Conexión ---
    def connect(self):
        print("🌐 Conectando a Deriv API...")
        self.ws = websocket.WebSocketApp(
            self.ws_url,
            on_open=self.on_open,
            on_message=self.on_message,
            on_error=self.on_error,
            on_close=self.on_close
        )
        wst = threading.Thread(target=self.ws.run_forever, kwargs={
            'sslopt': {"cert_reqs": ssl.CERT_NONE}, 'ping_interval': 30, 'ping_timeout': 10
        })
        wst.daemon = True
        wst.start()
        time.sleep(5)
        return self.connected

    def on_open(self, ws):
        print("✅ Conexión abierta")
        self.connected = True
        ws.send(json.dumps({"authorize": self.token}))

    def on_close(self, ws, close_status_code, close_msg):
        print("🔌 Conexión cerrada")
        self.connected = False
        self.authenticated = False

    def on_error(self, ws, error):
        print(f"❌ Error WebSocket: {error}")

    def on_message(self, ws, message):
        data = json.loads(message)
        if "error" in data:
            print(f"❌ Error: {data['error'].get('message', 'Error desconocido')}")
            return
        if "authorize" in data:
            self.authenticated = True
            print("✅ Autenticación exitosa.")
            self.subscribe_to_ticks()
        elif "tick" in data:
            self.handle_tick(data['tick'])

    def subscribe_to_ticks(self):
        print(f"📊 Suscribiendo a ticks de {self.symbol}...")
        self.ws.send(json.dumps({"ticks": self.symbol, "subscribe": 1}))
        print("⏳ Recopilando datos para formar la primera vela...")

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
            print(f"❌ Error en handle_tick: {e}")

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
            print(f"🕯️ Nueva vela cerrada. Total: {len(self.candles)}. Precio Cierre: {candle['close']:.2f}")

    def analyze_market(self):
        if len(self.candles) < self.min_candles:
            print(f"\r⏳ Recopilando velas iniciales: {len(self.candles)}/{self.min_candles}", end="")
            return

        # Crear DataFrame con los datos de las velas
        df = self.create_dataframe()
        
        try:
            # Calcular indicadores con pandas_ta
            df['ema_fast'] = ta.ema(df['close'], length=self.ema_fast_period)
            df['ema_slow'] = ta.ema(df['close'], length=self.ema_slow_period)
            df['ema_trend'] = ta.ema(df['close'], length=self.ema_trend_period)
            df['rsi'] = ta.rsi(df['close'], length=self.rsi_period)
            df['atr'] = ta.atr(df['high'], df['low'], df['close'], length=self.atr_period)
        except Exception as e:
            print(f"❌ Error calculando indicadores: {e}")
            return

        last_close = df['close'].iloc[-1]
        last_atr = df['atr'].iloc[-1]

        is_uptrend = df['ema_fast'].iloc[-1] > df['ema_slow'].iloc[-1] and df['ema_slow'].iloc[-1] > df['ema_trend'].iloc[-1]
        is_downtrend = df['ema_fast'].iloc[-1] < df['ema_slow'].iloc[-1] and df['ema_slow'].iloc[-1] < df['ema_trend'].iloc[-1]

        signal = None
        current_time = time.time()

        if current_time - self.last_signal_time < self.signal_cooldown:
            return

        # Señal de COMPRA (BUY)
        if is_uptrend and df['ema_fast'].iloc[-2] <= df['ema_slow'].iloc[-2] and df['ema_fast'].iloc[-1] > df['ema_slow'].iloc[-1]:
            if df['rsi'].iloc[-1] > 40 and df['rsi'].iloc[-1] < 70:
                signal = "BUY"

        # Señal de VENTA (SELL)
        if is_downtrend and df['ema_fast'].iloc[-2] >= df['ema_slow'].iloc[-2] and df['ema_fast'].iloc[-1] < df['ema_slow'].iloc[-1]:
            if df['rsi'].iloc[-1] < 60 and df['rsi'].iloc[-1] > 30:
                signal = "SELL"

        if signal:
            self.last_signal_time = current_time
            self.display_signal(signal, last_close, last_atr, df['rsi'].iloc[-1])

            # Enviar señal a Telegram
            if self.telegram_enabled:
                telegram_msg = self.format_telegram_message(signal, last_close, last_atr, df['rsi'].iloc[-1])
                self.send_telegram_message(telegram_msg)

    def create_dataframe(self):
        """Crea un DataFrame pandas a partir de las velas almacenadas"""
        import pandas as pd
        
        data = {
            'timestamp': [c['timestamp'] for c in self.candles],
            'open': [c['open'] for c in self.candles],
            'high': [c['high'] for c in self.candles],
            'low': [c['low'] for c in self.candles],
            'close': [c['close'] for c in self.candles],
            'volume': [c['volume'] for c in self.candles]
        }
        
        df = pd.DataFrame(data)
        return df

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

        print("\n" + "="*60)
        print(f"🎯 {color_code}NUEVA SEÑAL DE TRADING - BOOM 1000{reset_code}")
        print("="*60)
        print(f"   📈 Dirección: {color_code}{direction}{reset_code}")
        print(f"   💰 Precio de Entrada: {price:.2f}")
        print(f"   🎯 Take Profit (TP): {tp:.2f} (Basado en ATR x{self.tp_atr_multiplier})")
        print(f"   🛑 Stop Loss (SL): {sl:.2f} (Basado en ATR x{self.sl_atr_multiplier})")
        print(f"   ⏰ Hora: {datetime.now().strftime('%H:%M:%S')}")
        print(f"   📊 Info: RSI={rsi_value:.1f}, ATR={atr_value:.2f}")
        print("="*60)

    def run(self):
        print("\n" + "="*60)
        print("🤖 ANALIZADOR BOOM 1000 v2.0 - ESTRATEGIA DE VELAS")
        print("="*60)
        print("🧠 ESTRATEGIA:")
        print(f"   • Análisis en velas de {self.candle_interval_seconds} segundos.")
        print(f"   • Filtro de tendencia con EMA {self.ema_trend_period}.")
        print(f"   • Entrada por cruce de EMAs {self.ema_fast_period}/{self.ema_slow_period}.")
        print(f"   • TP/SL dinámico con ATR({self.atr_period}) x{self.tp_atr_multiplier}/{self.sl_atr_multiplier}.")

        if self.telegram_enabled:
            print("   📱 Notificaciones Telegram: ACTIVADAS")
        else:
            print("   📱 Notificaciones Telegram: DESACTIVADAS")

        print("="*60)

        if self.connect():
            try:
                while self.connected:
                    if self.new_candle_ready:
                        self.analyze_market()
                        self.new_candle_ready = False
                    time.sleep(1)
            except KeyboardInterrupt:
                print("\n🛑 Deteniendo analizador...")
        else:
            print("❌ No se pudo conectar a Deriv")

# --- Ejecución ---
if __name__ == "__main__":
    # Reemplaza con tu token real si es necesario
    DEMO_TOKEN = "a1-m63zGttjKYP6vUq8SIJdmySH8d3Jc"

    # Configuración de Telegram (reemplaza con tus datos reales)
    TELEGRAM_BOT_TOKEN = "7868591681:AAGYeuSUwozg3xTi1zmxPx9gWRP2xsXP0Uc"  # Ejemplo: "123456789:AAFmC4f5gH6IjK7L8m9n0oP1qR2sT3uV4wX"
    TELEGRAM_CHAT_ID = "-1003028922957"  # El ID que proporcionaste

    analyzer = BOOM1000CandleAnalyzer(
        DEMO_TOKEN,
        telegram_token=TELEGRAM_BOT_TOKEN,
        telegram_chat_id=TELEGRAM_CHAT_ID
    )
    analyzer.run()
