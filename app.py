import os
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from datetime import datetime, timezone
import base64, requests, sqlite3, os, uuid
from contextlib import closing
from PIL import Image
from apscheduler.schedulers.background import BackgroundScheduler
import fitz  # PyMuPDF
import threading
import time
import psycopg2
from psycopg2.extras import RealDictCursor
import urllib.parse

# ======================
# CONFIGURAÇÃO FLASK
# ======================
app = Flask(__name__, static_folder='main', static_url_path='')

# Configurações para Render
API_KEY = os.getenv("API_KEY", "fmFeKYVdcU06C3S57mmVZ4BhsEwdVIww")
INSTANCE_NAME = os.getenv("INSTANCE_NAME", "ICPA")
EVOLUTION_URL = os.getenv("EVOLUTION_URL", f"http://localhost:8081/message/sendMedia/{INSTANCE_NAME}")

CORS(app)

# ======================
# CONFIGURAÇÕES DE BANCO DE DADOS
# ======================
def get_database_config():
    database_url = os.getenv('DATABASE_URL')
    
    if database_url and database_url.startswith('postgres://'):
        database_url = database_url.replace('postgres://', 'postgresql://', 1)
    
    if database_url and database_url.startswith('postgresql://'):
        # Usar PostgreSQL (Render)
        return {
            'type': 'postgresql',
            'url': database_url
        }
    else:
        # Usar SQLite (local)
        return {
            'type': 'sqlite',
            'url': os.path.join(os.path.dirname(os.path.abspath(__file__)), 'whatsapp_scheduler.db')
        }

DB_CONFIG = get_database_config()

# ======================
# CONFIGURAÇÕES DE ARQUIVOS
# ======================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_FOLDER = os.path.join(BASE_DIR, 'uploads')
THUMB_FOLDER = os.path.join(UPLOAD_FOLDER, 'thumbs')
THUMBNAIL_SIZE = (100, 100)
DEFAULT_DOC_ICON = "/default_doc.png"

# Criar diretórios
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(THUMB_FOLDER, exist_ok=True)

# ======================
# BANCO DE DADOS
# ======================
def get_db_connection():
    if DB_CONFIG['type'] == 'postgresql':
        # PostgreSQL
        conn = psycopg2.connect(DB_CONFIG['url'], sslmode='require')
        conn.cursor_factory = RealDictCursor
        return conn
    else:
        # SQLite
        conn = sqlite3.connect(DB_CONFIG['url'])
        conn.row_factory = sqlite3.Row
        return conn

