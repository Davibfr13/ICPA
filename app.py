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

# chave usada para proteger as rotas (verificar header 'apikey')
GLOBAL_API_KEY = os.getenv("GLOBAL_API_KEY", "fmFeKYVdcU06C3S57mmVZ4BhsEwdVIww")
INSTANCE_NAME = os.getenv("INSTANCE_NAME", "ICPA")
EVOLUTION_URL = os.getenv("EVOLUTION_URL", "https://evolution-lg1k.onrender.com/message/sendMedia/ICPA")

# Nota: defina DATABASE_URL no ambiente do Render. Se não, usa o default abaixo.
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres.ermyehfyvbllpvavqwzt:fa26grxB%23UCGPT%23@aws-1-us-east-1.pooler.supabase.com:6543/postgres?schema=public"
)

UPLOAD_FOLDER = os.path.join(BASE_DIR, 'uploads')
THUMB_FOLDER = os.path.join(UPLOAD_FOLDER, 'thumbs')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(THUMB_FOLDER, exist_ok=True)

THUMBNAIL_SIZE = (100, 100)

# ======================
# BANCO DE DADOS (PostgreSQL)
# ======================
def get_db():
    """Abre conexão com o Postgres usando RealDictCursor para retornar dict-like rows."""
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)


def init_db():
    """Cria a tabela se não existir."""
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
    """Salva um arquivo base64 e retorna o caminho absoluto."""
    filename = f"{uuid.uuid4().hex}.{ext}"
    path = os.path.join(UPLOAD_FOLDER, filename)
    with open(path, "wb") as f:
        f.write(base64.b64decode(file_data_b64))
    return path


def create_thumbnail(filepath, mediatype='image'):
    """Cria thumbnail (apenas para imagens). Retorna caminho absoluto ou None."""
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
# LÓGICA DE ENVIO
# ======================
def send_message_to_evolution(job_id):
    """Busca job no DB, monta payload e chama Evolution API. Atualiza status no DB."""
    conn = None
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM scheduled_messages WHERE job_id = %s", (job_id,))
        row = cur.fetchone()
        if not row:
            print(f"Job {job_id} não encontrado.")
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
            cur.close()
            conn.close()
            print(error_msg)
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
                error_msg = f"Erro HTTP {resp.status_code}: {resp.text}"
                cur.execute(
                    "UPDATE scheduled_messages SET status=%s, last_attempt=NOW(), error=%s WHERE job_id=%s",
                    ("erro", error_msg, job_id)
                )
                print(f"Falha no envio {job_id}: {error_msg}")
            conn.commit()
        except Exception as e:
            error_msg = f"Erro de conexão: {e}"
            cur.execute(
                "UPDATE scheduled_messages SET status=%s, last_attempt=NOW(), error=%s WHERE job_id=%s",
                ("erro", error_msg, job_id)
            )
            conn.commit()
            print(error_msg)

        cur.close()
    except Exception as e:
        print("Erro crítico no envio:", e)
    finally:
        if conn:
            conn.close()


# ======================
# RECARREGAR AGENDAMENTOS (ao iniciar)
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

reload_pending_schedules()

# ======================
# ROTAS API
# ======================

def check_apikey(req):
    key = req.headers.get('apikey') or req.headers.get('APIKEY') or req.args.get('apikey')
    return key == GLOBAL_API_KEY


