import os
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
        # Call tikwm API (reliable TikTok downloader)
        api_url = "https://www.tikwm.com/api/"
        res = requests.post(api_url, json={"url": url}, headers={"Content-Type": "application/json"})

        if res.status_code != 200:
            return jsonify({"success": False, "message": "TikTok API failed."})

        result = res.json()

        if result.get("data") and result["data"].get("play"):
            video_url = result["data"]["play"]  # Direct video link
            return jsonify({"success": True, "url": video_url})

        return jsonify({"success": False, "message": "Invalid TikTok response."})

    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, threaded=True)
