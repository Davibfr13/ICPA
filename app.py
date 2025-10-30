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
<<<<<<< HEAD
import base64, requests, psycopg2, uuid
from psycopg2.extras import RealDictCursor
from PIL import Image
=======
>>>>>>> 5d60cddd646831c2f182e6257375eb7205be0943
from apscheduler.schedulers.background import BackgroundScheduler
from PIL import Image
from contextlib import closing

# === CONFIGURAÇÃO FLASK ===
# Servir arquivos estáticos diretamente da raiz (onde estão index.html e calendar.html)
app = Flask(__name__, static_folder='.', static_url_path='')
CORS(app)

<<<<<<< HEAD
# ======================
# CONFIGURAÇÕES GERAIS
# ======================
API_KEY = os.getenv("API_KEY", "65BCAE7AB84F-4AA5-A836-D9BF5FBEE3B5")
INSTANCE_NAME = os.getenv("INSTANCE_NAME", "ICPA")
EVOLUTION_URL = os.getenv("EVOLUTION_URL", "https://evolution-lg1k.onrender.com/message/sendMedia/ICPA")

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres.ermyehfyvbllpvavqwzt:fa26grxB%23UCGPT%23@aws-1-us-east-1.pooler.supabase.com:5432/postgres?schema=public"
)

UPLOAD_FOLDER = os.path.join(BASE_DIR, 'uploads')
=======
UPLOAD_FOLDER = 'uploads'
>>>>>>> 5d60cddd646831c2f182e6257375eb7205be0943
THUMB_FOLDER = os.path.join(UPLOAD_FOLDER, 'thumbs')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(THUMB_FOLDER, exist_ok=True)

<<<<<<< HEAD
# ======================
# BANCO DE DADOS (PostgreSQL)
# ======================
def get_db():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

def init_db():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS scheduled_messages (
            id SERIAL PRIMARY KEY,
            job_id TEXT NOT NULL UNIQUE,
            number TEXT NOT NULL,
            media_path TEXT NOT NULL,
            thumbnail_path TEXT,
            mediatype TEXT NOT NULL,
            caption TEXT,
            scheduled_at TIMESTAMP WITH TIME ZONE NOT NULL,
            status TEXT NOT NULL,
            last_attempt TIMESTAMP WITH TIME ZONE,
            error TEXT
        );
    """)
    conn.commit()
    cur.close()
    conn.close()

=======
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
>>>>>>> 5d60cddd646831c2f182e6257375eb7205be0943
init_db()

scheduler = BackgroundScheduler()
scheduler.start()

<<<<<<< HEAD
# ======================
# FUNÇÕES AUXILIARES
# ======================
=======
# === FUNÇÕES AUXILIARES ===
def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

>>>>>>> 5d60cddd646831c2f182e6257375eb7205be0943
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

<<<<<<< HEAD
# ======================
# FUNÇÃO DE ENVIO
# ======================
def send_message_to_evolution(job_id):
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM scheduled_messages WHERE job_id = %s", (job_id,))
        row = cur.fetchone()
        if not row:
            print(f"Job {job_id} não encontrado")
            cur.close()
            conn.close()
            return

        payload = {
            "number": row['number'],
            "mediatype": row['mediatype'],
            "media": None,
            "caption": row['caption'] or ''
        }

        try:
            with open(row['media_path'], 'rb') as f:
                payload['media'] = base64.b64encode(f.read()).decode()
        except Exception as e:
            error_msg = f"Erro ao ler arquivo: {str(e)}"
            cur.execute("UPDATE scheduled_messages SET status=%s, last_attempt=NOW(), error=%s WHERE job_id=%s",
                        ("erro", error_msg, job_id))
            conn.commit()
            return

        try:
            response = requests.post(EVOLUTION_URL, json=payload, headers={"apikey": API_KEY}, timeout=30)
            if response.status_code == 200:
                cur.execute("UPDATE scheduled_messages SET status=%s, last_attempt=NOW(), error=NULL WHERE job_id=%s",
                            ("enviado", job_id))
                conn.commit()
                print(f"Mensagem {job_id} enviada com sucesso")
            else:
                error_msg = f"Erro HTTP {response.status_code}: {response.text}"
                cur.execute("UPDATE scheduled_messages SET status=%s, last_attempt=NOW(), error=%s WHERE job_id=%s",
                            ("erro", error_msg, job_id))
                conn.commit()
                print(f"Erro ao enviar mensagem {job_id}: {error_msg}")
        except Exception as e:
            error_msg = f"Erro de conexão: {str(e)}"
            cur.execute("UPDATE scheduled_messages SET status=%s, last_attempt=NOW(), error=%s WHERE job_id=%s",
                        ("erro", error_msg, job_id))
            conn.commit()
            print(f"Erro de conexão para mensagem {job_id}: {error_msg}")

        cur.close()
        conn.close()
    except Exception as e:
        print(f"Erro crítico no processamento: {str(e)}")

# ======================
# RECARREGA AGENDAMENTOS
# ======================
def reload_pending_schedules():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM scheduled_messages WHERE status='agendado'")
    rows = cur.fetchall()
    for row in rows:
        job_id = row['job_id']
        scheduled_at = row['scheduled_at']
        if scheduled_at > datetime.now(timezone.utc):
            if not scheduler.get_job(job_id):
                scheduler.add_job(send_message_to_evolution, 'date', run_date=scheduled_at, args=[job_id], id=job_id)
                print(f"Agendamento recarregado: {job_id}")
    cur.close()
    conn.close()

reload_pending_schedules()

# ======================
# ROTAS
# ======================
=======
# === ROTAS DE FRONTEND ===
>>>>>>> 5d60cddd646831c2f182e6257375eb7205be0943
@app.route('/')
def index():
    return app.send_static_file('index.html')

@app.route('/calendar')
def calendar():
<<<<<<< HEAD
    return send_from_directory(BASE_DIR, 'calendar.html')

@app.route('/api/health')
def health():
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT 1")
        conn.close()
        return jsonify({"status": "healthy", "database": "PostgreSQL", "evolution_url": EVOLUTION_URL}), 200
    except Exception as e:
        return jsonify({"status": "unhealthy", "error": str(e)}), 500

# ======================
# INICIALIZAÇÃO
# ======================
def initialize_app():
    print("🚀 Servidor Flask iniciado no Render")
    print(f"Banco: {DATABASE_URL.split('@')[1]}")
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM scheduled_messages")
        count = cur.fetchone()['count']
        print(f"Banco OK — {count} mensagens encontradas.")
        cur.close()
        conn.close()
    except Exception as e:
        print(f"Erro ao conectar banco: {e}")

initialize_app()
=======
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
>>>>>>> 5d60cddd646831c2f182e6257375eb7205be0943

if __name__ == '__main__':
    port = int(os.getenv("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
