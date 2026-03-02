import threading
import time
import requests
import random
import string
import re
import sqlite3
from dotenv import load_dotenv
import os

load_dotenv()
from flask import Flask, request, jsonify, Response
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

# ====== SESSION SETUP ======
session = requests.Session()
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

session.headers.update({"User-Agent": "Mozilla/5.0"})

retry = Retry(
    total=5,
    backoff_factor=0.5,
    status_forcelist=[429, 500, 502, 503, 504]
)
adapter = HTTPAdapter(max_retries=retry, pool_connections=500, pool_maxsize=500)
session.mount("http://", adapter)
session.mount("https://", adapter)

# ====== SQLITE DATABASE SETUP ======
conn = sqlite3.connect("stats.db", check_same_thread=False)
c = conn.cursor()

# Stats table
c.execute('''
CREATE TABLE IF NOT EXISTS stats (
    key TEXT PRIMARY KEY,
    value INTEGER
)
''')
for key in ["requests", "downloads", "cache_hits", "videos_served"]:
    c.execute("INSERT OR IGNORE INTO stats (key, value) VALUES (?, ?)", (key, 0))
conn.commit()

# Unique IPs
c.execute('''
CREATE TABLE IF NOT EXISTS unique_ips (
    ip TEXT PRIMARY KEY
)
''')

# Video cache
c.execute('''
CREATE TABLE IF NOT EXISTS video_cache (
    url TEXT PRIMARY KEY,
    video_url TEXT
)
''')
conn.commit()

# Download logs
c.execute('''
CREATE TABLE IF NOT EXISTS download_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ip TEXT,
    url TEXT,
    timestamp INTEGER
)
''')
conn.commit()

# ====== RAM CACHE ======
cache = {}  # url -> video_url

# ====== HELPERS ======
def clean_filename(text):
    text = re.sub(r'[\\/*?:"<>|]', "", text)
    text = re.sub(r'\s+', " ", text).strip()
    return text[:120]

def random_string(length=6):
    return ''.join(random.choices(string.ascii_letters + string.digits, k=length))

# ====== BACKUP FETCH (ssstik.io) ======
def fetch_tiktok_backup(url):
    try:
        res = session.post(
            "https://api2.musicaldown.com/v2/download",
            data={"url": url},
            timeout=10
        )
        if res.status_code == 200:
            data = res.json()
            video_url = data.get("video", {}).get("no_watermark")
            if video_url:
                return video_url
    except Exception as e:
        print("Backup fetch failed:", e)
    return None

# ====== MAIN FETCH ======
def fetch_tiktok_video(url):
    # Primary TikWM
    try:
        res = session.post("https://www.tikwm.com/api/", data={"url": url, "hd": "1"}, timeout=10)
        if res.status_code == 200:
            video = res.json().get("data", {}).get("play")
            if video:
                return video
    except Exception as e:
        print("Primary TikWM failed:", e)

    # Secondary TikWM
    try:
        res = session.post("https://tikwm.com/api/", data={"url": url}, timeout=10)
        if res.status_code == 200:
            video = res.json().get("data", {}).get("play")
            if video:
                return video
    except Exception as e:
        print("Secondary TikWM failed:", e)

    # Backup ssstik.io
    return fetch_tiktok_backup(url)

# ====== SAVE CACHE TO DB THREAD-SAFE ======
def save_cache_db(url, video_url):
    try:
        conn2 = sqlite3.connect("stats.db")
        c2 = conn2.cursor()
        c2.execute(
            "INSERT OR IGNORE INTO video_cache (url, video_url) VALUES (?, ?)",
            (url, video_url)
        )
        conn2.commit()
        conn2.close()
    except Exception as e:
        print("DB thread error:", e)

