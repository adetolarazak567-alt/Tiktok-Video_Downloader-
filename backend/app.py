import threading
import time
import requests
import random
import string
import re
import sqlite3
import concurrent.futures
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
    "Accept-Encoding": "gzip, deflate, br",
    "DNT": "1",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Cache-Control": "max-age=0",
})

retry = Retry(total=3, backoff_factor=0.5, status_forcelist=[429, 500, 502, 503, 504])

adapter = HTTPAdapter(max_retries=retry, pool_connections=100, pool_maxsize=100)

session.mount("http://", adapter)
session.mount("https://", adapter)

# ===== THREAD-SAFE SQLITE =====
thread_local = threading.local()

def get_db():
    if not hasattr(thread_local, 'conn') or thread_local.conn is None:
        thread_local.conn = sqlite3.connect("stats.db", check_same_thread=False)
    return thread_local.conn

def init_db():
    conn = sqlite3.connect("stats.db")
    c = conn.cursor()
    
    c.execute("""
    CREATE TABLE IF NOT EXISTS stats (
        key TEXT PRIMARY KEY,
        value INTEGER
    )
    """)
    
    for key in ["requests", "downloads", "cache_hits", "videos_served", 
                "yt_dlp_success", "yt_dlp_fail", "scrape_success", "scrape_fail",
                "api_success", "api_fail"]:
        c.execute("INSERT OR IGNORE INTO stats (key,value) VALUES (?,?)", (key, 0))
    
    c.execute("""
    CREATE TABLE IF NOT EXISTS unique_ips (ip TEXT PRIMARY KEY)
    """)
    
    c.execute("""
    CREATE TABLE IF NOT EXISTS video_cache (
        url TEXT PRIMARY KEY,
        video_url TEXT, title TEXT, author TEXT, thumbnail TEXT, created_at INTEGER
    )
    """)
    
    c.execute("""
    CREATE TABLE IF NOT EXISTS download_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ip TEXT, url TEXT, timestamp INTEGER, source TEXT, success INTEGER
    )
    """)
    
    conn.commit()
    conn.close()

init_db()

cache = {}

def clean_filename(text):
    if not text:
        return "video"
    text = re.sub(r'[\\/*?:"<>|]', "", text)
    text = re.sub(r'[ \t\n\r]+', " ", text).strip()
    return text[:120]

def random_string(length=6):
    return ''.join(random.choices(string.ascii_letters + string.digits, k=length))

def expand_url(url):
    try:
        if any(x in url for x in ["vt.tiktok.com", "vm.tiktok.com", "t.tiktok.com"]):
            r = session.head(url, allow_redirects=True, timeout=5)
            return r.url
    except Exception as e:
        print(f"Expand error: {e}")
    return url

def extract_video_id(url):
    patterns = [r'/video/(\d+)', r'/v/(\d+)', r'video/(\d+)']
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None

def update_stats(key, ip=None, url=None, source=None, success=1):
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
# METHOD 1: yt-dlp (PRIMARY - Most Reliable, Completely Free)
# ============================================================================

def fetch_yt_dlp(url):
    try:
        import yt_dlp
        
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'nocheckcertificate': True,
            'cookiefile': None,
            'cookiesfrombrowser': None,
        }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            
            formats = info.get('formats', [])
            best_url = None
            
            for fmt in formats:
                if fmt.get('vcodec') != 'none' and fmt.get('acodec') != 'none':
                    best_url = fmt.get('url')
                    break
            
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
        print("[yt-dlp] Not installed. Run: pip install yt-dlp")
    except Exception as e:
        print(f"[yt-dlp] Error: {e}")
    
    return None


# ============================================================================
# METHOD 2: Direct TikTok Scraping (FALLBACK - Free)
# ============================================================================

