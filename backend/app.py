from flask import Flask, render_template, request, jsonify
import requests

app = Flask(__name__)

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/download", methods=["POST"])
def download_video():
    data = request.get_json()
    url = data.get("url")

    if not url:
        return jsonify({"success": False, "message": "No URL provided."})

    try:
        # -----------------------------
        # Replace this section with your actual TikTok fetch logic or API
        # Example placeholder: return a static demo video
        video_url = "https://example.com/demo.mp4"  # Replace with TikTok video URL
        # -----------------------------

        return jsonify({"success": True, "url": video_url})

    except Exception as e:
        return jsonify({"success": False, "message": str(e)})

if __name__ == "__main__":
    # threaded=True handles multiple requests at once
    app.run(debug=True, host="0.0.0.0", port=5000, threaded=True)
