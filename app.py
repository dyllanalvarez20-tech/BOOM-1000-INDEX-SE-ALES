from flask import Flask
import threading
import subprocess
import time

app = Flask(__name__)

def run_bot():
    try:
        print("🤖 Iniciando bot de trading...")
        subprocess.run(["python", "main.py"])
    except Exception as e:
        print(f"❌ Error en bot: {e}")

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