def fetch_tiktok_api_direct(url):
    video_id = extract_video_id(url)
    if not video_id:
        print("[Direct] Could not extract video ID")
        return None
    
    try:
        mobile_headers = {
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "Referer": "https://www.tiktok.com/",
        }
        
        res = session.get(url, headers=mobile_headers, timeout=15, allow_redirects=True)
        
        if res.status_code == 200:
            # Pattern 1: SIGI_STATE
            sigi_match = re.search(r'<script id="SIGI_STATE" type="application/json">(.*?)</script>', res.text, re.DOTALL)
            if sigi_match:
                try:
                    sigi_data = json.loads(sigi_match.group(1))
                    item_module = sigi_data.get("ItemModule", {})
                    if item_module:
                        vid_data = list(item_module.values())[0]
                        video_info = vid_data.get("video", {})
                        play_addr = video_info.get("playAddr", "")
                        
                        if play_addr:
                            if play_addr.startswith("//"):
                                play_addr = "https:" + play_addr
                            
                            author_info = vid_data.get("author", "")
                            return {
                                "video_url": play_addr,
                                "title": vid_data.get("desc", "TikTok Video")[:200],
                                "author": f"@{author_info}" if author_info else "Unknown",
                                "thumbnail": video_info.get("cover", ""),
                                "source": "direct_scrape"
                            }
                except Exception as e:
                    print(f"[Direct] SIGI parse error: {e}")
            
            # Pattern 2: __UNIVERSAL_DATA_FOR_REHYDRATION__
            universal_match = re.search(r'<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__" type="application/json">(.*?)</script>', res.text, re.DOTALL)
            if universal_match:
                try:
                    uni_data = json.loads(universal_match.group(1))
                    
                    def find_video_data(obj):
                        if isinstance(obj, dict):
                            if "video" in obj and isinstance(obj["video"], dict):
                                video_obj = obj["video"]
                                if "playAddr" in video_obj or "downloadAddr" in video_obj:
                                    return video_obj
                            for v in obj.values():
                                result = find_video_data(v)
                                if result:
                                    return result
                        elif isinstance(obj, list):
                            for item in obj:
                                result = find_video_data(item)
                                if result:
                                    return result
                        return None
                    
                    video_obj = find_video_data(uni_data)
                    if video_obj:
                        play_addr = video_obj.get("playAddr", "") or video_obj.get("downloadAddr", "")
                        if play_addr:
                            if play_addr.startswith("//"):
                                play_addr = "https:" + play_addr
                            
                            return {
                                "video_url": play_addr,
                                "title": "TikTok Video",
                                "author": "Unknown",
                                "thumbnail": video_obj.get("cover", ""),
                                "source": "direct_scrape"
                            }
                except Exception as e:
                    print(f"[Direct] Universal data parse error: {e}")
            
            # Pattern 3: Any script with video URL
            script_matches = re.findall(r'<script[^>]*>(.*?)</script>', res.text, re.DOTALL)
            for script in script_matches:
                if "playAddr" in script or "downloadAddr" in script:
                    url_match = re.search(r'(https?://[a-zA-Z0-9._/:-]+\.mp4[a-zA-Z0-9._/:-]*)', script)
                    if url_match:
                        return {
                            "video_url": url_match.group(1),
                            "title": "TikTok Video",
                            "author": "Unknown",
                            "thumbnail": "",
                            "source": "direct_scrape"
                        }
        else:
            print(f"[Direct] Page fetch failed: {res.status_code}")
            
    except Exception as e:
        print(f"[Direct] Error: {e}")
    
    return None


# ============================================================================
# METHOD 3: Free Third-Party APIs (Last Resort - Often Broken)
# ============================================================================

def fetch_free_api_1(url):
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
        
        if res.status_code == 200:
            data = res.json()
            if data.get("status") == "ok" and data.get("statusCode") == 200:
                html = data.get("data", "")
                video_match = re.search(r'href="(https?://[^"]+\.mp4[^"]*)"', html)
                if video_match:
                    return {
                        "video_url": video_match.group(1).replace("&amp;", "&"),
                        "title": "TikTok Video",
                        "author": "Unknown",
                        "thumbnail": "",
                        "source": "savetik"
                    }
    except Exception as e:
        print(f"[FreeAPI1] Error: {e}")
    return None


