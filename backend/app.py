import time
import requests
import re
import sqlite3
from flask import Flask, request, jsonify, Response
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

session = requests.Session()

# ===== SQLITE SETUP =====
conn = sqlite3.connect("toolifyx.db", check_same_thread=False)
cursor = conn.cursor()

# Create tables if not exist
cursor.execute("""
CREATE TABLE IF NOT EXISTS stats (
    id INTEGER PRIMARY KEY,
    requests INTEGER DEFAULT 0,
    downloads INTEGER DEFAULT 0,
    cache_hits INTEGER DEFAULT 0,
    videos_served INTEGER DEFAULT 0
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ip TEXT,
    url TEXT,
    timestamp INTEGER
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS cache (
    url TEXT PRIMARY KEY,
    video_url TEXT,
    title TEXT
)
""")

# Ensure stats row exists
cursor.execute("SELECT id FROM stats WHERE id=1")
if not cursor.fetchone():
    cursor.execute("""
    INSERT INTO stats (id, requests, downloads, cache_hits, videos_served)
    VALUES (1,0,0,0,0)
    """)
    conn.commit()


# ===== CLEAN FILENAME =====
def clean_filename(text):
    text = re.sub(r'[\\/*?:"<>|]', "", text)
    text = re.sub(r'\s+', " ", text).strip()
    return text[:120]


# ===== UPDATE STAT FUNCTION =====
def increment_stat(field):
    cursor.execute(f"UPDATE stats SET {field} = {field} + 1 WHERE id=1")
    conn.commit()


# ===== DOWNLOAD API =====
@app.route("/download", methods=["POST"])
def download_video():

    increment_stat("requests")

    data = request.get_json()
    url = data.get("url")
    ip = request.remote_addr

    if not url:
        return jsonify({"success": False, "message": "No URL"}), 400

    # ===== CACHE CHECK (SQLITE) =====
    cursor.execute("SELECT video_url, title FROM cache WHERE url=?", (url,))
    cached = cursor.fetchone()

    if cached:

        increment_stat("cache_hits")
        increment_stat("downloads")
        increment_stat("videos_served")

        cursor.execute(
            "INSERT INTO logs (ip, url, timestamp) VALUES (?,?,?)",
            (ip, url, int(time.time()))
        )
        conn.commit()

        return jsonify({
            "success": True,
            "url": cached[0],
            "title": cached[1]
        })

    # ===== FETCH FROM TIKWM =====
    try:

        res = session.post(
            "https://www.tikwm.com/api/",
            json={"url": url},
            headers={
                "User-Agent": "Mozilla/5.0",
                "Content-Type": "application/json"
            },
            timeout=30
        )

        if res.status_code != 200:
            return jsonify({"success": False, "message": "API error"}), 500

        result = res.json()

        if result.get("data"):

            video_url = result["data"]["play"]
            title = clean_filename(result["data"].get("title") or "TikTok Video")

            # Save cache permanently
            cursor.execute(
                "INSERT OR REPLACE INTO cache (url, video_url, title) VALUES (?,?,?)",
                (url, video_url, title)
            )

            increment_stat("downloads")
            increment_stat("videos_served")

            cursor.execute(
                "INSERT INTO logs (ip, url, timestamp) VALUES (?,?,?)",
                (ip, url, int(time.time()))
            )

            conn.commit()

            return jsonify({
                "success": True,
                "url": video_url,
                "title": title
            })

        return jsonify({"success": False, "message": "Invalid response"}), 500

    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


# ===== FILE SERVING =====
@app.route("/file")
def serve_file():

    video_url = request.args.get("url")
    title = request.args.get("title") or "TikTok Video"

    title = clean_filename(title)

    filename = f"ToolifyX Downloader-{rand}.mp4"

    try:

        r = session.get(video_url, stream=True, timeout=60)

        headers = {
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Type": "video/mp4"
        }

        if r.headers.get("Content-Length"):
            headers["Content-Length"] = r.headers.get("Content-Length")

        return Response(
            r.iter_content(chunk_size=8192),
            headers=headers
        )

    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


# ===== PERMANENT STATS API =====
@app.route("/stats")
def stats():

    cursor.execute("SELECT * FROM stats WHERE id=1")
    s = cursor.fetchone()

    cursor.execute("SELECT COUNT(DISTINCT ip) FROM logs")
    unique_ips = cursor.fetchone()[0]

    cursor.execute("""
    SELECT ip, url, timestamp
    FROM logs
    ORDER BY id DESC
    LIMIT 100
    """)

    logs = [
        {"ip": row[0], "url": row[1], "timestamp": row[2]}
        for row in cursor.fetchall()
    ]

    return jsonify({
        "requests": s[1],
        "downloads": s[2],
        "cache_hits": s[3],
        "videos_served": s[4],
        "unique_ips": unique_ips,
        "logs": logs
    })


# ===== ADMIN CLEAR (YOU CONTROL THIS) =====
@app.route("/admin/clear", methods=["POST"])
def clear():

    cursor.execute("DELETE FROM logs")
    cursor.execute("DELETE FROM cache")

    cursor.execute("""
    UPDATE stats
    SET requests=0, downloads=0, cache_hits=0, videos_served=0
    WHERE id=1
    """)

    conn.commit()

    return jsonify({"success": True, "message": "All stats cleared"})


# ===== START SERVER =====
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)