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
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


# ================= APP =================
app = Flask(__name__)
CORS(app)


# ================= SESSION (FAST) =================
session = requests.Session()

session.headers.update({
    "User-Agent": "Mozilla/5.0"
})

retry = Retry(
    total=3,
    backoff_factor=0.2,
    status_forcelist=[429, 500, 502, 503, 504]
)

adapter = HTTPAdapter(
    max_retries=retry,
    pool_connections=500,
    pool_maxsize=500
)

session.mount("http://", adapter)
session.mount("https://", adapter)


# ================= DATABASE =================
conn = sqlite3.connect("stats.db", check_same_thread=False)
c = conn.cursor()

c.execute("""
CREATE TABLE IF NOT EXISTS stats (
    key TEXT PRIMARY KEY,
    value INTEGER
)
""")

for key in ["requests", "downloads", "cache_hits", "videos_served"]:
    c.execute(
        "INSERT OR IGNORE INTO stats (key, value) VALUES (?, ?)",
        (key, 0)
    )

c.execute("""
CREATE TABLE IF NOT EXISTS unique_ips (
    ip TEXT PRIMARY KEY
)
""")

c.execute("""
CREATE TABLE IF NOT EXISTS video_cache (
    url TEXT PRIMARY KEY,
    video_url TEXT
)
""")

c.execute("""
CREATE TABLE IF NOT EXISTS download_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ip TEXT,
    url TEXT,
    timestamp INTEGER
)
""")

conn.commit()


# ================= RAM CACHE =================
cache = {}


# ================= HELPERS =================
def clean_filename(text):
    text = re.sub(r'[\\/*?:"<>|]', "", text)
    text = re.sub(r'\s+', " ", text).strip()
    return text[:120]


def random_string(length=6):
    return ''.join(
        random.choices(
            string.ascii_letters + string.digits,
            k=length
        )
    )


# ================= THREAD SAFE DB SAVE =================
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


# ================= FETCH VIDEO =================
def fetch_tiktok_video(url):

    try:
        res = session.post(
            "https://www.tikwm.com/api/",
            data={"url": url, "hd": "1"},
            timeout=3
        )

        if res.status_code == 200:

            data = res.json()

            if data.get("code") == 0:

                video = data["data"].get("play")

                if video:
                    return video

    except Exception as e:
        print("Primary failed:", e)


    try:
        res = session.post(
            "https://tikwm.com/api/",
            data={"url": url},
            timeout=3
        )

        if res.status_code == 200:

            video = res.json().get("data", {}).get("play")

            if video:
                return video

    except Exception as e:
        print("Backup failed:", e)

    return None


# ================= DOWNLOAD ROUTE =================
@app.route("/download", methods=["POST"])
def download_video():

    try:

        data = request.get_json()

        if not data:
            return jsonify({"success": False}), 400

        url = data.get("url")

        if not url:
            return jsonify({"success": False}), 400

        ip = request.remote_addr


        # ===== stats =====
        try:
            c.execute(
                "UPDATE stats SET value=value+1 WHERE key='requests'"
            )

            c.execute(
                "INSERT OR IGNORE INTO unique_ips (ip) VALUES (?)",
                (ip,)
            )

            conn.commit()

        except:
            pass


        # ===== RAM CACHE =====
        if url in cache:

            video_url = cache[url]

            filename = clean_filename(
                "ToolifyX Downloader"
            ) + "_" + random_string() + ".mp4"

            return jsonify({
                "success": True,
                "url": video_url,
                "filename": filename
            })


        # ===== DB CACHE =====
        c.execute(
            "SELECT video_url FROM video_cache WHERE url=?",
            (url,)
        )

        row = c.fetchone()

        if row:

            video_url = row[0]

            cache[url] = video_url

            filename = clean_filename(
                "ToolifyX Downloader"
            ) + "_" + random_string() + ".mp4"

            return jsonify({
                "success": True,
                "url": video_url,
                "filename": filename
            })


        # ===== FETCH =====
        video_url = fetch_tiktok_video(url)

        if not video_url:

            return jsonify({
                "success": False,
                "message": "Fetch failed"
            }), 500


        # ===== SAVE RAM =====
        cache[url] = video_url


        # ===== SAVE DB THREAD =====
        threading.Thread(
            target=save_cache_db,
            args=(url, video_url),
            daemon=True
        ).start()


        # ===== stats =====
        try:

            c.execute(
                "UPDATE stats SET value=value+1 WHERE key='downloads'"
            )

            c.execute(
                "UPDATE stats SET value=value+1 WHERE key='videos_served'"
            )

            c.execute(
                """
                INSERT INTO download_logs
                (ip, url, timestamp)
                VALUES (?, ?, ?)
                """,
                (ip, url, int(time.time()))
            )

            conn.commit()

        except:
            pass


        filename = clean_filename(
            "ToolifyX Downloader"
        ) + "_" + random_string() + ".mp4"


        return jsonify({
            "success": True,
            "url": video_url,
            "filename": filename
        })


    except Exception as e:

        print("CRASH:", e)

        return jsonify({
            "success": False
        }), 500


# ================= FILE SERVE =================
@app.route("/file")
def serve_file():

    video_url = request.args.get("url")

    if not video_url:
        return jsonify({"success": False}), 400

    try:

        r = session.get(
            video_url,
            stream=True,
            timeout=10
        )

        filename = (
            "ToolifyX Downloader-"
            + random_string()
            + ".mp4"
        )

        headers = {
            "Content-Disposition":
            f'attachment; filename="{filename}"',

            "Content-Type": "video/mp4"
        }

        return Response(
            r.iter_content(chunk_size=8192),
            headers=headers
        )

    except Exception as e:

        return jsonify({
            "success": False,
            "error": str(e)
        })


# ================= ADMIN =================
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")


@app.route("/admin/reset", methods=["POST"])
def reset_stats():

    data = request.get_json()

    if data.get("password") != ADMIN_PASSWORD:
        return jsonify({"success": False}), 401

    c.execute("DELETE FROM download_logs")
    c.execute("DELETE FROM unique_ips")

    for key in [
        "requests",
        "downloads",
        "cache_hits",
        "videos_served"
    ]:
        c.execute(
            "UPDATE stats SET value=0 WHERE key=?",
            (key,)
        )

    conn.commit()

    return jsonify({"success": True})


# ================= START =================
if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=5000,
        threaded=True
    )