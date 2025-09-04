import os
import requests
from flask import Flask, request, jsonify
from flask_cors import CORS
from bs4 import BeautifulSoup

app = Flask(__name__)
CORS(app)  # allow cross-origin requests

@app.route("/download", methods=["POST"])
def download_video():
    data = request.get_json()
    url = data.get("url")

    if not url:
        return jsonify({"success": False, "message": "No URL provided."})

    try:
        # fetch TikTok page
        headers = {"User-Agent": "Mozilla/5.0"}
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code != 200:
            return jsonify({"success": False, "message": "Failed to fetch TikTok page."})

        # parse page and extract video URL
        soup = BeautifulSoup(r.text, "html.parser")
        video_tag = soup.find("video")
        if not video_tag or not video_tag.get("src"):
            return jsonify({"success": False, "message": "Could not find video link."})

        video_url = video_tag["src"]

        return jsonify({"success": True, "url": video_url})

    except Exception as e:
        return jsonify({"success": False, "message": str(e)})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, threadrequests
