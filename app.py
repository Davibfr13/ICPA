import os
import uuid
import base64
import threading
import time
import sqlite3
import requests
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from datetime import datetime, timezone
from apscheduler.schedulers.background import BackgroundScheduler
from PIL import Image
from contextlib import closing

# === CONFIGURAÇÃO FLASK ===
# Servir arquivos estáticos diretamente da raiz (onde estão index.html e calendar.html)
app = Flask(__name__, static_folder='.', static_url_path='')
CORS(app)

UPLOAD_FOLDER = 'uploads'
THUMB_FOLDER = os.path.join(UPLOAD_FOLDER, 'thumbs')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(THUMB_FOLDER, exist_ok=True)

DATABASE = 'whatsapp_scheduler.db'
EVOLUTION_URL = "http://localhost:8080"
INSTANCE_NAME = os.getenv("INSTANCE_NAME", "ICPA")
API_KEY = os.getenv("API_KEY", "your_api_key_here")

# === BANCO DE DADOS ===
def init_db():
    with sqlite3.connect(DATABASE) as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS scheduled_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL UNIQUE,
                number TEXT NOT NULL,
                media_path TEXT,
                thumbnail_path TEXT,
                mediatype TEXT,
                caption TEXT,
                scheduled_at TEXT NOT NULL,
                status TEXT,
                last_attempt TEXT,
                error TEXT
            )
        ''')
init_db()

scheduler = BackgroundScheduler()
scheduler.start()

# === FUNÇÕES AUXILIARES ===
def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

def is_base64(sb):
    try:
        if isinstance(sb, str):
            sb_bytes = sb.encode('utf-8')
        else:
            sb_bytes = sb
        return base64.b64encode(base64.b64decode(sb_bytes)) == sb_bytes
    except:
        return False

def save_file(file_data, ext):
    filename = f"{uuid.uuid4().hex}.{ext}"
    path = os.path.join(UPLOAD_FOLDER, filename)
    with open(path, "wb") as f:
        f.write(base64.b64decode(file_data))
    return path

def create_thumbnail(filepath, mediatype='image'):
    try:
        if mediatype != 'image':
            return None
        thumb_path = os.path.join(THUMB_FOLDER, os.path.basename(filepath) + ".png")
        img = Image.open(filepath)
        img.thumbnail((100, 100))
        img.save(thumb_path)
        return thumb_path
    except Exception as e:
        print("Erro ao criar thumbnail:", e)
        return None

# === ROTAS DE FRONTEND ===
@app.route('/')
def index():
    try:
        # Caminho de frontend customizado
        if os.path.exists('main/index.html'):
            return send_from_directory('main', 'index.html')
        else:
            # Interface simples com link para Evolution Manager
            evolution_url = f"http://localhost:8080/manager"
            public_url = request.host_url.rstrip('/')
            evolution_public = f"{public_url}:8080/manager"

            return f"""
            <!DOCTYPE html>
            <html>
            <head>
                <title>ICPA WhatsApp Scheduler</title>
                <style>
                    body {{ font-family: Arial, sans-serif; margin: 40px; background: #fafafa; }}
                    .container {{ max-width: 800px; margin: 0 auto; text-align: center; }}
                    h1 {{ color: #333; }}
                    .btn {{
                        display: inline-block;
                        background-color: #2b7cff;
                        color: white;
                        padding: 12px 20px;
                        border-radius: 6px;
                        text-decoration: none;
                        font-weight: bold;
                        margin-top: 20px;
                    }}
                    .btn:hover {{ background-color: #1f5ed9; }}
                </style>
            </head>
            <body>
                <div class="container">
                    <h1>ICPA WhatsApp Scheduler</h1>
                    <p>API Flask e Evolution API estão em execução.</p>
                    <p><b>Frontend:</b> {'main/index.html' if os.path.exists('main/index.html') else 'Não encontrado'}</p>
                    <a href="{evolution_public}" target="_blank" class="btn">🌐 Abrir Gerenciador Evolution</a>
                    <div style="margin-top:40px; text-align:left; background:#f5f5f5; padding:20px; border-radius:8px;">
                        <h3>Endpoints disponíveis:</h3>
                        <ul>
                            <li><a href="/api/health">/api/health</a> - Health check</li>
                            <li><a href="/api/scheduled">/api/scheduled</a> - Mensagens agendadas</li>
                            <li>POST /api/send-media - Enviar mídia</li>
                            <li>POST /api/schedule-media - Agendar mídia</li>
                        </ul>
                    </div>
                </div>
            </body>
            </html>
            """
    except Exception as e:
        return f"Erro ao carregar página: {str(e)}"


@app.route('/calendar')
def calendar():
    return app.send_static_file('calendar.html')

@app.route('/uploads/<path:filename>')
def uploaded_file(filename):
    return send_from_directory(UPLOAD_FOLDER, filename)

@app.route('/uploads/thumbs/<path:filename>')
def uploaded_thumb(filename):
    return send_from_directory(THUMB_FOLDER, filename)

# === ROTA DE SAÚDE (TESTE) ===
@app.route('/api/health', methods=['GET'])
def health():
    ok = False
    try:
        with get_db() as conn:
            conn.execute("SELECT 1")
        ok = True
    except:
        ok = False
    return jsonify({
        "status": "healthy" if ok else "unhealthy",
        "index_exists": os.path.exists('index.html'),
        "calendar_exists": os.path.exists('calendar.html')
    })

if __name__ == '__main__':
    port = int(os.getenv("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
