import os
import uuid
import base64
import threading
import requests
import psycopg2
from psycopg2.extras import RealDictCursor
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime, timezone
from PIL import Image

# ======================
# CONFIGURAÇÕES GERAIS
# ======================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(__name__, static_folder='.', static_url_path='')
CORS(app)

GLOBAL_API_KEY = os.getenv("GLOBAL_API_KEY", "fmFeKYVdcU06C3S57mmVZ4BhsEwdVIww")
INSTANCE_NAME = os.getenv("INSTANCE_NAME", "ICPA")
EVOLUTION_URL = os.getenv("EVOLUTION_URL", "https://evolution-lg1k.onrender.com/message/sendMedia/ICPA")

# Supabase / PostgreSQL URL - REMOVA ?schema=public
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:FA26grxB%23UCGPT%23@aws-1-us-east-1.pooler.supabase.com:6543/postgres"
)

UPLOAD_FOLDER = os.path.join(BASE_DIR, 'uploads')
THUMB_FOLDER = os.path.join(UPLOAD_FOLDER, 'thumbs')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(THUMB_FOLDER, exist_ok=True)

THUMBNAIL_SIZE = (100, 100)

# ======================
# BANCO DE DADOS
# ======================
def get_db():
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
    # define schema padrão
    cur = conn.cursor()
    cur.execute("SET search_path TO public;")
    cur.close()
    return conn

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
            status TEXT NOT NULL DEFAULT 'agendado',
            last_attempt TIMESTAMP WITH TIME ZONE,
            error TEXT
        );
    """)
    conn.commit()
    cur.close()
    conn.close()

# ======================
# SCHEDULER
# ======================
scheduler = BackgroundScheduler()
scheduler.start()

# ======================
# UTILITÁRIOS
# ======================
def is_base64(sb):
    try:
        if isinstance(sb, str):
            sb_bytes = sb.encode('utf-8')
        else:
            sb_bytes = sb
        return base64.b64encode(base64.b64decode(sb_bytes)) == sb_bytes
    except Exception:
        return False

def save_file(file_data_b64, ext):
    filename = f"{uuid.uuid4().hex}.{ext}"
    path = os.path.join(UPLOAD_FOLDER, filename)
    with open(path, "wb") as f:
        f.write(base64.b64decode(file_data_b64))
    return path

def create_thumbnail(filepath, mediatype='image'):
    try:
        if mediatype != 'image':
            return None
        img = Image.open(filepath)
        img.thumbnail(THUMBNAIL_SIZE)
        thumb_name = os.path.basename(filepath)
        thumb_path = os.path.join(THUMB_FOLDER, thumb_name)
        img.save(thumb_path)
        return thumb_path
    except Exception as e:
        print("Erro ao criar thumbnail:", e)
        return None

# ======================
# ENVIO
# ======================
def send_message_to_evolution(job_id):
    conn = None
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM scheduled_messages WHERE job_id = %s", (job_id,))
        row = cur.fetchone()
        if not row:
            print(f"Job {job_id} não encontrado.")
            return

        payload = {
            "number": row["number"],
            "mediatype": row["mediatype"],
            "media": None,
            "caption": row["caption"] or ""
        }

        try:
            with open(row["media_path"], "rb") as f:
                payload["media"] = base64.b64encode(f.read()).decode()
        except Exception as e:
            cur.execute(
                "UPDATE scheduled_messages SET status=%s, last_attempt=NOW(), error=%s WHERE job_id=%s",
                ("erro", f"Erro ao ler arquivo: {e}", job_id)
            )
            conn.commit()
            print(f"Erro ao ler arquivo: {e}")
            return

        try:
            headers = {"apikey": GLOBAL_API_KEY}
            resp = requests.post(EVOLUTION_URL, json=payload, headers=headers, timeout=30)
            if resp.status_code == 200:
                cur.execute(
                    "UPDATE scheduled_messages SET status=%s, last_attempt=NOW(), error=NULL WHERE job_id=%s",
                    ("enviado", job_id)
                )
                print(f"Mensagem {job_id} enviada com sucesso.")
            else:
                cur.execute(
                    "UPDATE scheduled_messages SET status=%s, last_attempt=NOW(), error=%s WHERE job_id=%s",
                    ("erro", f"Erro HTTP {resp.status_code}: {resp.text}", job_id)
                )
                print(f"Falha no envio {job_id}: {resp.status_code}")
            conn.commit()
        except Exception as e:
            cur.execute(
                "UPDATE scheduled_messages SET status=%s, last_attempt=NOW(), error=%s WHERE job_id=%s",
                ("erro", f"Erro de conexão: {e}", job_id)
            )
            conn.commit()
            print(f"Erro de conexão: {e}")

        cur.close()
    except Exception as e:
        print("Erro crítico no envio:", e)
    finally:
        if conn:
            conn.close()

# ======================
# RELOAD JOBS
# ======================
def reload_pending_schedules():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM scheduled_messages WHERE status = 'agendado'")
    rows = cur.fetchall()
    for row in rows:
        job_id = row["job_id"]
        scheduled_at = row["scheduled_at"]
        if scheduled_at and scheduled_at > datetime.now(timezone.utc):
            if not scheduler.get_job(job_id):
                scheduler.add_job(send_message_to_evolution, 'date', run_date=scheduled_at, args=[job_id], id=job_id)
                print(f"Agendamento recarregado: {job_id}")
    cur.close()
    conn.close()

# ======================
# ROTAS
# ======================
def check_apikey(req):
    key = req.headers.get('apikey') or req.headers.get('APIKEY') or req.args.get('apikey')
    return key == GLOBAL_API_KEY

@app.route('/')
def index():
    return send_from_directory(BASE_DIR, 'index.html')

@app.route('/calendar')
def calendar():
    return send_from_directory(BASE_DIR, 'calendar.html')

@app.route('/uploads/<path:filename>')
def uploaded_file(filename):
    return send_from_directory(UPLOAD_FOLDER, filename)

@app.route('/uploads/thumbs/<path:filename>')
def uploaded_thumb(filename):
    return send_from_directory(THUMB_FOLDER, filename)

@app.route('/api/health', methods=['GET'])
def health():
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT 1")
        cur.close()
        conn.close()
        return jsonify({"status": "healthy", "database": "PostgreSQL", "evolution_url": EVOLUTION_URL})
    except Exception as e:
        return jsonify({"status": "unhealthy", "error": str(e)}), 500

# ======================
# INICIALIZAÇÃO (Gunicorn)
# ======================
if __name__ != "__main__":
    init_db()
    reload_pending_schedules()
    print("🚀 Flask pronto para Gunicorn no Render.")