def init_db():
    with closing(get_db_connection()) as conn:
        with conn:
            cursor = conn.cursor()
            
            if DB_CONFIG['type'] == 'postgresql':
                # PostgreSQL
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS scheduled_messages (
                        id SERIAL PRIMARY KEY,
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
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS logs (
                        id SERIAL PRIMARY KEY,
                        job_id TEXT,
                        scheduled_at TEXT,
                        sent_at TEXT NOT NULL,
                        status TEXT NOT NULL,
                        caption TEXT,
                        destination TEXT NOT NULL,
                        mediatype TEXT,
                        thumbnail_path TEXT,
                        response TEXT NOT NULL,
                        error TEXT
                    )
                ''')
            else:
                # SQLite
                cursor.execute('''
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
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS logs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        job_id TEXT,
                        scheduled_at TEXT,
                        sent_at TEXT NOT NULL,
                        status TEXT NOT NULL,
                        caption TEXT,
                        destination TEXT NOT NULL,
                        mediatype TEXT,
                        thumbnail_path TEXT,
                        response TEXT NOT NULL,
                        error TEXT
                    )
                ''')
            conn.commit()

init_db()
scheduler = BackgroundScheduler()
scheduler.start()

# ======================
# FUNÇÕES AUXILIARES
# ======================
def get_db():
    return get_db_connection()

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
        thumb_path = os.path.join(THUMB_FOLDER, os.path.basename(filepath) + ".png")
        
        if mediatype == 'image':
            img = Image.open(filepath)
            img.thumbnail(THUMBNAIL_SIZE)
            img.save(thumb_path)
            
        elif mediatype == 'document' and filepath.endswith('.pdf'):
            doc = fitz.open(filepath)
            page = doc[0]
            pix = page.get_pixmap(matrix=fitz.Matrix(2,2))
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            img.thumbnail(THUMBNAIL_SIZE)
            img.save(thumb_path)
            
        else:
            # Para vídeo e outros tipos, usa ícone padrão
            return DEFAULT_DOC_ICON
            
        return thumb_path
    except Exception as e:
        print(f"Erro ao criar thumbnail: {e}")
        return DEFAULT_DOC_ICON

# ======================
# FUNÇÃO DE ENVIO
# ======================
def send_message_to_evolution(job_id):
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM scheduled_messages WHERE job_id=%s", (job_id,))
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
                cursor.execute("UPDATE scheduled_messages SET status=%s, last_attempt=%s, error=%s WHERE job_id=%s",
                           ("erro", datetime.now(timezone.utc).isoformat(), error_msg, job_id))
                conn.commit()
                return

            try:
                response = requests.post(EVOLUTION_URL, json=payload, headers={"apikey": API_KEY}, timeout=30)
                
                if response.status_code == 200:
                    cursor.execute("UPDATE scheduled_messages SET status=%s, last_attempt=%s, error=NULL WHERE job_id=%s",
                               ("enviado", datetime.now(timezone.utc).isoformat(), job_id))
                    conn.commit()
                    print(f"Mensagem {job_id} enviada com sucesso")
                else:
                    error_msg = f"Erro HTTP {response.status_code}: {response.text}"
                    cursor.execute("UPDATE scheduled_messages SET status=%s, last_attempt=%s, error=%s WHERE job_id=%s",
                               ("erro", datetime.now(timezone.utc).isoformat(), error_msg, job_id))
                    conn.commit()
                    print(f"Erro ao enviar mensagem {job_id}: {error_msg}")

            except Exception as e:
                error_msg = f"Erro de conexão: {str(e)}"
                cursor.execute("UPDATE scheduled_messages SET status=%s, last_attempt=%s, error=%s WHERE job_id=%s",
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

# ======================
# ROTAS PRINCIPAIS
# ======================
@app.route('/')
def index():
    try:
        return send_from_directory(app.static_folder, 'index.html')
    except Exception as e:
        return f"""
        <html>
            <head><title>ICPA WhatsApp Scheduler</title></head>
            <body>
                <h1>ICPA WhatsApp Scheduler</h1>
                <p>API está funcionando!</p>
                <p>Database: {DB_CONFIG['type']}</p>
                <p><a href="/api/health">Health Check</a></p>
                <p><a href="/api/scheduled">Scheduled Messages</a></p>
            </body>
        </html>
        """

@app.route('/<path:filename>')
def serve_static(filename):
    return send_from_directory(app.static_folder, filename)

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
        ext = mediatype if mediatype != 'document' else 'pdf'
        media_path = save_file(media_b64, ext)
        thumbnail_path = create_thumbnail(media_path, mediatype)
        job_id = str(uuid.uuid4())

        with get_db() as conn:
            cursor = conn.cursor()
            if DB_CONFIG['type'] == 'postgresql':
                cursor.execute('''
                    INSERT INTO scheduled_messages (
                        job_id, number, media_path, thumbnail_path, mediatype, caption,
                        scheduled_at, status
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ''', (job_id, number, media_path, thumbnail_path, mediatype, data.get('caption',''),
                      datetime.now(timezone.utc).isoformat(), 'processando'))
            else:
                cursor.execute('''
                    INSERT INTO scheduled_messages (
                        job_id, number, media_path, thumbnail_path, mediatype, caption,
                        scheduled_at, status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ''', (job_id, number, media_path, thumbnail_path, mediatype, data.get('caption',''),
                      datetime.now(timezone.utc).isoformat(), 'processando'))
            conn.commit()

        def background_send():
            time.sleep(1)
            send_message_to_evolution(job_id)
        
        thread = threading.Thread(target=background_send)
        thread.daemon = True
        thread.start()

        return jsonify({
            "job_id": job_id,
            "status": "processando",
            "message": "Mensagem em processamento.",
            "thumbnail": thumbnail_path.replace(UPLOAD_FOLDER, '').lstrip('/')
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
        ext = mediatype if mediatype != 'document' else 'pdf'
        media_path = save_file(media_b64, ext)
        thumbnail_path = create_thumbnail(media_path, mediatype)
        job_id = str(uuid.uuid4())

        with get_db() as conn:
            cursor = conn.cursor()
            if DB_CONFIG['type'] == 'postgresql':
                cursor.execute('''
                    INSERT INTO scheduled_messages (
                        job_id, number, media_path, thumbnail_path, mediatype, caption,
                        scheduled_at, status
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ''', (job_id, number, media_path, thumbnail_path, mediatype, data.get('caption',''),
                      schedule_time.isoformat(), 'agendado'))
            else:
                cursor.execute('''
                    INSERT INTO scheduled_messages (
                        job_id, number, media_path, thumbnail_path, mediatype, caption,
                        scheduled_at, status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ''', (job_id, number, media_path, thumbnail_path, mediatype, data.get('caption',''),
                      schedule_time.isoformat(), 'agendado'))
            conn.commit()

        scheduler.add_job(send_message_to_evolution, 'date', run_date=schedule_time, args=[job_id], id=job_id)

        return jsonify({
            "status": "agendado", 
            "job_id": job_id, 
            "scheduled_at": schedule_time.isoformat(),
            "message": "Mensagem agendada com sucesso."
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
            if DB_CONFIG['type'] == 'postgresql':
                cursor.execute("SELECT * FROM scheduled_messages ORDER BY scheduled_at DESC")
            else:
                cursor.execute("SELECT * FROM scheduled_messages ORDER BY scheduled_at DESC")
            
            rows = cursor.fetchall()
            messages = []
            
            for row in rows:
                thumb_url = DEFAULT_DOC_ICON
                if row['thumbnail_path']:
                    thumb_filename = os.path.basename(row['thumbnail_path'])
                    if os.path.exists(row['thumbnail_path']):
                        thumb_url = f"/uploads/thumbs/{thumb_filename}"
                
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

@app.route('/api/status/<job_id>', methods=['GET'])
def get_message_status(job_id):
    client_key = request.headers.get('apikey')
    if client_key != API_KEY:
        return jsonify({"error": "Unauthorized"}), 401
    
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            if DB_CONFIG['type'] == 'postgresql':
                cursor.execute("SELECT * FROM scheduled_messages WHERE job_id=%s", (job_id,))
            else:
                cursor.execute("SELECT * FROM scheduled_messages WHERE job_id=?", (job_id,))
            
            row = cursor.fetchone()
            
            if row:
                return jsonify({
                    "job_id": job_id,
                    "status": row['status'],
                    "error": row['error'],
                    "last_attempt": row['last_attempt'],
                    "number": row['number'],
                    "mediatype": row['mediatype'],
                    "caption": row['caption'],
                    "scheduled_at": row['scheduled_at']
                }), 200
            else:
                return jsonify({"error": "Mensagem não encontrada"}), 404
                
    except Exception as e:
        return jsonify({"error": f"Erro ao buscar status: {str(e)}"}), 500

@app.route('/api/health', methods=['GET'])
def health_check():
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            
        # Verifica se a pasta main existe
        main_exists = os.path.exists(app.static_folder)
        index_exists = os.path.exists(os.path.join(app.static_folder, 'index.html')) if main_exists else False
        
        return jsonify({
            "status": "healthy", 
            "database": DB_CONFIG['type'],
            "main_folder_exists": main_exists,
            "index_html_exists": index_exists,
            "main_folder_path": app.static_folder,
            "environment": "production",
            "timestamp": datetime.now(timezone.utc).isoformat()
        }), 200
    except Exception as e:
        return jsonify({
            "status": "unhealthy", 
            "database": DB_CONFIG['type'],
            "error": str(e)
        }), 500

# ======================
# INICIALIZAÇÃO
# ======================
def initialize_app():
    print("Iniciando servidor Flask...")
    print(f"Diretório atual: {os.getcwd()}")
    print(f"Main folder: {app.static_folder}")
    print(f"Database type: {DB_CONFIG['type']}")
    
    # Listar arquivos no diretório atual
    print("Arquivos no diretório raiz:")
    for file in os.listdir('.'):
        print(f"  - {file}")
    
    # Listar arquivos na pasta main se existir
    if os.path.exists(app.static_folder):
        print("Arquivos na pasta main:")
        for file in os.listdir(app.static_folder):
            print(f"  - {file}")
    else:
        print("Pasta main não encontrada!")
    
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) as count FROM scheduled_messages")
            result = cursor.fetchone()
            count = result['count'] if DB_CONFIG['type'] == 'postgresql' else result[0]
            print(f"Banco de dados OK. {count} mensagens agendadas.")
    except Exception as e:
        print(f"Erro no banco de dados: {e}")
    
    reload_pending_schedules()
    print("Aplicação inicializada.")

initialize_app()

if __name__ == '__main__':
    port = int(os.getenv("PORT", 5000))
    debug = os.getenv("DEBUG", "false").lower() == "true"
    print(f"Iniciando servidor na porta {port}...")
    app.run(host='0.0.0.0', port=port, debug=debug)
