from flask import Flask
import threading
import time
import os
import sys

# Agregar el directorio actual al path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

app = Flask(__name__)

# Variable global para controlar el bot
bot_thread = None

def run_bot():
    try:
        print("🤖 Iniciando bot de trading...")
        from main import BOOM1000CandleAnalyzer
        
        # Obtener variables de entorno
        DEMO_TOKEN = os.environ.get('TOKEN', 'a1-m63zGttjKYP6vUq8SIJdmySH8d3Jc')
        TELEGRAM_BOT_TOKEN = os.environ.get('7868591681:AAGYeuSUwozg3xTi1zmxPx9gWRP2xsXP0Uc', '')
        TELEGRAM_CHAT_ID = os.environ.get('-1003028922957', '')
        
        analyzer = BOOM1000CandleAnalyzer(
            token=DEMO_TOKEN,
            telegram_token=TELEGRAM_BOT_TOKEN,
            telegram_chat_id=TELEGRAM_CHAT_ID
        )
        analyzer.run()
    except Exception as e:
        print(f"❌ Error en bot: {e}")
        import traceback
        traceback.print_exc()

@app.route('/')
def home():
    return "✅ Bot BOOM1000 funcionando - " + time.ctime()

@app.route('/health')
def health():
    return "OK"

# Iniciar bot solo una vez
if not bot_thread:
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
