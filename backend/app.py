import re
import json
import requests
from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

@app.route("/download", methods=["POST"])
def download_video():
    data = request.get_json()
    url = data.get("url")

    if not url:
        return jsonify({"success": False, "message": "No URL provided."})

    try:
        # Fetch TikTok page
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
        }
        resp = requests.get(url, headers=headers, timeout=15)
        if resp.status_code != 200:
            return jsonify({"success": False, "message": "Failed to fetch TikTok page."})

        html = resp.text

        # Extract SIGI_STATE JSON (TikTok’s embedded data)
        match = re.search(r'<script id="SIGI_STATE"[^>]*>(.*?)</script>', html)
        if not match:
            return jsonify({"success": False, "message": "Video data not found."})

        sigi_state = json.loads(match.group(1))

        # Find video info
        if "ItemModule" not in sigi_state or not sigi_state["ItemModule"]:
            return jsonify({"success": False, "message": "No video info available."})

        first_key = next(iter(sigi_state["ItemModule"]))
        video_data = sigi_state["ItemModule"][first_key]["video"]

        # Video URLs
        video_url = video_data.get("playAddr")  # With watermark
        no_watermark = video_data.get("downloadAddr")  # Usually no watermark

        return jsonify({
            "success": True,
            "url": no_watermark or video_url,
            "watermark": video_url,
            "no_watermark": no_watermark
        })

    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, threaded=True)
