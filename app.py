import os
import threading
import time
import sqlite3
import requests
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from datetime import datetime
from apscheduler.schedulers.background import BackgroundScheduler
from werkzeug.utils import secure_filename
from PIL import Image

# Configuração principal
app = Flask(__name__, static_folder='main', static_url_path='/')
CORS(app)

UPLOAD_FOLDER = 'uploads'
THUMB_FOLDER = os.path.join(UPLOAD_FOLDER, 'thumbs')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(THUMB_FOLDER, exist_ok=True)

DATABASE = 'schedule.db'
EVOLUTION_URL = "http://localhost:8080"  # Evolution API interna
INSTANCE_KEY = "evolution_instance"

# ---------------------- Banco ----------------------
def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS schedules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                phone TEXT NOT NULL,
                message TEXT,
                media_path TEXT,
                schedule_time TEXT NOT NULL,
                status TEXT DEFAULT 'pending'
            )
        """)
init_db()

# ---------------------- Miniatura ----------------------
def create_thumbnail(image_path):
    try:
        thumb_path = os.path.join(THUMB_FOLDER, os.path.basename(image_path))
        with Image.open(image_path) as img:
            img.thumbnail((200, 200))
            img.save(thumb_path)
        return thumb_path
    except Exception as e:
        print("Erro ao criar miniatura:", e)
        return None

# ---------------------- Scheduler ----------------------
scheduler = BackgroundScheduler()
scheduler.start()

def send_scheduled_message(schedule_id):
    with get_db() as conn:
        schedule = conn.execute("SELECT * FROM schedules WHERE id=?", (schedule_id,)).fetchone()
        if not schedule or schedule["status"] != "pending":
            return

        data = {
            "number": schedule["phone"],
            "text": schedule["message"]
        }

        if schedule["media_path"]:
            with open(schedule["media_path"], "rb") as f:
                files = {"file": f}
                response = requests.post(f"{EVOLUTION_URL}/message/sendMedia/{INSTANCE_KEY}", files=files, data={"number": schedule["phone"], "caption": schedule["message"]})
        else:
            response = requests.post(f"{EVOLUTION_URL}/message/sendText/{INSTANCE_KEY}", json=data)

        status = "sent" if response.ok else "failed"
        conn.execute("UPDATE schedules SET status=? WHERE id=?", (status, schedule_id))
        conn.commit()
        print(f"Mensagem {status} (ID {schedule_id})")

def load_pending_jobs():
    with get_db() as conn:
        rows = conn.execute("SELECT id, schedule_time FROM schedules WHERE status='pending'").fetchall()
        for row in rows:
            try:
                run_time = datetime.strptime(row["schedule_time"], "%Y-%m-%d %H:%M:%S")
                if run_time > datetime.now():
                    scheduler.add_job(send_scheduled_message, 'date', run_date=run_time, args=[row["id"]])
            except Exception as e:
                print("Erro ao reativar job:", e)

threading.Thread(target=load_pending_jobs).start()

# ---------------------- Rotas ----------------------
@app.route("/")
def index():
    return app.send_static_file("index.html")

@app.route("/calendar")
def calendar():
    return app.send_static_file("calendar.html")

@app.route("/api/schedule-media", methods=["POST"])
def schedule_media():
    phone = request.form.get("phone")
    message = request.form.get("message", "")
    schedule_time = request.form.get("schedule_time")
    file = request.files.get("file")

    if not all([phone, schedule_time, file]):
        return jsonify({"error": "Campos obrigatórios ausentes"}), 400

    filename = secure_filename(file.filename)
    file_path = os.path.join(UPLOAD_FOLDER, filename)
    file.save(file_path)
    create_thumbnail(file_path)

    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("INSERT INTO schedules (phone, message, media_path, schedule_time) VALUES (?, ?, ?, ?)",
                    (phone, message, file_path, schedule_time))
        conn.commit()
        schedule_id = cur.lastrowid

    run_time = datetime.strptime(schedule_time, "%Y-%m-%d %H:%M:%S")
    scheduler.add_job(send_scheduled_message, 'date', run_date=run_time, args=[schedule_id])
    return jsonify({"status": "scheduled", "id": schedule_id})

@app.route("/api/scheduled")
def list_scheduled():
    with get_db() as conn:
        schedules = conn.execute("SELECT * FROM schedules ORDER BY schedule_time DESC").fetchall()
        return jsonify([dict(row) for row in schedules])

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
