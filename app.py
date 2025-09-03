from flask import Flask
import threading
import time
import os
import sys

# Agregar el directorio actual al path para importar main.py
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

app = Flask(__name__)

# Importar y ejecutar el bot directamente
def run_bot():
    try:
        print("🤖 Iniciando bot de trading...")
        from main import BOOM1000CandleAnalyzer, DEMO_TOKEN, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
        
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

# Iniciar bot en segundo plano
bot_thread = threading.Thread(target=run_bot, daemon=True)
bot_thread.start()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
