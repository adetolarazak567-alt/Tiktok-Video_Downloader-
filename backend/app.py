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
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

session.headers.update({
    "User-Agent": "Mozilla/5.0"
})

retry = Retry(
    total=5,
    backoff_factor=1,
    status_forcelist=[429, 500, 502, 503, 504]
)

adapter = HTTPAdapter(max_retries=retry)

session.mount("http://", adapter)
session.mount("https://", adapter)

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

# ====== Fetch_tiktok_video ======
def fetch_tiktok_video(url):

    max_retries = 5

    for attempt in range(max_retries):

        # PRIMARY API
        try:
            res = session.post(
                "https://www.tikwm.com/api/",
                data={"url": url, "hd": "1"},
                headers={
                    "Origin": "https://www.tikwm.com",
                    "Referer": "https://www.tikwm.com/",
                    "Content-Type": "application/x-www-form-urlencoded"
                },
                timeout=20
            )

            if res.status_code == 200:

                data = res.json()

                if data.get("code") == 0:

                    video = data["data"].get("play")

                    if video:
                        return video

        except Exception as e:
            print("Primary failed:", e)


        # BACKUP API
        try:
            res = session.post(
                "https://tikwm.com/api/",
                data={"url": url},
                timeout=20
            )

            if res.status_code == 200:

                data = res.json()

                video = data.get("data", {}).get("play")

                if video:
                    return video

        except Exception as e:
            print("Backup failed:", e)


        time.sleep(2)

    return None
# ====== DOWNLOAD ROUTE ======
@app.route("/download", methods=["POST"])
def download_video():

    try:

        data = request.get_json()
        url = data.get("url")
        ip = request.remote_addr

        if not url:
            return jsonify({"success": False, "message": "No URL"}), 400


        # increment requests safely
        try:
            c.execute("UPDATE stats SET value = value + 1 WHERE key='requests'")
            c.execute("INSERT OR IGNORE INTO unique_ips (ip) VALUES (?)", (ip,))
            conn.commit()
        except:
            pass


        # CACHE HIT (FASTEST)
        if url in cache:

            try:
                c.execute("UPDATE stats SET value=value+1 WHERE key='cache_hits'")
                c.execute("UPDATE stats SET value=value+1 WHERE key='downloads'")
                c.execute("UPDATE stats SET value=value+1 WHERE key='videos_served'")
                conn.commit()
            except:
                pass

            filename = clean_filename("ToolifyX Downloader") + "_" + random_string() + ".mp4"

            return jsonify({
                "success": True,
                "url": cache[url],
                "filename": filename
            })


        # FETCH VIDEO (RETRY SAFE)
        video_url = fetch_tiktok_video(url)

        if not video_url:
            return jsonify({
                "success": False,
                "message": "Fetch failed, try again"
            }), 500


        # SAVE CACHE
        cache[url] = video_url


        # update stats safely
        try:
            c.execute("UPDATE stats SET value=value+1 WHERE key='downloads'")
            c.execute("UPDATE stats SET value=value+1 WHERE key='videos_served'")

            c.execute(
                "INSERT INTO download_logs (ip, url, timestamp) VALUES (?, ?, ?)",
                (ip, url, int(time.time()))
            )

            conn.commit()
        except:
            pass


        filename = clean_filename("ToolifyX Downloader") + "_" + random_string() + ".mp4"

        return jsonify({
            "success": True,
            "url": video_url,
            "filename": filename
        })


    except Exception as e:

        print("CRASH PREVENTED:", e)

        return jsonify({
            "success": False,
            "message": "Server recovered automatically"
        }), 500

    

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
    "Content-Disposition": f'inline; filename="{filename}"',
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

# ====== WAKE ROUTE (KEEP SERVER ALIVE) ======
@app.route("/wake", methods=["GET"])
def wake():
    return jsonify({
        "success": True,
        "message": "Server is awake"
    })

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
    while True:
        try:
            app.run(host="0.0.0.0", port=5000, threaded=True)
        except Exception as e:
            print("Server crashed, restarting...", e)
            time.sleep(5)