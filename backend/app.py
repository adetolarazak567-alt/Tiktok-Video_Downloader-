import time
import requests
import random
import string
from flask import Flask, request, jsonify, Response
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

session = requests.Session()

# ====== STATS STORAGE ======
stats = {
    "requests": 0,
    "downloads": 0,
    "cache_hits": 0,
    "videos_served": 0,
    "unique_ips": set(),
    "download_logs": []
}

cache = {}  # url -> video_url


# ===== DOWNLOAD API (gets video URL from TikWM) =====
@app.route("/download", methods=["POST"])
def download_video():
    stats["requests"] += 1

    data = request.get_json()
    url = data.get("url")
    ip = request.remote_addr

    if not url:
        return jsonify({"success": False, "message": "No URL"}), 400

    stats["unique_ips"].add(ip)

    # ⚡ CACHE HIT
    if url in cache:
        stats["cache_hits"] += 1
        stats["downloads"] += 1
        stats["videos_served"] += 1

        stats["download_logs"].append({
            "ip": ip,
            "url": url,
            "timestamp": int(time.time())
        })

        return jsonify({"success": True, "url": cache[url]})

    try:
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
        except requests.exceptions.Timeout:
            # retry once
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

            cache[url] = video_url

            stats["downloads"] += 1
            stats["videos_served"] += 1

            stats["download_logs"].append({
                "ip": ip,
                "url": url,
                "timestamp": int(time.time())
            })

            return jsonify({
                "success": True,
                "url": video_url
            })

        return jsonify({"success": False, "message": "Invalid response"}), 500

    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


# ===== FILE SERVING ROUTE (renames file to ToolifyX-random.mp4) =====
@app.route("/file")
def serve_file():

    video_url = request.args.get("url")

    if not video_url:
        return jsonify({"success": False, "message": "No video URL"}), 400

    try:
        r = session.get(video_url, stream=True, timeout=60)

        # generate random filename
        rand = ''.join(random.choices(string.ascii_letters + string.digits, k=6))
        filename = f"ToolifyX-{rand}.mp4"

        return Response(
            r.iter_content(chunk_size=8192),
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Content-Type": "video/mp4"
            }
        )

    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


# ===== STATS ROUTE =====
@app.route("/stats", methods=["GET"])
def get_stats():
    return jsonify({
        "requests": stats["requests"],
        "downloads": stats["downloads"],
        "cache_hits": stats["cache_hits"],
        "videos_served": stats["videos_served"],
        "unique_ips": len(stats["unique_ips"]),
        "download_logs": stats["download_logs"]
    })


# ===== START SERVER =====
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, threaded=True)