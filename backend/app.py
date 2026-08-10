import threading
import time
import requests
import random
import string
import re
import sqlite3
import concurrent.futures
import subprocess
import json
import os
from dotenv import load_dotenv

load_dotenv()

from flask import Flask, request, jsonify, Response
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

# ===== SESSION SETUP =====
session = requests.Session()

from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
})

retry = Retry(
    total=3,
    backoff_factor=0.2,
    status_forcelist=[429, 500, 502, 503, 504]
)

adapter = HTTPAdapter(
    max_retries=retry,
    pool_connections=100,
    pool_maxsize=100
)

session.mount("http://", adapter)
session.mount("https://", adapter)

# ===== THREAD-SAFE SQLITE =====
thread_local = threading.local()

def get_db():
    """Create a new connection per thread — NEVER share across threads"""
    if not hasattr(thread_local, 'conn') or thread_local.conn is None:
        thread_local.conn = sqlite3.connect("stats.db", check_same_thread=False)
    return thread_local.conn

def init_db():
    """Initialize database tables"""
    conn = sqlite3.connect("stats.db")
    c = conn.cursor()

    c.execute('''
    CREATE TABLE IF NOT EXISTS stats (
        key TEXT PRIMARY KEY,
        value INTEGER
    )
    ''')

    for key in ["requests", "downloads", "cache_hits", "videos_served", "yt_dlp_fails", "api_fails"]:
        c.execute("INSERT OR IGNORE INTO stats (key,value) VALUES (?,?)", (key, 0))

    c.execute('''
    CREATE TABLE IF NOT EXISTS unique_ips (
        ip TEXT PRIMARY KEY
    )
    ''')

    c.execute('''
    CREATE TABLE IF NOT EXISTS video_cache (
        url TEXT PRIMARY KEY,
        video_url TEXT,
        title TEXT,
        author TEXT,
        thumbnail TEXT,
        created_at INTEGER
    )
    ''')

    c.execute('''
    CREATE TABLE IF NOT EXISTS download_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ip TEXT,
        url TEXT,
        timestamp INTEGER,
        source TEXT,
        success INTEGER
    )
    ''')

    conn.commit()
    conn.close()

init_db()

# ===== RAM CACHE =====
cache = {}

# ===== HELPERS =====
def clean_filename(text):
    if not text:
        return "video"
    text = re.sub(r'[\\/*?:"<>|]', "", text)
    text = re.sub(r'\s+', " ", text).strip()
    return text[:120]