# ====== DOWNLOAD ROUTE ======
@app.route("/download", methods=["POST"])
def download_video():
    try:
        data = request.get_json()
        url = data.get("url")
        ip = request.remote_addr

        if not url:
            return jsonify({"success": False, "message": "No URL"}), 400

        # Increment requests
        try:
            c.execute("UPDATE stats SET value = value + 1 WHERE key='requests'")
            c.execute("INSERT OR IGNORE INTO unique_ips (ip) VALUES (?)", (ip,))
            conn.commit()
        except:
            pass

        # RAM cache
        if url in cache:
            filename = clean_filename("ToolifyX Downloader") + "_" + random_string() + ".mp4"
            return jsonify({"success": True, "url": cache[url], "filename": filename})

        # DB cache
        c.execute("SELECT video_url FROM video_cache WHERE url=?", (url,))
        row = c.fetchone()
        if row:
            video_url = row[0]
            cache[url] = video_url
            filename = clean_filename("ToolifyX Downloader") + "_" + random_string() + ".mp4"
            return jsonify({"success": True, "url": video_url, "filename": filename})

        # Fetch video
        video_url = fetch_tiktok_video(url)
        print("FETCH RESULT:", video_url)
        if not video_url:
            return jsonify({"success": False, "message": "Fetch failed, try again"}), 500

        # Save RAM cache
        cache[url] = video_url

        # Save DB in background
        threading.Thread(target=save_cache_db, args=(url, video_url), daemon=True).start()

        # Update stats & logs
        try:
            c.execute("UPDATE stats SET value=value+1 WHERE key='downloads'")
            c.execute("UPDATE stats SET value=value+1 WHERE key='videos_served'")
            c.execute("INSERT INTO download_logs (ip, url, timestamp) VALUES (?, ?, ?)",
                      (ip, url, int(time.time())))
            conn.commit()
        except:
            pass

        filename = clean_filename("ToolifyX Downloader") + "_" + random_string() + ".mp4"
        return jsonify({"success": True, "url": video_url, "filename": filename})

    except Exception as e:
        print("CRASH PREVENTED:", e)
        return jsonify({"success": False, "message": "Server recovered automatically"}), 500

# ====== FILE SERVING ======
@app.route("/file")
def serve_file():
    video_url = request.args.get("url")
    mode = request.args.get("mode", "preview")
    if not video_url:
        return jsonify({"success": False, "message": "No video URL"}), 400
    try:
        r = session.get(video_url, stream=True, timeout=10)
        rand = random_string()
        filename = f"ToolifyX Downloader-{rand}.mp4"
        file_size = r.headers.get("Content-Length")
        disposition = f'attachment; filename="{filename}"' if mode=="download" else f'inline; filename="{filename}"'
        headers = {"Content-Disposition": disposition, "Content-Type": "video/mp4"}
        if file_size:
            headers["Content-Length"] = file_size
        return Response(r.iter_content(chunk_size=8192), headers=headers)
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

# ====== STATS ======
@app.route("/stats", methods=["GET"])
def get_stats():
    c.execute("SELECT key, value FROM stats")
    stats_data = dict(c.fetchall())
    c.execute("SELECT COUNT(*) FROM unique_ips")
    unique_ips_count = c.fetchone()[0]
    c.execute("SELECT ip, url, timestamp FROM download_logs")
    logs = [{"ip": ip, "url": url, "timestamp": ts} for ip, url, ts in c.fetchall()]
    return jsonify({**stats_data, "unique_ips": unique_ips_count, "download_logs": logs})

# ====== WAKE ======
@app.route("/wake", methods=["GET"])
def wake():
    return jsonify({"success": True, "message": "Server is awake"})

# ====== ADMIN RESET ======
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")
@app.route("/admin/reset", methods=["POST"])
def reset_stats():
    data = request.get_json()
    password = data.get("password")
    if password != ADMIN_PASSWORD:
        return jsonify({"success": False, "message": "Wrong password"}), 401
    for key in ["requests", "downloads", "cache_hits", "videos_served"]:
        c.execute("UPDATE stats SET value = 0 WHERE key = ?", (key,))
    c.execute("DELETE FROM unique_ips")
    c.execute("DELETE FROM download_logs")
    conn.commit()
    return jsonify({"success": True})

# ====== START SERVER ======
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, threaded=True)