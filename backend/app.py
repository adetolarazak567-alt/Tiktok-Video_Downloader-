import threading
import time
import requests
import random
import string
import re
import sqlite3
import concurrent.futures
from dotenv import load_dotenv
import os

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

# ===== THREAD-SAFE SQLITE (CRITICAL FIX) =====
def get_db():
    """Create a new connection per thread — NEVER share connections across threads"""
    if not hasattr(thread_local, 'conn') or thread_local.conn is None:
        thread_local.conn = sqlite3.connect("stats.db", check_same_thread=False)
    return thread_local.conn

thread_local = threading.local()

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
    
    for key in ["requests", "downloads", "cache_hits", "videos_served"]:
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
        timestamp INTEGER
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
    return ''.join(random.choices(string.ascii_letters + string.digits, k=length))

def expand_url(url):
    """Expand short TikTok links"""
    try:
        if any(x in url for x in ["vt.tiktok.com", "vm.tiktok.com", "t.tiktok.com"]):
            r = session.head(url, allow_redirects=True, timeout=5)
            return r.url
    except Exception as e:
        print(f"Expand error: {e}")
    return url

# ===== WORKING API FETCHERS (REPLACEMENTS) =====

def fetch_ttsave(url):
    """
    ttsave.app - Currently working API
    """
    try:
        # Step 1: Get the main page to establish session
        page = session.get("https://ttsave.app/", timeout=10)
        
        # Step 2: Send download request
        res = session.post(
            "https://ttsave.app/download",
            json={"query": url},
            timeout=15,
            headers={
                "Content-Type": "application/json",
                "Origin": "https://ttsave.app",
                "Referer": "https://ttsave.app/",
                "X-Requested-With": "XMLHttpRequest"
            }
        )
        
        print(f"ttsave status: {res.status_code}")
        
        if res.status_code == 200:
            # Response is HTML, parse for video URL
            text = res.text
            
            # Look for video URL patterns in the HTML
            video_match = re.search(r'href="(https?://[^"]+\.mp4[^"]*)"', text)
            if video_match:
                video_url = video_match.group(1).replace("&amp;", "&")
                
                # Extract other metadata from HTML if available
                title_match = re.search(r'<h2[^>]*>(.*?)</h2>', text, re.DOTALL)
                title = re.sub(r'<[^>]+>', '', title_match.group(1)).strip() if title_match else ""
                
                author_match = re.search(r'@([^<\s]+)', text)
                author = f"@{author_match.group(1)}" if author_match else ""
                
                thumb_match = re.search(r'src="(https?://[^"]+\.(?:jpg|jpeg|png|webp)[^"]*)"', text)
                thumbnail = thumb_match.group(1) if thumb_match else ""
                
                return {
                    "video_url": video_url,
                    "title": title or "TikTok Video",
                    "author": author or "Unknown",
                    "thumbnail": thumbnail,
                    "source": "ttsave"
                }
            else:
                print(f"ttsave: No video URL found in response")
                print(f"Response preview: {text[:500]}")
                
    except Exception as e:
        print(f"ttsave error: {e}")
    return None


def fetch_savetik(url):
    """
    savetik.co - Alternative working API
    """
    try:
        res = session.post(
            "https://savetik.co/api/ajaxSearch",
            data={"q": url, "lang": "en"},
            timeout=15,
            headers={
                "X-Requested-With": "XMLHttpRequest",
                "Origin": "https://savetik.co",
                "Referer": "https://savetik.co/en"
            }
        )
        
        print(f"savetik status: {res.status_code}")
        
        if res.status_code == 200:
            data = res.json()
            print(f"savetik response: {data}")
            
            if data.get("status") == "ok" and data.get("statusCode") == 200:
                # Parse HTML content for video links
                html = data.get("data", "")
                
                # Find video URL
                video_match = re.search(r'href="(https?://[^"]+\.mp4[^"]*)"', html)
                if video_match:
                    video_url = video_match.group(1).replace("&amp;", "&")
                    
                    # Extract metadata
                    title_match = re.search(r'<h3[^>]*>(.*?)</h3>', html, re.DOTALL)
                    title = re.sub(r'<[^>]+>', '', title_match.group(1)).strip() if title_match else ""
                    
                    author_match = re.search(r'@([^<\s]+)', html)
                    author = f"@{author_match.group(1)}" if author_match else ""
                    
                    thumb_match = re.search(r'src="(https?://[^"]+\.(?:jpg|jpeg|png|webp)[^"]*)"', html)
                    thumbnail = thumb_match.group(1) if thumb_match else ""
                    
                    return {
                        "video_url": video_url,
                        "title": title or "TikTok Video",
                        "author": author or "Unknown",
                        "thumbnail": thumbnail,
                        "source": "savetik"
                    }
            elif data.get("statusCode") == 404:
                print(f"savetik: Video not found (may be private/deleted)")
            else:
                print(f"savetik: Unexpected response - {data}")
                
    except Exception as e:
        print(f"savetik error: {e}")
    return None


