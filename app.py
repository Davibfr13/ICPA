import os
import uuid
import base64
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

API_KEY = os.getenv("API_KEY", "0D30D72C9D86-4A07-A3B9-CDAD39907EBC")
INSTANCE_NAME = os.getenv("INSTANCE_NAME", "ICPA")
EVOLUTION_URL = os.getenv("EVOLUTION_URL", "https://evolution-lg1k.onrender.com/message/sendMedia/ICPA")

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres.ermyehfyvbllpvavqwzt:fa26grxB%23UCGPT%23@aws-1-us-east-1.pooler.supabase.com:5432/postgres"
)

UPLOAD_FOLDER = os.path.join(BASE_DIR, 'uploads')
THUMB_FOLDER = os.path.join(UPLOAD_FOLDER, 'thumbs')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(THUMB_FOLDER, exist_ok=True)


# ======================
# BANCO DE DADOS (PostgreSQL)
# ======================

def get_db():
    """Abre conexão com o banco PostgreSQL."""
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)


def init_db():
    """Cria a tabela caso não exista."""
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


init_db()

scheduler = BackgroundScheduler()
scheduler.start()


# ======================
# FUNÇÕES AUXILIARES
# ======================

def save_file(file_data, ext):
    """Salva arquivo base64 e retorna o caminho."""
    filename = f"{uuid.uuid4().hex}.{ext}"
    path = os.path.join(UPLOAD_FOLDER, filename)
    with open(path, "wb") as f:
        f.write(base64.b64decode(file_data))
    return path


def create_thumbnail(filepath, mediatype='image'):
    """Cria miniatura de imagem."""
    try:
        if mediatype != 'image':
            return None
        thumb_path = os.path.join(THUMB_FOLDER, os.path.basename(filepath))
        img = Image.open(filepath)
        img.thumbnail((100, 100))
        img.save(thumb_path)
        return thumb_path
    except Exception as e:
        print("Erro ao criar thumbnail:", e)
        return None


# ======================
# ENVIO DE MENSAGEM
# ======================

def send_message_to_evolution(job_id):
    """Executa o envio da mensagem via Evolution API."""
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM scheduled_messages WHERE job_id = %s", (job_id,))
        row = cur.fetchone()
        if not row:
            print(f"⚠️ Job {job_id} não encontrado.")
            cur.close()
            conn.close()
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
            error_msg = f"Erro ao ler arquivo: {e}"
            cur.execute(
                "UPDATE scheduled_messages SET status=%s, last_attempt=NOW(), error=%s WHERE job_id=%s",
                ("erro", error_msg, job_id)
            )
            conn.commit()
            return

        try:
            response = requests.post(EVOLUTION_URL, json=payload, headers={"apikey": API_KEY}, timeout=30)
            if response.status_code == 200:
                cur.execute(
                    "UPDATE scheduled_messages SET status=%s, last_attempt=NOW(), error=NULL WHERE job_id=%s",
                    ("enviado", job_id)
                )
                print(f"✅ Mensagem {job_id} enviada com sucesso.")
            else:
                error_msg = f"Erro HTTP {response.status_code}: {response.text}"
                cur.execute(
                    "UPDATE scheduled_messages SET status=%s, last_attempt=NOW(), error=%s WHERE job_id=%s",
                    ("erro", error_msg, job_id)
                )
                print(f"❌ Falha ao enviar mensagem {job_id}: {error_msg}")
            conn.commit()
        except Exception as e:
            error_msg = f"Erro de conexão: {e}"
            cur.execute(
                "UPDATE scheduled_messages SET status=%s, last_attempt=NOW(), error=%s WHERE job_id=%s",
                ("erro", error_msg, job_id)
            )
            conn.commit()
            print(f"⚠️ Erro de conexão: {error_msg}")

        cur.close()
        conn.close()
    except Exception as e:
        print(f"Erro crítico: {e}")


# ======================
# RECARREGAR AGENDAMENTOS
# ======================

def reload_pending_schedules():
    """Recarrega mensagens agendadas pendentes ao iniciar."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM scheduled_messages WHERE status = 'agendado'")
    rows = cur.fetchall()
    for row in rows:
        job_id = row["job_id"]
        scheduled_at = row["scheduled_at"]
        if scheduled_at > datetime.now(timezone.utc):
            if not scheduler.get_job(job_id):
                scheduler.add_job(send_message_to_evolution, 'date', run_date=scheduled_at, args=[job_id], id=job_id)
                print(f"🔁 Agendamento recarregado: {job_id}")
    cur.close()
    conn.close()


reload_pending_schedules()


# ======================
# ROTAS DE FRONTEND
# ======================

@app.route('/')
def index():
    return app.send_static_file('index.html')


@app.route('/calendar')
def calendar():
    return app.send_static_file('calendar.html')


@app.route('/uploads/<path:filename>')
def uploaded_file(filename):
    return send_from_directory(UPLOAD_FOLDER, filename)


@app.route('/uploads/thumbs/<path:filename>')
def uploaded_thumb(filename):
    return send_from_directory(THUMB_FOLDER, filename)


# ======================
# ROTA DE SAÚDE
# ======================

@app.route('/api/health', methods=['GET'])
def health():
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT 1")
        conn.close()
        return jsonify({
            "status": "healthy",
            "database": "PostgreSQL",
            "evolution_url": EVOLUTION_URL
        }), 200
    except Exception as e:
        return jsonify({
            "status": "unhealthy",
            "error": str(e)
        }), 500


# ======================
# INICIALIZAÇÃO
# ======================

def initialize_app():
    print("🚀 Servidor Flask iniciado no Render.")
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM scheduled_messages")
        count = cur.fetchone()["count"]
        print(f"📦 Banco conectado com sucesso — {count} mensagens agendadas.")
        cur.close()
        conn.close()
    except Exception as e:
        print(f"❌ Erro ao conectar ao banco: {e}")


initialize_app()


if __name__ == '__main__':
    port = int(os.getenv("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
