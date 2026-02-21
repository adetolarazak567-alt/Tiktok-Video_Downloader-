import time
import requests
import re
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

cache = {}  # url -> {video_url, title}


# ===== CLEAN FILENAME FUNCTION =====
def clean_filename(text):
    text = re.sub(r'[\\/*?:"<>|]', "", text)  # remove invalid characters
    text = re.sub(r'\s+', " ", text).strip()
    return text[:120]  # limit length


# ===== DOWNLOAD API =====
@app.route("/download", methods=["POST"])
def download_video():
    stats["requests"] += 1

    data = request.get_json()
    url = data.get("url")
    ip = request.remote_addr

    if not url:
        return jsonify({"success": False, "message": "No URL"}), 400

    stats["unique_ips"].add(ip)

    # ===== CACHE HIT =====
    if url in cache:
        stats["cache_hits"] += 1
        stats["downloads"] += 1
        stats["videos_served"] += 1

        return jsonify({
            "success": True,
            "url": cache[url]["video_url"],
            "title": cache[url]["title"]
        })

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
            title = result["data"].get("title") or "TikTok Video"

            clean_title = clean_filename(title)

            cache[url] = {
                "video_url": video_url,
                "title": clean_title
            }

            stats["downloads"] += 1
            stats["videos_served"] += 1

            return jsonify({
                "success": True,
                "url": video_url,
                "title": clean_title
            })

        return jsonify({"success": False, "message": "Invalid response"}), 500

    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


# ===== FILE SERVING ROUTE =====
@app.route("/file")
def serve_file():

    video_url = request.args.get("url")
    title = request.args.get("title")

    if not video_url:
        return jsonify({"success": False, "message": "No video URL"}), 400

    try:
        r = session.get(video_url, stream=True, timeout=60)

        if not title:
            title = "TikTok Video"

        title = clean_filename(title)

        filename = f"ToolifyX Downloader - {title}.mp4"

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