@app.route('/api/send-media', methods=['POST'])
def api_send_media():
    """Recebe mídia base64 e envia imediatamente (background)"""
    if not check_apikey(request):
        return jsonify({"error": "Unauthorized"}), 401
    try:
        data = request.get_json()
        number = data.get('number')
        media_b64 = data.get('media')
        mediatype = data.get('mediatype', 'image')
        caption = data.get('caption', '')

        if not number or not media_b64 or not is_base64(media_b64):
            return jsonify({"error": "Número ou mídia inválidos"}), 400

        ext = 'png' if mediatype == 'image' else ('mp4' if mediatype == 'video' else 'pdf')
        media_path = save_file(media_b64, ext)
        thumb_path = create_thumbnail(media_path, mediatype)
        job_id = str(uuid.uuid4())

        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO scheduled_messages (job_id, number, media_path, thumbnail_path, mediatype, caption, scheduled_at, status)
            VALUES (%s, %s, %s, %s, %s, %s, NOW(), %s)
        """, (job_id, number, media_path, thumb_path, mediatype, caption, 'processando'))
        conn.commit()
        cur.close()
        conn.close()

        # enviar em background leve
        def background_send(jid):
            send_message_to_evolution(jid)

        threading.Thread(target=background_send, args=(job_id,), daemon=True).start()

        thumb_url = f"/uploads/thumbs/{os.path.basename(thumb_path)}" if thumb_path else None
        return jsonify({"job_id": job_id, "status": "processando", "thumbnail": thumb_url}), 200
    except Exception as e:
        print("Erro /api/send-media:", e)
        return jsonify({"error": f"Erro interno: {str(e)}"}), 500


@app.route('/api/schedule-media', methods=['POST'])
def api_schedule_media():
    """Agenda envio futuro"""
    if not check_apikey(request):
        return jsonify({"error": "Unauthorized"}), 401
    try:
        data = request.get_json()
        number = data.get('number')
        media_b64 = data.get('media')
        schedule_time_str = data.get('schedule_time')  # espera ISO like "2025-10-31T15:30:00" (UTC ou local)
        mediatype = data.get('mediatype', 'image')
        caption = data.get('caption', '')

        if not number or not media_b64 or not is_base64(media_b64) or not schedule_time_str:
            return jsonify({"error": "Dados inválidos"}), 400

        # parse ISO datetime; se sem timezone assume UTC
        try:
            schedule_time = datetime.fromisoformat(schedule_time_str)
            if schedule_time.tzinfo is None:
                schedule_time = schedule_time.replace(tzinfo=timezone.utc)
        except Exception:
            return jsonify({"error": "schedule_time inválido. Use ISO format (ex: 2025-10-31T15:30:00 or 2025-10-31T15:30:00+00:00)"}), 400

        # não permite agendamento no passado
        if schedule_time <= datetime.now(timezone.utc):
            return jsonify({"error": "schedule_time deve ser uma data/hora futura"}), 400

        ext = 'png' if mediatype == 'image' else ('mp4' if mediatype == 'video' else 'pdf')
        media_path = save_file(media_b64, ext)
        thumb_path = create_thumbnail(media_path, mediatype)
        job_id = str(uuid.uuid4())

        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO scheduled_messages (job_id, number, media_path, thumbnail_path, mediatype, caption, scheduled_at, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """, (job_id, number, media_path, thumb_path, mediatype, caption, schedule_time.isoformat(), 'agendado'))
        conn.commit()
        cur.close()
        conn.close()

        # adiciona job ao scheduler
        scheduler.add_job(send_message_to_evolution, 'date', run_date=schedule_time, args=[job_id], id=job_id)

        thumb_url = f"/uploads/thumbs/{os.path.basename(thumb_path)}" if thumb_path else None
        return jsonify({
            "status": "agendado",
            "job_id": job_id,
            "scheduled_at": schedule_time.isoformat(),
            "thumbnail": thumb_url
        }), 200
    except Exception as e:
        print("Erro /api/schedule-media:", e)
        return jsonify({"error": f"Erro interno: {str(e)}"}), 500


@app.route('/api/scheduled', methods=['GET'])
def api_get_scheduled():
    if not check_apikey(request):
        return jsonify({"error": "Unauthorized"}), 401
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM scheduled_messages ORDER BY scheduled_at DESC")
        rows = cur.fetchall()
        data = []
        for r in rows:
            thumb_url = None
            if r.get('thumbnail_path') and os.path.exists(r.get('thumbnail_path')):
                thumb_url = f"/uploads/thumbs/{os.path.basename(r.get('thumbnail_path'))}"
            data.append({
                "job_id": r.get('job_id'),
                "number": r.get('number'),
                "mediatype": r.get('mediatype'),
                "caption": r.get('caption'),
                "scheduled_at": r.get('scheduled_at'),
                "status": r.get('status'),
                "last_attempt": r.get('last_attempt'),
                "error": r.get('error'),
                "thumbnail": thumb_url
            })
        cur.close()
        conn.close()
        return jsonify(data), 200
    except Exception as e:
        print("Erro /api/scheduled:", e)
        return jsonify({"error": f"Erro interno: {str(e)}"}), 500


# ======================
# ROTAS DE FRONTEND E UPLOADS
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
        cur.close()
        conn.close()
        return jsonify({
            "status": "healthy",
            "database": "PostgreSQL",
            "evolution_url": EVOLUTION_URL
        }), 200
    except Exception as e:
        return jsonify({"status": "unhealthy", "error": str(e)}), 500


# ======================
# INICIALIZAÇÃO
# ======================
def initialize_app():
    print("🚀 Servidor Flask iniciado no Render.")
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM scheduled_messages")
        count = cur.fetchone().get("count", 0)
        print(f"📦 Banco conectado com sucesso — {count} mensagens agendadas.")
        cur.close()
        conn.close()
    except Exception as e:
        print(f"❌ Erro ao conectar ao banco: {e}")


initialize_app()

if __name__ == '__main__':
    port = int(os.getenv("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
