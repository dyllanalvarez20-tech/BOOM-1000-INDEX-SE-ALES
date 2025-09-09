import websocket
import json
import threading
import time
import numpy as np
from datetime import datetime
import ssl
from collections import deque
import requests
from flask import Flask, jsonify
import atexit

app = Flask(__name__)

class BOOM1000CandleAnalyzer:
    def __init__(self, token, app_id="88258", telegram_token=None, telegram_chat_id=None):
        # --- Configuración de Conexión ---
        self.ws_url = f"wss://ws.derivws.com/websockets/v3?app_id={app_id}"
        self.token = token
        self.ws = None
        self.connected = False
        self.authenticated = False
        self.last_reconnect_time = time.time()
        self.service_url = "https://boom-1000-index-se-ales.onrender.com"

        # --- Configuración de Telegram ---
        self.telegram_token = telegram_token
        self.telegram_chat_id = telegram_chat_id
        self.telegram_enabled = telegram_token is not None and telegram_chat_id is not None

        # --- Configuración de Trading ---
        self.symbol = "BOOM1000"
        self.candle_interval_seconds = 60
        self.min_candles = 50

        # --- Parámetros Optimizados para Spikes Alcistas ---
        self.ema_fast_period = 8
        self.ema_slow_period = 21
        self.ema_trend_period = 50
        self.macd_fast = 8
        self.macd_slow = 21
        self.macd_signal = 9
        self.rsi_period = 14
        self.stoch_k = 10
        self.stoch_d = 3
        self.stoch_slow = 3
        self.atr_period = 14
        self.sl_atr_multiplier = 2.5
        self.tp_atr_multiplier = 3.5
        self.volume_ma_period = 20
        self.min_volume_ratio = 1.2
        self.min_price_change = 0.5

        # --- Almacenamiento de Datos ---
        self.ticks_for_current_candle = []
        self.candles = deque(maxlen=200)
        self.last_candle_timestamp = 0
        self.new_candle_ready = False

        # --- Estado de Señales ---
        self.last_signal_time = 0
        self.signal_cooldown = self.candle_interval_seconds * 2
        self.last_signal = None
        self.signals_history = []
        self.consecutive_signals = 0
        self.max_consecutive_signals = 3

        # Iniciar en un hilo separado
        self.thread = threading.Thread(target=self.run_analyzer, daemon=True)
        self.thread.start()

    def self_ping(self):
        """Función para hacerse ping a sí mismo y evitar que Render duerma el servicio"""
        try:
            health_url = f"{self.service_url}/health"
            response = requests.get(health_url, timeout=10)
            print(f"✅ Self-ping exitoso: {response.status_code}")
            return True
        except Exception as e:
            print(f"❌ Error en self-ping: {e}")
            return False

    # --- Métodos para calcular indicadores MANUALMENTE ---
    def calculate_ema(self, prices, period):
        """Calcula EMA manualmente"""
        if len(prices) < period:
            return np.array([np.nan] * len(prices))
        
        ema = np.zeros(len(prices))
        k = 2 / (period + 1)
        
        # Primer valor EMA es SMA simple
        ema[period-1] = np.mean(prices[:period])
        
        # Calcular EMA para los valores restantes
        for i in range(period, len(prices)):
            ema[i] = (prices[i] * k) + (ema[i-1] * (1 - k))
        
        return ema

    def calculate_sma(self, values, period):
        """Calcula SMA manualmente"""
        if len(values) < period:
            return np.array([np.nan] * len(values))
        
        sma = np.zeros(len(values))
        for i in range(period-1, len(values)):
            sma[i] = np.mean(values[i-period+1:i+1])
        
        return sma

    def calculate_macd(self, prices, fast_period=12, slow_period=26, signal_period=9):
        """Calcula MACD manualmente"""
        if len(prices) < slow_period + signal_period:
            return np.zeros(len(prices)), np.zeros(len(prices)), np.zeros(len(prices))
        
        # Calcular EMAs
        ema_fast = self.calculate_ema(prices, fast_period)
        ema_slow = self.calculate_ema(prices, slow_period)
        
        # MACD Line
        macd_line = ema_fast - ema_slow
        
        # Signal Line (EMA of MACD Line)
        signal_line = self.calculate_ema(macd_line, signal_period)
        
        # MACD Histogram
        macd_histogram = macd_line - signal_line
        
        return macd_line, signal_line, macd_histogram

    def calculate_rsi(self, prices, period=14):
        """Calcula RSI manualmente"""
        if len(prices) < period + 1:
            return np.array([np.nan] * len(prices))
        
        deltas = np.diff(prices)
        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)
        
        avg_gain = np.zeros(len(prices))
        avg_loss = np.zeros(len(prices))
        rsi = np.zeros(len(prices))
        
        # Valores iniciales
        avg_gain[period] = np.mean(gains[:period])
        avg_loss[period] = np.mean(losses[:period])
        
        for i in range(period + 1, len(prices)):
            avg_gain[i] = (avg_gain[i-1] * (period - 1) + gains[i-1]) / period
            avg_loss[i] = (avg_loss[i-1] * (period - 1) + losses[i-1]) / period
        
        for i in range(period, len(prices)):
            if avg_loss[i] == 0:
                rsi[i] = 100
            else:
                rs = avg_gain[i] / avg_loss[i]
                rsi[i] = 100 - (100 / (1 + rs))
        
        return rsi

    def calculate_stochastic(self, highs, lows, closes, k_period=14, d_period=3, slow_period=3):
        """Calcula Estocástico manualmente"""
        if len(closes) < k_period + d_period:
            return np.zeros(len(closes)), np.zeros(len(closes))
        
        stoch_k = np.zeros(len(closes))
        
        for i in range(k_period-1, len(closes)):
            highest_high = np.max(highs[i-k_period+1:i+1])
            lowest_low = np.min(lows[i-k_period+1:i+1])
            
            if highest_high != lowest_low:
                stoch_k[i] = 100 * (closes[i] - lowest_low) / (highest_high - lowest_low)
            else:
                stoch_k[i] = 50
        
        # Suavizar con período lento
        stoch_k_smoothed = self.calculate_sma(stoch_k, slow_period)
        
        # Línea D (SMA de la línea K)
        stoch_d = self.calculate_sma(stoch_k_smoothed, d_period)
        
        return stoch_k_smoothed, stoch_d

    def calculate_atr(self, highs, lows, closes, period=14):
        """Calcula ATR manualmente"""
        if len(highs) < period + 1:
            return np.array([np.nan] * len(highs))
        
        tr = np.zeros(len(highs))
        atr = np.zeros(len(highs))
        
        # Calcular True Range
        for i in range(1, len(highs)):
            hl = highs[i] - lows[i]
            hc = abs(highs[i] - closes[i-1])
            lc = abs(lows[i] - closes[i-1])
            tr[i] = max(hl, hc, lc)
        
        # Primer ATR es el promedio simple de los primeros period TR
        atr[period] = np.mean(tr[1:period+1])
        
        # Calcular ATR para los valores restantes
        for i in range(period + 1, len(highs)):
            atr[i] = (atr[i-1] * (period - 1) + tr[i]) / period
        
        return atr

    def calculate_roc(self, prices, period=10):
        """Calcula Rate of Change manualmente"""
        if len(prices) < period:
            return np.zeros(len(prices))
        
        roc = np.zeros(len(prices))
        for i in range(period, len(prices)):
            roc[i] = 100 * (prices[i] - prices[i-period]) / prices[i-period]
        
        return roc

    def calculate_adx(self, highs, lows, closes, period=14):
        """Calcula ADX manualmente (simplificado)"""
        if len(highs) < period * 2:
            return np.zeros(len(highs))
        
        # Calcular +DI y -DI
        plus_di = np.zeros(len(highs))
        minus_di = np.zeros(len(highs))
        
        for i in range(1, len(highs)):
            up_move = highs[i] - highs[i-1]
            down_move = lows[i-1] - lows[i]
            
            if up_move > down_move and up_move > 0:
                plus_di[i] = up_move
            if down_move > up_move and down_move > 0:
                minus_di[i] = down_move
        
        # Suavizar +DI y -DI
        plus_di_sma = self.calculate_sma(plus_di, period)
        minus_di_sma = self.calculate_sma(minus_di, period)
        
        # Calcular DX
        dx = np.zeros(len(highs))
        for i in range(period, len(highs)):
            if plus_di_sma[i] + minus_di_sma[i] > 0:
                dx[i] = 100 * abs(plus_di_sma[i] - minus_di_sma[i]) / (plus_di_sma[i] + minus_di_sma[i])
        
        # ADX es SMA de DX
        adx = self.calculate_sma(dx, period)
        
        return adx

    def calculate_indicators(self, closes, highs, lows, volumes):
        """Calcula todos los indicadores manualmente"""
        indicators = {}
        
        # EMA
        indicators['ema_fast'] = self.calculate_ema(closes, self.ema_fast_period)
        indicators['ema_slow'] = self.calculate_ema(closes, self.ema_slow_period)
        indicators['ema_trend'] = self.calculate_ema(closes, self.ema_trend_period)
        
        # MACD
        indicators['macd'], indicators['macd_signal'], indicators['macd_hist'] = self.calculate_macd(
            closes, self.macd_fast, self.macd_slow, self.macd_signal
        )
        
        # RSI
        indicators['rsi'] = self.calculate_rsi(closes, self.rsi_period)
        
        # Estocástico
        indicators['stoch_k'], indicators['stoch_d'] = self.calculate_stochastic(
            highs, lows, closes, self.stoch_k, self.stoch_d, self.stoch_slow
        )
        
        # ATR
        indicators['atr'] = self.calculate_atr(highs, lows, closes, self.atr_period)
        
        # Volume MA
        indicators['volume_ma'] = self.calculate_sma(volumes, self.volume_ma_period)
        
        # ROC
        indicators['roc'] = self.calculate_roc(closes, 5)
        
        # ADX (simplificado)
        indicators['adx'] = self.calculate_adx(highs, lows, closes, 14)
        
        return indicators

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
        try:
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

            # Esperar a que se conecte
            timeout = 10
            start_time = time.time()
            while not self.connected and time.time() - start_time < timeout:
                time.sleep(0.1)

            return self.connected
        except Exception as e:
            print(f"❌ Error en conexión: {e}")
            return False

    def disconnect(self):
        """Cierra la conexión WebSocket"""
        if self.ws:
            self.ws.close()
            self.connected = False
            self.authenticated = False
            print("🔌 Conexión cerrada manualmente")

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
            'volume': len(prices),
            'price_change': ((prices[-1] - prices[0]) / prices[0]) * 100
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

        # Extraer arrays de numpy
        opens = np.array([c['open'] for c in self.candles], dtype=float)
        highs = np.array([c['high'] for c in self.candles], dtype=float)
        lows = np.array([c['low'] for c in self.candles], dtype=float)
        closes = np.array([c['close'] for c in self.candles], dtype=float)
        volumes = np.array([c['volume'] for c in self.candles], dtype=float)
        price_changes = np.array([c['price_change'] for c in self.candles], dtype=float)

        try:
            # Calcular todos los indicadores MANUALMENTE
            indicators = self.calculate_indicators(closes, highs, lows, volumes)
            ema_fast = indicators['ema_fast']
            ema_slow = indicators['ema_slow']
            ema_trend = indicators['ema_trend']
            macd = indicators['macd']
            macd_signal = indicators['macd_signal']
            macd_hist = indicators['macd_hist']
            rsi = indicators['rsi']
            stoch_k = indicators['stoch_k']
            stoch_d = indicators['stoch_d']
            atr = indicators['atr']
            volume_ma = indicators['volume_ma']
            roc = indicators['roc']
            adx = indicators['adx']
        except Exception as e:
            print(f"❌ Error calculando indicadores: {e}")
            return

        # Verificar si tenemos suficientes datos para análisis
        valid_data = (
            len(closes) >= self.ema_trend_period and
            not np.isnan(ema_fast[-1]) and
            not np.isnan(ema_slow[-1]) and
            not np.isnan(rsi[-1]) and
            not np.isnan(atr[-1])
        )
        
        if not valid_data:
            return

        last_close = closes[-1]
        last_atr = atr[-1]
        current_volume = volumes[-1]
        current_price_change = price_changes[-1]
        avg_volume = volume_ma[-1] if not np.isnan(volume_ma[-1]) and volume_ma[-1] > 0 else current_volume

        # Condiciones de tendencia
        is_strong_uptrend = (
            ema_fast[-1] > ema_slow[-1] and
            ema_slow[-1] > ema_trend[-1] and
            closes[-1] > ema_trend[-1]
        )

        # Condiciones de momentum
        macd_bullish = macd[-1] > macd_signal[-1] and macd_hist[-1] > 0
        strong_momentum = roc[-1] > 1.0 if not np.isnan(roc[-1]) else False

        # Condiciones de RSI
        rsi_ok = rsi[-1] > 50 and rsi[-1] < 75 if not np.isnan(rsi[-1]) else False

        # Estocástico
        stoch_bullish = (
            stoch_k[-1] > stoch_d[-1] and 
            stoch_k[-1] > 50 if not np.isnan(stoch_k[-1]) and not np.isnan(stoch_d[-1]) else False
        )

        # Fuerza de tendencia
        strong_trend = adx[-1] > 25 if not np.isnan(adx[-1]) else True

        # Condiciones de volumen
        volume_ok = current_volume > avg_volume * self.min_volume_ratio

        # Condición de spike
        price_spike = current_price_change > self.min_price_change

        signal = None
        current_time = time.time()

        if current_time - self.last_signal_time < self.signal_cooldown:
            return

        # Señal de COMPRA (BUY)
        if (is_strong_uptrend and
            macd_bullish and
            rsi_ok and
            stoch_bullish and
            volume_ok and
            (price_spike or strong_momentum) and
            strong_trend):

            if (self.last_signal is None or
                self.last_signal['direction'] != 'BUY' or
                self.consecutive_signals < self.max_consecutive_signals):

                signal = "BUY"
                if self.last_signal and self.last_signal['direction'] == 'BUY':
                    self.consecutive_signals += 1
                else:
                    self.consecutive_signals = 1

        if signal:
            self.last_signal_time = current_time
            self.last_signal = {
                'direction': signal,
                'price': last_close,
                'atr': last_atr,
                'rsi': rsi[-1] if not np.isnan(rsi[-1]) else 50,
                'stoch_k': stoch_k[-1] if not np.isnan(stoch_k[-1]) else 50,
                'stoch_d': stoch_d[-1] if not np.isnan(stoch_d[-1]) else 50,
                'macd': macd[-1] if not np.isnan(macd[-1]) else 0,
                'roc': roc[-1] if not np.isnan(roc[-1]) else 0,
                'adx': adx[-1] if not np.isnan(adx[-1]) else 0,
                'volume_ratio': current_volume / avg_volume if avg_volume > 0 else 1,
                'price_change': current_price_change,
                'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            }
            self.signals_history.append(self.last_signal)

            self.display_signal(signal, last_close, last_atr, rsi[-1],
                               stoch_k[-1], stoch_d[-1], macd[-1],
                               current_volume / avg_volume if avg_volume > 0 else 1,
                               roc[-1], adx[-1], current_price_change)

            if self.telegram_enabled:
                telegram_msg = self.format_telegram_message(
                    signal, last_close, last_atr, rsi[-1],
                    stoch_k[-1], stoch_d[-1], macd[-1],
                    current_volume / avg_volume if avg_volume > 0 else 1,
                    roc[-1], adx[-1], current_price_change
                )
                self.send_telegram_message(telegram_msg)

    def format_telegram_message(self, direction, price, atr_value, rsi_value,
                               stoch_k, stoch_d, macd_value, volume_ratio,
                               roc_value, adx_value, price_change):
        sl = price - (atr_value * self.sl_atr_multiplier)
        tp = price + (atr_value * self.tp_atr_multiplier)
        direction_emoji = "📈"

        message = f"""
🚀 <b>SEÑAL DE COMPRA - BOOM 1000 SPIKE</b> 🚀

{direction_emoji} <b>Dirección:</b> {direction}
💰 <b>Precio Entrada:</b> {price:.2f}
🎯 <b>Take Profit:</b> {tp:.2f}
🛑 <b>Stop Loss:</b> {sl:.2f}

📊 <b>Indicadores:</b>
   • RSI: {rsi_value:.1f}
   • ATR: {atr_value:.2f}
   • Estocástico K: {stoch_k:.1f}, D: {stoch_d:.1f}
   • MACD: {macd_value:.4f}
   • ROC: {roc_value:.2f}%
   • ADX: {adx_value:.1f}
   • Volumen: {volume_ratio:.2f}x promedio
   • Cambio Precio: {price_change:.2f}%

⏰ <b>Hora:</b> {datetime.now().strftime('%H:%M:%S')}

#Trading #Señal #BOOM1000 #SPIKE
"""
        return message

    def display_signal(self, direction, price, atr_value, rsi_value,
                      stoch_k, stoch_d, macd_value, volume_ratio,
                      roc_value, adx_value, price_change):
        sl = price - (atr_value * self.sl_atr_multiplier)
        tp = price + (atr_value * self.tp_atr_multiplier)
        color_code = "\033[92m"
        reset_code = "\033[0m"

        print("\n" + "="*70)
        print(f"🎯 {color_code}NUEVA SEÑAL DE COMPRA - BOOM 1000 SPIKE{reset_code}")
        print("="*70)
        print(f"   📈 Dirección: {color_code}{direction}{reset_code}")
        print(f"   💰 Precio de Entrada: {price:.2f}")
        print(f"   🎯 Take Profit (TP): {tp:.2f}")
        print(f"   🛑 Stop Loss (SL): {sl:.2f}")
        print(f"   ⏰ Hora: {datetime.now().strftime('%H:%M:%S')}")
        print(f"   📊 RSI: {rsi_value:.1f}")
        print(f"   📈 Estocástico: K={stoch_k:.1f}, D={stoch_d:.1f}")
        print(f"   📉 MACD: {macd_value:.4f}")
        print(f"   🚀 ROC: {roc_value:.2f}%")
        print(f"   📊 ADX: {adx_value:.1f}")
        print(f"   🔊 Volumen: {volume_ratio:.2f}x promedio")
        print(f"   📈 Cambio Precio: {price_change:.2f}%")
        print("="*70)

    def run_analyzer(self):
        print("\n" + "="*70)
        print("🤖 ANALIZADOR BOOM 1000 v3.0 - SÓLO SEÑALES COMPRA")
        print("="*70)
        print("🧠 ESTRATEGIA OPTIMIZADA PARA SPIKES ALCISTAS:")
        print(f"   • Análisis en velas de {self.candle_interval_seconds} segundos.")
        print(f"   • EMA {self.ema_fast_period}/{self.ema_slow_period}/{self.ema_trend_period}.")
        print(f"   • MACD({self.macd_fast},{self.macd_slow},{self.macd_signal}).")
        print(f"   • RSI({self.rsi_period}) y Estocástico({self.stoch_k},{self.stoch_d}).")
        print(f"   • ROC y ADX para confirmar momentum.")
        print(f"   • Volumen mínimo requerido: {self.min_volume_ratio}x promedio.")
        print(f"   • TP/SL dinámico con ATR({self.atr_period}) x{self.tp_atr_multiplier}/{self.sl_atr_multiplier}.")

        if self.telegram_enabled:
            print("   📱 Notificaciones Telegram: ACTIVADAS")
        else:
            print("   📱 Notificaciones Telegram: DESACTIVADAS")

        print("="*70)

        # Bucle principal con reconexión automática
        reconnect_interval = 15 * 60
        ping_interval = 10 * 60
        
        last_ping_time = time.time()
        last_reconnect_time = time.time()

        while True:
            try:
                current_time = time.time()

                # Auto-ping cada 10 minutos
                if current_time - last_ping_time >= ping_interval:
                    print("🔄 Realizando auto-ping para mantener servicio activo...")
                    self.self_ping()
                    last_ping_time = current_time

                # Reconectar cada 15 minutos o si no está conectado
                if not self.connected or current_time - last_reconnect_time >= reconnect_interval:
                    if self.connected:
                        print("🔄 Reconexión programada (cada 15 minutos)...")
                        self.disconnect()
                        time.sleep(2)

                    last_reconnect_time = current_time

                    if self.connect():
                        print("✅ Reconexión exitosa")
                        while self.connected:
                            if self.new_candle_ready:
                                self.analyze_market()
                                self.new_candle_ready = False
                            time.sleep(1)
                    else:
                        print("❌ No se pudo conectar, reintentando en 30 segundos...")
                        time.sleep(30)
                else:
                    next_action = min(
                        reconnect_interval - (current_time - last_reconnect_time),
                        ping_interval - (current_time - last_ping_time)
                    )
                    if next_action > 0:
                        sleep_time = min(60, next_action)
                        print(f"⏰ Próxima acción en {sleep_time:.0f} segundos")
                        time.sleep(sleep_time)

            except Exception as e:
                print(f"❌ Error crítico en run_analyzer: {e}")
                print("🔄 Reintentando en 30 segundos...")
                time.sleep(30)

# Crear instancia global del analizador
analyzer = None

@app.route('/')
def home():
    return jsonify({
        "status": "running",
        "service": "BOOM 1000 Analyzer v3.0 - Solo COMPRAS",
        "connected": analyzer.connected if analyzer else False,
        "last_signal": analyzer.last_signal if analyzer else None,
        "total_candles": len(analyzer.candles) if analyzer else 0,
        "next_reconnect": analyzer.last_reconnect_time + (15 * 60) - time.time() if analyzer and hasattr(analyzer, 'last_reconnect_time') else 0
    })

@app.route('/health')
def health():
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "connected": analyzer.connected if analyzer else False
    })