def fetch_free_api_2(url):
    try:
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
        
        if res.status_code == 200:
            video_match = re.search(r'href="(https?://[^"]+\.mp4[^"]*)"', res.text)
            if video_match:
                return {
                    "video_url": video_match.group(1).replace("&amp;", "&"),
                    "title": "TikTok Video",
                    "author": "Unknown",
                    "thumbnail": "",
                    "source": "ttsave"
                }
    except Exception as e:
        print(f"[FreeAPI2] Error: {e}")
    return None


# ============================================================================
# MAIN FETCH LOGIC
# ============================================================================

def fetch_tiktok_video(url):
    url = expand_url(url)
    print(f"\n[Fetch] Processing: {url}")
    
    # Check RAM cache (1 hour expiry)
    if url in cache:
        cached = cache[url]
        if time.time() - cached.get("timestamp", 0) < 3600:
            print("[Fetch] RAM cache hit")
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
            print("[Fetch] DB cache hit")
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
        print(f"[Fetch] Cache check error: {e}")
    
    # Try methods in order
    methods = [
        ("yt-dlp", fetch_yt_dlp, "yt_dlp_success", "yt_dlp_fail"),
        ("direct_scrape", fetch_tiktok_api_direct, "scrape_success", "scrape_fail"),
        ("free_api_1", fetch_free_api_1, "api_success", "api_fail"),
        ("free_api_2", fetch_free_api_2, "api_success", "api_fail"),
    ]
    
    for name, method, success_key, fail_key in methods:
        print(f"[Fetch] Trying {name}...")
        result = method(url)
        if result:
            result["original_url"] = url
            print(f"[Fetch] SUCCESS via {name}")
            update_stats(success_key)
            
            cache[url] = {"data": result, "timestamp": time.time()}
            threading.Thread(target=save_cache_db, args=(url, result), daemon=True).start()
            
            return result
        print(f"[Fetch] {name} failed")
        update_stats(fail_key)
    
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
        
        if "tiktok.com" not in url and "vt.tiktok.com" not in url and "vm.tiktok.com" not in url:
            return jsonify({"success": False, "message": "Invalid TikTok URL"}), 400
        
        update_stats("requests", ip)
        
        result = fetch_tiktok_video(url)
        
        print(f"[Download] FETCH RESULT: {result is not None}")
        
        if not result:
            return jsonify({
                "success": False,
                "message": "Unable to fetch video. All methods failed. Please install yt-dlp: pip install yt-dlp"
            }), 503
        
        video_url = result["video_url"]
        title = result.get("title", "")
        author = result.get("author", "")
        thumbnail = result.get("thumbnail", "")
        original_url = result.get("original_url", url)
        source = result.get("source", "unknown")
        
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
        print(f"[Download] ERROR: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({
            "success": False,
            "message": "Server error occurred"
        }), 500


@app.route("/file")
def serve_file():
    video_url = request.args.get("url")
    video_id = request.args.get("videoId")
    mode = request.args.get("mode", "preview")
    
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
        range_header = request.headers.get("Range")
        source_headers = {}
        if range_header:
            source_headers["Range"] = range_header
        
        r = session.get(video_url, stream=True, timeout=20, headers=source_headers)
        
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
            'attachment; filename="' + filename + '"' if mode == "download"
            else 'inline; filename="' + filename + '"'
        )
        headers["Content-Disposition"] = disposition
        
        def generate():
            for chunk in r.iter_content(chunk_size=65536):
                if chunk:
                    yield chunk
        
        return Response(generate(), status=status_code, headers=headers)
        
    except Exception as e:
        print(f"[Serve] ERROR: {e}")
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
        print(f"[Stats] ERROR: {e}")
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
        
        for key in ["requests", "downloads", "cache_hits", "videos_served",
                    "yt_dlp_success", "yt_dlp_fail", "scrape_success", "scrape_fail",
                    "api_success", "api_fail"]:
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
    methods = {
        "yt_dlp": False,
        "yt_dlp_version": None,
        "direct_scrape": True,
        "free_apis": True,
    }
    
    try:
        import yt_dlp
        methods["yt_dlp"] = True
        methods["yt_dlp_version"] = yt_dlp.version.__version__
    except ImportError:
        pass
    
    return jsonify({
        "success": True,
        "methods_available": methods,
        "message": "Install yt-dlp for best results: pip install yt-dlp"
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, threaded=True)