def random_string(length=6):
    return '''.join(random.choices(string.ascii_letters + string.digits, k=length))

def expand_url(url):
    """Expand short TikTok links"""
    try:
        if any(x in url for x in ["vt.tiktok.com", "vm.tiktok.com", "t.tiktok.com"]):
            r = session.head(url, allow_redirects=True, timeout=5)
            return r.url
    except Exception as e:
        print(f"Expand error: {e}")
    return url

def update_stats(key, ip=None, url=None, source=None, success=1):
    """Thread-safe stats update"""
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute("UPDATE stats SET value=value+1 WHERE key=?", (key,))
        if ip:
            c.execute("INSERT OR IGNORE INTO unique_ips (ip) VALUES (?)", (ip,))
        if ip and url:
            c.execute(
                "INSERT INTO download_logs (ip,url,timestamp,source,success) VALUES (?,?,?,?,?)",
                (ip, url, int(time.time()), source or "unknown", success)
            )
        conn.commit()
    except Exception as e:
        print(f"Stats update error: {e}")

def save_cache_db(url, result):
    """Thread-safe cache save"""
    try:
        conn = sqlite3.connect("stats.db")
        c = conn.cursor()
        c.execute(
            """INSERT OR REPLACE INTO video_cache 
               (url, video_url, title, author, thumbnail, created_at) 
               VALUES (?,?,?,?,?,?)""",
            (url, result.get("video_url"), result.get("title"), 
             result.get("author"), result.get("thumbnail"), int(time.time()))
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"DB cache save error: {e}")

# ============================================================================
# METHOD 1: yt-dlp (MOST RELIABLE - Install on your server)
# ============================================================================
# Install: pip install yt-dlp
# This is the ONLY consistently working method in 2026
# ============================================================================

def fetch_yt_dlp(url):
    """
    Use yt-dlp to extract TikTok video info.
    This is the MOST RELIABLE method. Install yt-dlp on your server.
    """
    try:
        import yt_dlp

        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'cookiesfrombrowser': None,  # Don't use browser cookies
            'nocheckcertificate': True,
            'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'referer': 'https://www.tiktok.com/',
            'headers': {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.5',
                'Accept-Encoding': 'gzip, deflate, br',
                'DNT': '1',
                'Connection': 'keep-alive',
            }
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

            # Find best format (video + audio)
            formats = info.get('formats', [])
            best_url = None

            # Prefer format with both video and audio
            for fmt in formats:
                if fmt.get('vcodec') != 'none' and fmt.get('acodec') != 'none':
                    best_url = fmt.get('url')
                    break

            # Fallback: any video format
            if not best_url:
                for fmt in formats:
                    if fmt.get('vcodec') != 'none':
                        best_url = fmt.get('url')
                        break

            if best_url:
                return {
                    "video_url": best_url,
                    "title": info.get('title', 'TikTok Video'),
                    "author": info.get('uploader', 'Unknown'),
                    "thumbnail": info.get('thumbnail', ''),
                    "duration": info.get('duration', 0),
                    "source": "yt-dlp"
                }

    except ImportError:
        print("yt-dlp not installed. Run: pip install yt-dlp")
    except Exception as e:
        print(f"yt-dlp error: {e}")
        update_stats("yt_dlp_fails")

    return None


# ============================================================================
# METHOD 2: Paid API Fallbacks (If yt-dlp fails or not installed)
# ============================================================================
# Sign up for one of these and add your API key to .env:
# - RapidAPI TikTok Downloader (~$10-20/month)
# - ScrapeBadger (pay-per-use, ~$0.005/request)
# - Apify TikTok Downloader Actor
# ============================================================================

def fetch_rapidapi(url):
    """
    RapidAPI TikTok Downloader - requires API key
    Sign up: https://rapidapi.com/LaurynProsacco58/api/tiktok-video-downloader-api-no-watermark
    """
    api_key = os.getenv("RAPIDAPI_KEY")
    if not api_key:
        return None

    try:
        headers = {
            "x-rapidapi-key": api_key,
            "x-rapidapi-host": "tiktok-video-downloader-api-no-watermark.p.rapidapi.com"
        }

        res = session.get(
            "https://tiktok-video-downloader-api-no-watermark.p.rapidapi.com/media/download",
            params={"url": url},
            headers=headers,
            timeout=15
        )

        if res.status_code == 200:
            data = res.json()
            video_url = data.get("video", {}).get("noWatermark") or data.get("video_url")
            if video_url:
                return {
                    "video_url": video_url,
                    "title": data.get("title", "TikTok Video"),
                    "author": data.get("author", "Unknown"),
                    "thumbnail": data.get("thumbnail", ""),
                    "source": "rapidapi"
                }
    except Exception as e:
        print(f"RapidAPI error: {e}")

    return None


def fetch_scrapebadger(url):
    """
    ScrapeBadger TikTok API - requires API key
    Sign up: https://scrapebadger.com
    """
    api_key = os.getenv("SCRAPEBADGER_KEY")
    if not api_key:
        return None

    try:
        # Extract video ID from URL
        video_id_match = re.search(r'/video/(\d+)', url)
        video_id = video_id_match.group(1) if video_id_match else None

        if not video_id:
            return None

        res = session.get(
            f"https://scrapebadger.com/v1/tiktok/videos/{video_id}",
            headers={"x-api-key": api_key},
            params={"region": "US"},
            timeout=20
        )

        if res.status_code == 200:
            data = res.json()
            video_data = data.get("video", {})

            # Get download URL from formats
            formats = video_data.get("formats", [])
            video_url = None
            for fmt in formats:
                if fmt.get("vcodec") != "none":
                    video_url = fmt.get("url")
                    break

            if video_url:
                author = video_data.get("author", {})
                return {
                    "video_url": video_url,
                    "title": video_data.get("desc", "TikTok Video")[:200],
                    "author": f"@{author.get('unique_id', 'unknown')}",
                    "thumbnail": video_data.get("cover", ""),
                    "source": "scrapebadger"
                }
    except Exception as e:
        print(f"ScrapeBadger error: {e}")

    return None


# ============================================================================
# METHOD 3: Direct scraping with requests (Last resort, often blocked)
# ============================================================================

def fetch_direct_scrape(url):
    """
    Direct TikTok page scraping - often blocked by Cloudflare/TikTok
    This is a fallback that may work occasionally.
    """
    try:
        res = session.get(url, timeout=15, allow_redirects=True)

        if res.status_code == 200:
            # Look for SSR data
            data_match = re.search(r'<script id="SIGI_STATE" type="application/json">(.*?)</script>', res.text)
            if data_match:
                data = json.loads(data_match.group(1))

                # Extract video info from SIGI_STATE
                item_module = data.get("ItemModule", {})
                if item_module:
                    video_id = list(item_module.keys())[0]
                    video_data = item_module[video_id]

                    video_url = video_data.get("video", {}).get("playAddr")
                    if video_url:
                        # Clean up the URL
                        if video_url.startswith("//"):
                            video_url = "https:" + video_url

                        author = video_data.get("author", "")
                        return {
                            "video_url": video_url,
                            "title": video_data.get("desc", "TikTok Video")[:200],
                            "author": f"@{author}" if author else "Unknown",
                            "thumbnail": video_data.get("cover", ""),
                            "source": "direct_scrape"
                        }
    except Exception as e:
        print(f"Direct scrape error: {e}")

    return None


# ============================================================================
# MAIN FETCH LOGIC: Try methods in order of reliability
# ============================================================================

def fetch_tiktok_video(url):
    """
    Fetch TikTok video using multiple methods in priority order:
    1. yt-dlp (most reliable, requires installation)
    2. Paid APIs (RapidAPI, ScrapeBadger)
    3. Direct scraping (last resort)
    """
    url = expand_url(url)
    print(f"Fetching: {url}")

    # Check RAM cache first (1 hour expiry)
    if url in cache:
        cached = cache[url]
        if time.time() - cached.get("timestamp", 0) < 3600:
            print("RAM cache hit")
            update_stats("cache_hits")
            return cached.get("data")

    # Check DB cache (24 hour expiry)
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT video_url, title, author, thumbnail FROM video_cache WHERE url=? AND created_at > ?", 
                  (url, int(time.time()) - 86400))
        row = c.fetchone()
        if row:
            print("DB cache hit")
            update_stats("cache_hits")
            return {
                "video_url": row[0],
                "title": row[1] or "TikTok Video",
                "author": row[2] or "Unknown",
                "thumbnail": row[3] or "",
                "original_url": url,
                "source": "cache"
            }
    except Exception as e:
        print(f"Cache check error: {e}")

    # Try methods in order
    methods = [
        ("yt-dlp", fetch_yt_dlp),
        ("rapidapi", fetch_rapidapi),
        ("scrapebadger", fetch_scrapebadger),
        ("direct_scrape", fetch_direct_scrape),
    ]

    for name, method in methods:
        print(f"Trying {name}...")
        result = method(url)
        if result:
            result["original_url"] = url
            print(f"✅ SUCCESS via {name}")

            # Save to caches
            cache[url] = {"data": result, "timestamp": time.time()}
            threading.Thread(target=save_cache_db, args=(url, result), daemon=True).start()

            return result
        print(f"❌ {name} failed")

    update_stats("api_fails")
    return None


# ============================================================================
# FLASK ROUTES
# ============================================================================

@app.route("/download", methods=["POST"])
def download_video():
    try:
        data = request.get_json()
        url = data.get("url")
        ip = request.remote_addr or request.headers.get("X-Forwarded-For", "unknown")

        if not url:
            return jsonify({"success": False, "message": "No URL provided"}), 400

        # Validate URL
        if "tiktok.com" not in url and "vt.tiktok.com" not in url and "vm.tiktok.com" not in url:
            return jsonify({"success": False, "message": "Invalid TikTok URL"}), 400

        # Update stats
        update_stats("requests", ip)

        # Fetch video
        result = fetch_tiktok_video(url)

        print(f"FETCH RESULT: {result}")

        if not result:
            return jsonify({
                "success": False,
                "message": "Unable to fetch video. All download methods failed. Please ensure yt-dlp is installed (pip install yt-dlp) or configure a paid API key."
            }), 503

        video_url = result["video_url"]
        title = result.get("title", "")
        author = result.get("author", "")
        thumbnail = result.get("thumbnail", "")
        original_url = result.get("original_url", url)
        source = result.get("source", "unknown")

        # Update success stats
        update_stats("downloads", ip, url, source, 1)
        update_stats("videos_served")

        filename = clean_filename(title or "ToolifyX Downloader") + "_" + random_string() + ".mp4"

        return jsonify({
            "success": True,
            "url": video_url,
            "filename": filename,
            "title": title,
            "author": author,
            "thumbnail": thumbnail,
            "videoId": original_url,
            "source": source
        })

    except Exception as e:
        print(f"DOWNLOAD ERROR: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({
            "success": False,
            "message": "Server error occurred"
        }), 500


@app.route("/file")
def serve_file():
    """Stream video file with range support"""
    video_url = request.args.get("url")
    video_id = request.args.get("videoId")
    mode = request.args.get("mode", "preview")

    # Re-fetch fresh URL if videoId provided
    if video_id and not video_url:
        result = fetch_tiktok_video(video_id)
        if result:
            video_url = result["video_url"]
        else:
            return jsonify({
                "success": False,
                "message": "Could not re-fetch video. Link may be expired or invalid."
            }), 500

    if not video_url:
        return jsonify({"success": False, "message": "No video URL"}), 400

    try:
        # Parse Range header
        range_header = request.headers.get("Range")
        source_headers = {}
        if range_header:
            source_headers["Range"] = range_header

        # Request from source
        r = session.get(video_url, stream=True, timeout=20, headers=source_headers)

        # Handle redirect if needed
        if r.status_code in (301, 302, 307, 308) and r.headers.get("Location"):
            r = session.get(r.headers["Location"], stream=True, timeout=20, headers=source_headers)

        status_code = 206 if r.status_code == 206 else (200 if r.status_code == 200 else r.status_code)

        if status_code not in (200, 206):
            return jsonify({
                "success": False,
                "message": f"Source returned status {r.status_code}"
            }), 502

        rand = random_string()
        filename = f"ToolifyX Downloader-{rand}.mp4"

        headers = {
            "Content-Type": r.headers.get("Content-Type", "video/mp4"),
            "Accept-Ranges": "bytes",
        }

        if "Content-Range" in r.headers:
            headers["Content-Range"] = r.headers["Content-Range"]
        if "Content-Length" in r.headers:
            headers["Content-Length"] = r.headers["Content-Length"]

        disposition = (
            f'attachment; filename="{filename}"' if mode == "download"
            else f'inline; filename="{filename}"'
        )
        headers["Content-Disposition"] = disposition

        def generate():
            for chunk in r.iter_content(chunk_size=65536):
                if chunk:
                    yield chunk

        return Response(generate(), status=status_code, headers=headers)

    except Exception as e:
        print(f"SERVE ERROR: {e}")
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/stats", methods=["GET"])
def get_stats():
    try:
        conn = get_db()
        c = conn.cursor()

        c.execute("SELECT key, value FROM stats")
        stats_data = dict(c.fetchall())

        c.execute("SELECT COUNT(*) FROM unique_ips")
        unique_ips_count = c.fetchone()[0]

        c.execute("SELECT ip, url, timestamp, source, success FROM download_logs ORDER BY timestamp DESC LIMIT 100")
        logs = [
            {"ip": ip, "url": url, "timestamp": ts, "source": src, "success": bool(succ)}
            for ip, url, ts, src, succ in c.fetchall()
        ]

        return jsonify({
            **stats_data,
            "unique_ips": unique_ips_count,
            "download_logs": logs
        })
    except Exception as e:
        print(f"STATS ERROR: {e}")
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/wake", methods=["GET"])
def wake():
    return jsonify({"success": True, "message": "Server is awake"})


@app.route("/admin/reset", methods=["POST"])
def reset_stats():
    data = request.get_json()
    password = data.get("password")

    if password != os.getenv("ADMIN_PASSWORD"):
        return jsonify({"success": False, "message": "Wrong password"}), 401

    try:
        conn = get_db()
        c = conn.cursor()

        for key in ["requests", "downloads", "cache_hits", "videos_served", "yt_dlp_fails", "api_fails"]:
            c.execute("UPDATE stats SET value=0 WHERE key=?", (key,))

        c.execute("DELETE FROM unique_ips")
        c.execute("DELETE FROM download_logs")
        c.execute("DELETE FROM video_cache")

        conn.commit()
        cache.clear()

        return jsonify({"success": True, "message": "All stats and caches reset"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/health", methods=["GET"])
def health_check():
    """Check which download methods are available"""
    methods = {
        "yt_dlp": False,
        "rapidapi": bool(os.getenv("RAPIDAPI_KEY")),
        "scrapebadger": bool(os.getenv("SCRAPEBADGER_KEY")),
    }

    try:
        import yt_dlp
        methods["yt_dlp"] = True
    except ImportError:
        pass

    return jsonify({
        "success": True,
        "methods_available": methods,
        "message": "Install yt-dlp for best results: pip install yt-dlp"
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, threaded=True)