def fetch_tiktokdownload(url):
    """
    tiktokdownload.online - Another alternative
    """
    try:
        res = session.post(
            "https://tiktokdownload.online/",
            data={"url": url},
            timeout=15,
            headers={
                "Origin": "https://tiktokdownload.online",
                "Referer": "https://tiktokdownload.online/",
                "X-Requested-With": "XMLHttpRequest"
            }
        )
        
        print(f"tiktokdownload status: {res.status_code}")
        
        if res.status_code == 200:
            try:
                data = res.json()
                if "error" not in data:
                    # Extract from their format
                    video_url = data.get("video_url") or data.get("url")
                    if video_url:
                        return {
                            "video_url": video_url,
                            "title": data.get("title", "TikTok Video"),
                            "author": data.get("author", "Unknown"),
                            "thumbnail": data.get("thumbnail", ""),
                            "source": "tiktokdownload"
                        }
            except:
                # HTML response, try to parse
                text = res.text
                video_match = re.search(r'(https?://[^"\']+\.mp4[^"\'\s]*)', text)
                if video_match:
                    return {
                        "video_url": video_match.group(1),
                        "title": "TikTok Video",
                        "author": "Unknown",
                        "thumbnail": "",
                        "source": "tiktokdownload"
                    }
                    
    except Exception as e:
        print(f"tiktokdownload error: {e}")
    return None


# ===== PARALLEL FETCH =====
def fetch_tiktok_video(url):
    """
    Try multiple APIs in parallel, return first success
    """
    url = expand_url(url)
    print(f"Fetching: {url}")
    
    # Check RAM cache first
    if url in cache:
        cached = cache[url]
        if time.time() - cached.get("timestamp", 0) < 3600:  # 1 hour cache
            print("RAM cache hit")
            return cached.get("data")
    
    # Check DB cache
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT video_url, title, author, thumbnail FROM video_cache WHERE url=? AND created_at > ?", 
                  (url, int(time.time()) - 86400))  # 24h DB cache
        row = c.fetchone()
        if row:
            print("DB cache hit")
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
    
    # Try APIs in parallel
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        futures = [
            executor.submit(fetch_ttsave, url),
            executor.submit(fetch_savetik, url),
            executor.submit(fetch_tiktokdownload, url)
        ]
        
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            if result:
                result["original_url"] = url
                # Save to RAM cache
                cache[url] = {"data": result, "timestamp": time.time()}
                # Save to DB cache (non-blocking)
                threading.Thread(target=save_cache_db, args=(url, result), daemon=True).start()
                return result
    
    return None


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


# ===== STATS HELPERS =====
def update_stats(key, ip=None, url=None):
    """Thread-safe stats update"""
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute("UPDATE stats SET value=value+1 WHERE key=?", (key,))
        if ip:
            c.execute("INSERT OR IGNORE INTO unique_ips (ip) VALUES (?)", (ip,))
        if ip and url:
            c.execute(
                "INSERT INTO download_logs (ip,url,timestamp) VALUES (?,?,?)",
                (ip, url, int(time.time()))
            )
        conn.commit()
    except Exception as e:
        print(f"Stats update error: {e}")


# ===== ROUTES =====

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
                "message": "Unable to fetch video. The link may be private, deleted, or our download services are temporarily unavailable."
            }), 503
        
        video_url = result["video_url"]
        title = result.get("title", "")
        author = result.get("author", "")
        thumbnail = result.get("thumbnail", "")
        original_url = result.get("original_url", url)
        source = result.get("source", "unknown")
        
        # Update success stats
        update_stats("downloads", ip, url)
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
        
        c.execute("SELECT ip, url, timestamp FROM download_logs ORDER BY timestamp DESC LIMIT 100")
        logs = [
            {"ip": ip, "url": url, "timestamp": ts}
            for ip, url, ts in c.fetchall()
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
        
        for key in ["requests", "downloads", "cache_hits", "videos_served"]:
            c.execute("UPDATE stats SET value=0 WHERE key=?", (key,))
        
        c.execute("DELETE FROM unique_ips")
        c.execute("DELETE FROM download_logs")
        c.execute("DELETE FROM video_cache")
        
        conn.commit()
        
        # Clear RAM cache too
        cache.clear()
        
        return jsonify({"success": True, "message": "All stats and caches reset"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, threaded=True)