@app.route('/signals')
def signals():
    if not analyzer:
        return jsonify({"error": "Analyzer not initialized"})

    return jsonify({
        "last_signal": analyzer.last_signal,
        "history": analyzer.signals_history[-10:] if analyzer.signals_history else [],
        "total_signals": len(analyzer.signals_history)
    })

@app.route('/reconnect')
def manual_reconnect():
    if not analyzer:
        return jsonify({"error": "Analyzer not initialized"})

    analyzer.last_reconnect_time = 0
    return jsonify({"status": "reconnection_triggered", "message": "Se forzará la reconexión en el próximo ciclo"})

def cleanup():
    print("🛑 Cerrando conexiones...")
    if analyzer and analyzer.ws:
        analyzer.ws.close()

atexit.register(cleanup)

if __name__ == "__main__":
    DEMO_TOKEN = "a1-m63zGttjKYP6vUq8SIJdmySH8d3Jc"
    TELEGRAM_BOT_TOKEN = "7868591681:AAGYeuSUwozg3xTi1zmxPx9gWRP2xsXP0Uc"
    TELEGRAM_CHAT_ID = "-1003028922957"

    analyzer = BOOM1000CandleAnalyzer(
        DEMO_TOKEN,
        telegram_token=TELEGRAM_BOT_TOKEN,
        telegram_chat_id=TELEGRAM_CHAT_ID
    )

    print("🚀 Iniciando servidor Flask...")
    app.run(host='0.0.0.0', port=10000, debug=False)
