import os
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from datetime import datetime, timezone
import base64, requests, sqlite3, uuid
from contextlib import closing
from PIL import Image
from apscheduler.schedulers.background import BackgroundScheduler
import threading
import time

# ======================
# CONFIGURAÇÃO FLASK
# ======================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(__name__)
CORS(app)

# ======================
# CONFIGURAÇÕES GERAIS
# ======================
API_KEY = os.getenv("API_KEY", "fmFeKYVdcU06C3S57mmVZ4BhsEwdVIww")
INSTANCE_NAME = os.getenv("INSTANCE_NAME", "ICPA")
EVOLUTION_URL = os.getenv("EVOLUTION_URL", f"http://localhost:8081/message/sendMedia/{INSTANCE_NAME}")

# Banco de dados persistente (Render usa /data)
DATA_DIR = "/data" if os.path.exists("/data") else BASE_DIR
DATABASE = os.path.join(DATA_DIR, "whatsapp_scheduler.db")

UPLOAD_FOLDER = os.path.join(DATA_DIR, 'uploads')
THUMB_FOLDER = os.path.join(UPLOAD_FOLDER, 'thumbs')
THUMBNAIL_SIZE = (100, 100)

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(THUMB_FOLDER, exist_ok=True)

# ======================
# BANCO DE DADOS
# ======================
def init_db():
    with closing(sqlite3.connect(DATABASE)) as conn:
        with conn:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS scheduled_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL UNIQUE,
                    number TEXT NOT NULL,
                    media_path TEXT NOT NULL,
                    thumbnail_path TEXT,
                    mediatype TEXT NOT NULL,
                    caption TEXT,
                    scheduled_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    last_attempt TEXT,
                    error TEXT
                )
            ''')
init_db()

scheduler = BackgroundScheduler()
scheduler.start()

# ======================
# FUNÇÕES AUXILIARES
# ======================
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
        img.thumbnail(THUMBNAIL_SIZE)
        img.save(thumb_path)
        return thumb_path
    except Exception as e:
        print(f"Erro ao criar thumbnail: {e}")
        return None

# ======================
# FUNÇÃO DE ENVIO
# ======================
def send_message_to_evolution(job_id):
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM scheduled_messages WHERE job_id=?", (job_id,))
            row = cursor.fetchone()
            if not row:
                print(f"Job {job_id} não encontrado")
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
                cursor.execute("UPDATE scheduled_messages SET status=?, last_attempt=?, error=? WHERE job_id=?",
                               ("erro", datetime.now(timezone.utc).isoformat(), error_msg, job_id))
                conn.commit()
                return

            try:
                response = requests.post(EVOLUTION_URL, json=payload, headers={"apikey": API_KEY}, timeout=30)
                if response.status_code == 200:
                    cursor.execute("UPDATE scheduled_messages SET status=?, last_attempt=?, error=NULL WHERE job_id=?",
                                   ("enviado", datetime.now(timezone.utc).isoformat(), job_id))
                    conn.commit()
                    print(f"Mensagem {job_id} enviada com sucesso")
                else:
                    error_msg = f"Erro HTTP {response.status_code}: {response.text}"
                    cursor.execute("UPDATE scheduled_messages SET status=?, last_attempt=?, error=? WHERE job_id=?",
                                   ("erro", datetime.now(timezone.utc).isoformat(), error_msg, job_id))
                    conn.commit()
                    print(f"Erro ao enviar mensagem {job_id}: {error_msg}")
            except Exception as e:
                error_msg = f"Erro de conexão: {str(e)}"
                cursor.execute("UPDATE scheduled_messages SET status=?, last_attempt=?, error=? WHERE job_id=?",
                               ("erro", datetime.now(timezone.utc).isoformat(), error_msg, job_id))
                conn.commit()
                print(f"Erro de conexão para mensagem {job_id}: {error_msg}")
    except Exception as e:
        print(f"Erro crítico no processamento: {str(e)}")

# ======================
# RECARREGA AGENDAMENTOS
# ======================
def reload_pending_schedules():
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM scheduled_messages WHERE status='agendado'")
        rows = cursor.fetchall()
        for row in rows:
            job_id = row['job_id']
            scheduled_at = datetime.fromisoformat(row['scheduled_at'])
            if scheduled_at > datetime.now(timezone.utc):
                if not scheduler.get_job(job_id):
                    scheduler.add_job(send_message_to_evolution, 'date', run_date=scheduled_at, args=[job_id], id=job_id)
                    print(f"Agendamento recarregado: {job_id}")

reload_pending_schedules()

# ======================
# ROTAS FRONTEND
# ======================
@app.route('/')
def index():
    return send_from_directory(BASE_DIR, 'index.html')

@app.route('/calendar')
def calendar():
    return send_from_directory(BASE_DIR, 'calendar.html')

@app.route('/<path:filename>')
def serve_static_files(filename):
    # Serve CSS, JS, images, etc. da raiz
    return send_from_directory(BASE_DIR, filename)

@app.route('/uploads/<path:filename>')
def uploaded_file(filename):
    return send_from_directory(UPLOAD_FOLDER, filename)

@app.route('/uploads/thumbs/<path:filename>')
def uploaded_thumb(filename):
    return send_from_directory(THUMB_FOLDER, filename)

# ======================
# API ROUTES
# ======================
@app.route('/api/send-media', methods=['POST'])
def handle_send_media():
    client_key = request.headers.get('apikey')
    if client_key != API_KEY:
        return jsonify({"error": "Unauthorized"}), 401
    try:
        data = request.get_json()
        number = data.get('number')
        media_b64 = data.get('media')
        if not number or not media_b64 or not is_base64(media_b64):
            return jsonify({"error": "Número ou mídia inválidos"}), 400
        mediatype = data.get('mediatype', 'image')
        ext = 'png' if mediatype == 'image' else 'pdf'
        media_path = save_file(media_b64, ext)
        thumbnail_path = create_thumbnail(media_path, mediatype)
        job_id = str(uuid.uuid4())
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO scheduled_messages (job_id, number, media_path, thumbnail_path, mediatype, caption, scheduled_at, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (job_id, number, media_path, thumbnail_path, mediatype, data.get('caption',''),
                  datetime.now(timezone.utc).isoformat(), 'processando'))
            conn.commit()
        def background_send():
            time.sleep(1)
            send_message_to_evolution(job_id)
        threading.Thread(target=background_send, daemon=True).start()
        thumb_url = f"/uploads/thumbs/{os.path.basename(thumbnail_path)}" if thumbnail_path else None
        return jsonify({
            "job_id": job_id,
            "status": "processando",
            "message": "Mensagem em processamento.",
            "thumbnail": thumb_url
        }), 200
    except Exception as e:
        return jsonify({"error": f"Erro interno: {str(e)}"}), 500

@app.route('/api/schedule-media', methods=['POST'])
def schedule_media():
    client_key = request.headers.get('apikey')
    if client_key != API_KEY:
        return jsonify({"error": "Unauthorized"}), 401
    try:
        data = request.get_json()
        number = data.get('number')
        media_b64 = data.get('media')
        schedule_time = datetime.fromisoformat(data.get('schedule_time'))
        if not number or not media_b64 or not is_base64(media_b64) or not schedule_time:
            return jsonify({"error": "Dados inválidos"}), 400
        mediatype = data.get('mediatype','image')
        ext = 'png' if mediatype == 'image' else 'pdf'
        media_path = save_file(media_b64, ext)
        thumbnail_path = create_thumbnail(media_path, mediatype)
        job_id = str(uuid.uuid4())
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO scheduled_messages (job_id, number, media_path, thumbnail_path, mediatype, caption, scheduled_at, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (job_id, number, media_path, thumbnail_path, mediatype, data.get('caption',''),
                  schedule_time.isoformat(), 'agendado'))
            conn.commit()
        scheduler.add_job(send_message_to_evolution, 'date', run_date=schedule_time, args=[job_id], id=job_id)
        thumb_url = f"/uploads/thumbs/{os.path.basename(thumbnail_path)}" if thumbnail_path else None
        return jsonify({
            "status": "agendado", 
            "job_id": job_id, 
            "scheduled_at": schedule_time.isoformat(),
            "message": "Mensagem agendada com sucesso.",
            "thumbnail": thumb_url
        }), 200
    except Exception as e:
        return jsonify({"error": f"Erro interno: {str(e)}"}), 500

@app.route('/api/scheduled', methods=['GET'])
def get_scheduled_messages():
    client_key = request.headers.get('apikey')
    if client_key != API_KEY:
        return jsonify({"error": "Unauthorized"}), 401
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM scheduled_messages ORDER BY scheduled_at DESC")
            rows = cursor.fetchall()
            messages = []
            for row in rows:
                thumb_url = None
                if row['thumbnail_path'] and os.path.exists(row['thumbnail_path']):
                    thumb_url = f"/uploads/thumbs/{os.path.basename(row['thumbnail_path'])}"
                messages.append({
                    "job_id": row['job_id'],
                    "number": row['number'],
                    "mediatype": row['mediatype'],
                    "caption": row['caption'],
                    "scheduled_at": row['scheduled_at'],
                    "status": row['status'],
                    "last_attempt": row['last_attempt'],
                    "error": row['error'],
                    "thumbnail": thumb_url
                })
        return jsonify(messages), 200
    except Exception as e:
        return jsonify({"error": f"Erro ao buscar mensagens: {str(e)}"}), 500

@app.route('/api/health', methods=['GET'])
def health_check():
    try:
        with get_db() as conn:
            conn.execute("SELECT 1")
        return jsonify({
            "status": "healthy",
            "database": "sqlite",
            "frontend": {
                "index_exists": os.path.exists(os.path.join(BASE_DIR, 'index.html')),
                "calendar_exists": os.path.exists(os.path.join(BASE_DIR, 'calendar.html'))
            },
            "environment": "render",
            "timestamp": datetime.now(timezone.utc).isoformat()
        }), 200
    except Exception as e:
        return jsonify({"status": "unhealthy", "error": str(e)}), 500

# ======================
# INICIALIZAÇÃO
# ======================
def initialize_app():
    print("Iniciando servidor Flask no Render...")
    print(f"Diretório base: {BASE_DIR}")
    print(f"Data dir: {DATA_DIR}")
    print(f"Arquivos na raiz: {[f for f in os.listdir(BASE_DIR) if f.endswith(('.html', '.css', '.js'))]}")
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM scheduled_messages")
            count = cursor.fetchone()[0]
            print(f"Banco de dados OK. {count} mensagens agendadas.")
    except Exception as e:
        print(f"Erro ao conectar banco: {e}")

initialize_app()

if __name__ == '__main__':
    port = int(os.getenv("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
