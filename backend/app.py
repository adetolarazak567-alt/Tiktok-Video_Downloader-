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
# Initialize stats if not exists
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

# ====== CACHE STORAGE ======
cache = {}  # url -> video_url (still in-memory for speed)

# ====== FILENAME CLEANING ======
def clean_filename(text):
    text = re.sub(r'[\\/*?:"<>|]', "", text)  # remove invalid characters
    text = re.sub(r'\s+', " ", text).strip()
    return text[:120]  # limit length

# ====== RANDOM STRING ======
def random_string(length=6):
    return ''.join(random.choices(string.ascii_letters + string.digits, k=length))

# ====== DOWNLOAD API ======
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

        # log download
        c.execute(
            "INSERT INTO download_logs (ip, url, timestamp) VALUES (?, ?, ?)",
            (ip, url, int(time.time()))
        )
        conn.commit()

        return jsonify({"success": True, "url": cache[url]})

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
            return jsonify({"success": False, "message": "API error"}), 500

        result = res.json()

        if result.get("data") and result["data"].get("play"):
            video_url = result["data"]["play"]

            # store in cache
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

            return jsonify({"success": True, "url": video_url})

        return jsonify({"success": False, "message": "Invalid response"}), 500

    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

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

        return Response(
            r.iter_content(chunk_size=8192),
            headers=headers
        )

    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

# ====== STATS ROUTE ======
@app.route("/stats", methods=["GET"])
def get_stats():
    # fetch stats
    c.execute("SELECT key, value FROM stats")
    stats_data = dict(c.fetchall())

    # unique IPs
    c.execute("SELECT COUNT(*) FROM unique_ips")
    unique_ips_count = c.fetchone()[0]

    # download logs
    c.execute("SELECT ip, url, timestamp FROM download_logs")
    logs = [{"ip": ip, "url": url, "timestamp": ts} for ip, url, ts in c.fetchall()]

    return jsonify({
        **stats_data,
        "unique_ips": unique_ips_count,
        "download_logs": logs
    })

# ====== ADMIN RESET ======
ADMIN_PASSWORD = "razzyadminX567"  # same password as your dashboard JS prompt

@app.route("/admin/reset", methods=["POST"])
def reset_stats():
    data = request.get_json()
    password = data.get("password")

    if password != ADMIN_PASSWORD:
        return jsonify({"success": False, "message": "Wrong password"}), 401

    # reset stats
    for key in ["requests", "downloads", "cache_hits", "videos_served"]:
        c.execute("UPDATE stats SET value = 0 WHERE key = ?", (key,))

    # clear unique IPs
    c.execute("DELETE FROM unique_ips")

    # clear logs
    c.execute("DELETE FROM download_logs")

    conn.commit()

    return jsonify({"success": True})

# ====== START SERVER ======
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, threaded=True)