import time
import requests
import random
import string
import re
import sqlite3
from flask import Flask, request, jsonify, Response
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

session = requests.Session()

# ====== SQLITE DATABASE SETUP ======
conn = sqlite3.connect("stats.db", check_same_thread=False)
c = conn.cursor()

# Create stats table
c.execute('''
CREATE TABLE IF NOT EXISTS stats (
    key TEXT PRIMARY KEY,
    value INTEGER
)
''')
for key in ["requests", "downloads", "cache_hits", "videos_served"]:
    c.execute("INSERT OR IGNORE INTO stats (key, value) VALUES (?, ?)", (key, 0))
conn.commit()

# Table for unique IPs
c.execute('''
CREATE TABLE IF NOT EXISTS unique_ips (
    ip TEXT PRIMARY KEY
)
''')

# Table for download logs
c.execute('''
CREATE TABLE IF NOT EXISTS download_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ip TEXT,
    url TEXT,
    timestamp INTEGER
)
''')
conn.commit()

# ====== CACHE ======
cache = {}  # url -> video_url

# ====== FILENAME CLEANING ======
def clean_filename(text):
    text = re.sub(r'[\\/*?:"<>|]', "", text)
    text = re.sub(r'\s+', " ", text).strip()
    return text[:120]

# ====== RANDOM STRING ======
def random_string(length=6):
    return ''.join(random.choices(string.ascii_letters + string.digits, k=length))

# ====== FETCH TIKTOK VIDEO FUNCTION ======
def fetch_tiktok_video(url):
    try:
        res = session.post(
            "https://www.tikwm.com/api/",
            json={"url": url},
            headers={
                "User-Agent": "Mozilla/5.0",
                "Accept": "application/json",
                "Content-Type": "application/json"
            },
            timeout=30
        )
        if res.status_code != 200:
            return None
        data = res.json().get("data", {})
        return data.get("play")
    except:
        return None

# ====== DOWNLOAD ROUTE ======
@app.route("/download", methods=["POST"])
def download_video():
    data = request.get_json()
    url = data.get("url")
    ip = request.remote_addr

    if not url:
        return jsonify({"success": False, "message": "No URL"}), 400

    # increment requests
    c.execute("UPDATE stats SET value = value + 1 WHERE key = 'requests'")

    # add unique IP
    c.execute("INSERT OR IGNORE INTO unique_ips (ip) VALUES (?)", (ip,))
    conn.commit()

    # ===== CACHE HIT =====
    if url in cache:
        c.execute("UPDATE stats SET value = value + 1 WHERE key = 'cache_hits'")
        c.execute("UPDATE stats SET value = value + 1 WHERE key = 'downloads'")
        c.execute("UPDATE stats SET value = value + 1 WHERE key = 'videos_served'")
        conn.commit()

        c.execute(
            "INSERT INTO download_logs (ip, url, timestamp) VALUES (?, ?, ?)",
            (ip, url, int(time.time()))
        )
        conn.commit()

        filename = clean_filename("TikTok Video") + f"_{random_string()}.mp4"
        return jsonify({"success": True, "url": cache[url], "filename": filename})

    # ===== FETCH FROM TIKWM =====
    video_url = fetch_tiktok_video(url)
    if not video_url:
        return jsonify({"success": False, "message": "TikWM API failed"}), 500

    # cache it
    cache[url] = video_url

    # increment stats
    c.execute("UPDATE stats SET value = value + 1 WHERE key = 'downloads'")
    c.execute("UPDATE stats SET value = value + 1 WHERE key = 'videos_served'")

    # log download
    c.execute(
        "INSERT INTO download_logs (ip, url, timestamp) VALUES (?, ?, ?)",
        (ip, url, int(time.time()))
    )
    conn.commit()

    # generate filename
    filename = clean_filename("TikTok Video") + f"_{random_string()}.mp4"
    return jsonify({"success": True, "url": video_url, "filename": filename})

# ====== FILE SERVING ======
@app.route("/file")
def serve_file():
    video_url = request.args.get("url")
    if not video_url:
        return jsonify({"success": False, "message": "No video URL"}), 400

    try:
        r = session.get(video_url, stream=True, timeout=60)

        rand = random_string()
        filename = f"ToolifyX Downloader-{rand}.mp4"

        file_size = r.headers.get("Content-Length")

        headers = {
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Type": "video/mp4"
        }

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

# ====== ADMIN RESET ======
ADMIN_PASSWORD = "razzyadminX567"

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