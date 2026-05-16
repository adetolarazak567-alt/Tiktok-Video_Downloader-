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
import json

load_dotenv()

from flask import Flask, request, jsonify, Response
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

# ===== SESSION SETUP (OPTIMIZED) =====
session = requests.Session()

from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

session.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"})

retry = Retry(
    total=3,
    backoff_factor=0.5,
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=["HEAD", "GET", "POST", "OPTIONS"]
)

adapter = HTTPAdapter(
    max_retries=retry,
    pool_connections=100,
    pool_maxsize=100,
    pool_block=False
)

session.mount("http://", adapter)
session.mount("https://", adapter)

# ===== SQLITE DATABASE =====
conn = sqlite3.connect("stats.db", check_same_thread=False)
c = conn.cursor()

c.execute('''
CREATE TABLE IF NOT EXISTS stats (
    key TEXT PRIMARY KEY,
    value INTEGER
)
''')

for key in ["requests", "downloads", "cache_hits", "videos_served"]:
    c.execute("INSERT OR IGNORE INTO stats (key,value) VALUES (?,?)", (key,0))

conn.commit()

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

# ===== RAM CACHE =====
cache = {}
metadata_cache = {}

# ===== HELPERS =====
def clean_filename(text):
    if not text:
        text = "ToolifyX Downloader"
    text = re.sub(r'[\\/*?:"<>|]', "", text)
    text = re.sub(r'\s+', " ", text).strip()
    return text[:120]

def random_string(length=6):
    return ''.join(random.choices(string.ascii_letters + string.digits, k=length))

# ===== EXPAND SHORT TIKTOK LINKS =====
def expand_url(url):
    try:
        if "vt.tiktok.com" in url or "vm.tiktok.com" in url or "t.tiktok.com" in url:
            r = session.head(url, allow_redirects=True, timeout=8)
            return r.url
    except Exception as e:
        print("Expand URL error:", e)
    return url

# ===== TIKTOK METADATA EXTRACTION =====
def extract_video_id(url):
    """Extract video ID from TikTok URL"""
    patterns = [
        r'tiktok\.com/@[\w.]+/video/(\d+)',
        r'tiktok\.com/t/(\w+)',
        r'vm\.tiktok\.com/\w+',
        r'vt\.tiktok\.com/\w+',
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None

def fetch_tiktok_metadata(url):
    """Fetch video metadata (title, author, thumbnail) from TikTok page"""
    try:
        # Try to get metadata from the page
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
        }
        
        r = session.get(url, headers=headers, timeout=10, allow_redirects=True)
        
        if r.status_code != 200:
            return None
            
        html = r.text
        
        # Try to extract from JSON-LD
        json_ld_match = re.search(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', html, re.DOTALL)
        if json_ld_match:
            try:
                data = json.loads(json_ld_match.group(1))
                if isinstance(data, dict):
                    return {
                        "title": data.get("name", data.get("description", ""))[:200],
                        "author": data.get("author", {}).get("name", "")[:100] if isinstance(data.get("author"), dict) else "",
                        "thumbnail": data.get("thumbnailUrl", data.get("image", "")),
                    }
            except json.JSONDecodeError:
                pass
        
        # Try to extract from meta tags
        title_match = re.search(r'<meta[^>]*property="og:title"[^>]*content="([^"]*)"', html)
        desc_match = re.search(r'<meta[^>]*property="og:description"[^>]*content="([^"]*)"', html)
        author_match = re.search(r'<meta[^>]*property="og:author"[^>]*content="([^"]*)"', html)
        thumb_match = re.search(r'<meta[^>]*property="og:image"[^>]*content="([^"]*)"', html)
        
        title = title_match.group(1) if title_match else (desc_match.group(1) if desc_match else "")
        author = author_match.group(1) if author_match else ""
        thumbnail = thumb_match.group(1) if thumb_match else ""
        
        # Clean up title
        title = re.sub(r' on TikTok$', '', title)
        title = re.sub(r' \| TikTok$', '', title)
        
        if title or author:
            return {
                "title": title[:200],
                "author": author[:100],
                "thumbnail": thumbnail,
            }
            
    except Exception as e:
        print("Metadata fetch error:", e)
    
    return None

# ===== API FETCHERS =====
def fetch_tikwm(url):
    try:
        res = session.post(
            "https://www.tikwm.com/api/",
            data={"url": url, "hd": "1"},
            timeout=8,
            headers={"Content-Type": "application/x-www-form-urlencoded"}
        )
        if res.status_code == 200:
            data = res.json()
            video = data.get("data", {}).get("play")
            if video:
                # Also try to get metadata from tikwm response
                meta = data.get("data", {})
                return {
                    "video_url": video,
                    "title": meta.get("title", ""),
                    "author": meta.get("author", {}).get("nickname", "") if isinstance(meta.get("author"), dict) else "",
                    "thumbnail": meta.get("cover", ""),
                }
    except Exception as e:
        print("tikwm error:", e)
    return None


def fetch_tikwm_alt(url):
    try:
        res = session.post(
            "https://tikwm.com/api/",
            data={"url": url},
            timeout=8,
            headers={"Content-Type": "application/x-www-form-urlencoded"}
        )
        if res.status_code == 200:
            data = res.json()
            video = data.get("data", {}).get("play")
            if video:
                meta = data.get("data", {})
                return {
                    "video_url": video,
                    "title": meta.get("title", ""),
                    "author": meta.get("author", {}).get("nickname", "") if isinstance(meta.get("author"), dict) else "",
                    "thumbnail": meta.get("cover", ""),
                }
    except Exception as e:
        print("tikwm_alt error:", e)
    return None


def fetch_backup(url):
    try:
        res = session.post(
            "https://api2.musicaldown.com/v2/download",
            data={"url": url},
            timeout=10,
            headers={"Content-Type": "application/x-www-form-urlencoded"}
        )
        if res.status_code == 200:
            data = res.json()
            video_url = data.get("video", {}).get("no_watermark")
            if video_url:
                return {
                    "video_url": video_url,
                    "title": data.get("title", ""),
                    "author": data.get("author", ""),
                    "thumbnail": data.get("thumbnail", ""),
                }
    except Exception as e:
        print("backup error:", e)
    return None

# ===== PARALLEL FETCH (ULTRA FAST) =====
def fetch_tiktok_video(url):
    url = expand_url(url)
    
    # Also fetch metadata in parallel
    metadata = None
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        # Submit video fetchers
        futures = [
            executor.submit(fetch_tikwm, url),
            executor.submit(fetch_tikwm_alt, url),
            executor.submit(fetch_backup, url),
        ]
        
        # Submit metadata fetcher
        meta_future = executor.submit(fetch_tiktok_metadata, url)
        
        # Get first successful video result
        video_result = None
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            if result and result.get("video_url"):
                video_result = result
                break
        
        # Get metadata
        try:
            metadata = meta_future.result(timeout=5)
        except:
            pass
    
    # Merge metadata if video fetcher didn't provide it
    if video_result and metadata:
        if not video_result.get("title") and metadata.get("title"):
            video_result["title"] = metadata["title"]
        if not video_result.get("author") and metadata.get("author"):
            video_result["author"] = metadata["author"]
        if not video_result.get("thumbnail") and metadata.get("thumbnail"):
            video_result["thumbnail"] = metadata["thumbnail"]
    
    return video_result

# ===== SAVE CACHE =====
def save_cache_db(url, video_url, title="", author="", thumbnail=""):
    try:
        conn2 = sqlite3.connect("stats.db")
        c2 = conn2.cursor()

        c2.execute(
            """INSERT OR REPLACE INTO video_cache 
               (url, video_url, title, author, thumbnail, created_at) 
               VALUES (?,?,?,?,?,?)""",
            (url, video_url, title, author, thumbnail, int(time.time()))
        )

        conn2.commit()
        conn2.close()

    except Exception as e:
        print("DB thread error:", e)

# ===== DOWNLOAD ROUTE =====
@app.route("/download", methods=["POST"])
def download_video():

    try:
        data = request.get_json()
        url = data.get("url")
        ip = request.remote_addr

        if not url:
            return jsonify({"success": False, "message": "No URL"}),400

        # Stats update
        try:
            c.execute("UPDATE stats SET value=value+1 WHERE key='requests'")
            c.execute("INSERT OR IGNORE INTO unique_ips (ip) VALUES (?)",(ip,))
            conn.commit()
        except:
            pass

        # RAM cache
        if url in cache:
            cached = cache[url]
            meta = metadata_cache.get(url, {})
            
            base_name = meta.get("title", "ToolifyX Downloader") or "ToolifyX Downloader"
            if meta.get("author"):
                base_name = f"{meta['author']} - {base_name}"
            
            filename = clean_filename(base_name)+"_"+random_string()+".mp4"

            return jsonify({
                "success": True,
                "url": cached,
                "filename": filename,
                "title": meta.get("title", ""),
                "author": meta.get("author", ""),
                "thumbnail": meta.get("thumbnail", ""),
                "cached": True
            })

        # DB cache
        c.execute("SELECT video_url, title, author, thumbnail FROM video_cache WHERE url=?",(url,))
        row = c.fetchone()

        if row:
            video_url, db_title, db_author, db_thumbnail = row
            cache[url] = video_url
            metadata_cache[url] = {
                "title": db_title or "",
                "author": db_author or "",
                "thumbnail": db_thumbnail or ""
            }

            base_name = db_title or "ToolifyX Downloader"
            if db_author:
                base_name = f"{db_author} - {base_name}"
            
            filename = clean_filename(base_name)+"_"+random_string()+".mp4"

            return jsonify({
                "success": True,
                "url": video_url,
                "filename": filename,
                "title": db_title or "",
                "author": db_author or "",
                "thumbnail": db_thumbnail or "",
                "cached": True
            })

        # Fetch
        result = fetch_tiktok_video(url)

        print("FETCH RESULT:", result)

        if not result or not result.get("video_url"):
            return jsonify({"success":False,"message":"Fetch failed"}),500

        video_url = result["video_url"]
        title = result.get("title", "")
        author = result.get("author", "")
        thumbnail = result.get("thumbnail", "")

        # RAM cache
        cache[url] = video_url
        metadata_cache[url] = {
            "title": title,
            "author": author,
            "thumbnail": thumbnail
        }

        # Save DB async
        threading.Thread(
            target=save_cache_db,
            args=(url, video_url, title, author, thumbnail),
            daemon=True
        ).start()

        # Stats
        try:
            c.execute("UPDATE stats SET value=value+1 WHERE key='downloads'")
            c.execute("UPDATE stats SET value=value+1 WHERE key='videos_served'")
            c.execute(
                "INSERT INTO download_logs (ip,url,timestamp) VALUES (?,?,?)",
                (ip,url,int(time.time()))
            )
            conn.commit()
        except:
            pass

        base_name = title or "ToolifyX Downloader"
        if author:
            base_name = f"{author} - {base_name}"
        
        filename = clean_filename(base_name)+"_"+random_string()+".mp4"

        return jsonify({
            "success":True,
            "url":video_url,
            "filename":filename,
            "title": title,
            "author": author,
            "thumbnail": thumbnail,
            "cached": False
        })

    except Exception as e:
        print("CRASH PREVENTED:",e)

        return jsonify({
            "success":False,
            "message":"Server recovered automatically"
        }),500

# ===== FILE SERVING =====
@app.route("/file")
def serve_file():

    video_url = request.args.get("url")
    mode = request.args.get("mode","preview")
    custom_filename = request.args.get("filename", "")

    if not video_url:
        return jsonify({"success":False,"message":"No video URL"}),400

    try:

        r = session.get(video_url,stream=True,timeout=15)

        rand = random_string()

        if custom_filename:
            filename = clean_filename(custom_filename)
            if not filename.endswith(".mp4"):
                filename += ".mp4"
        else:
            filename = f"ToolifyX Downloader-{rand}.mp4"

        file_size = r.headers.get("Content-Length")

        disposition = (
            f'attachment; filename="{filename}"'
            if mode=="download"
            else f'inline; filename="{filename}"'
        )

        headers = {
            "Content-Disposition":disposition,
            "Content-Type":"video/mp4"
        }

        if file_size:
            headers["Content-Length"]=file_size

        return Response(
            r.iter_content(chunk_size=65536),
            headers=headers
        )

    except Exception as e:
        return jsonify({"success":False,"message":str(e)}),500

# ===== STATS =====
@app.route("/stats",methods=["GET"])
def get_stats():

    c.execute("SELECT key,value FROM stats")
    stats_data=dict(c.fetchall())

    c.execute("SELECT COUNT(*) FROM unique_ips")
    unique_ips_count=c.fetchone()[0]

    c.execute("SELECT ip,url,timestamp FROM download_logs")

    logs=[
        {"ip":ip,"url":url,"timestamp":ts}
        for ip,url,ts in c.fetchall()
    ]

    return jsonify({
        **stats_data,
        "unique_ips":unique_ips_count,
        "download_logs":logs
    })

# ===== WAKE =====
@app.route("/wake",methods=["GET"])
def wake():
    return jsonify({
        "success":True,
        "message":"Server is awake"
    })

# ===== ADMIN RESET =====
ADMIN_PASSWORD=os.getenv("ADMIN_PASSWORD")

@app.route("/admin/reset",methods=["POST"])
def reset_stats():

    data=request.get_json()
    password=data.get("password")

    if password!=ADMIN_PASSWORD:
        return jsonify({
            "success":False,
            "message":"Wrong password"
        }),401

    for key in ["requests","downloads","cache_hits","videos_served"]:
        c.execute("UPDATE stats SET value=0 WHERE key=?",(key,))

    c.execute("DELETE FROM unique_ips")
    c.execute("DELETE FROM download_logs")

    conn.commit()

    return jsonify({"success":True})

# ===== START SERVER =====
if __name__=="__main__":
    app.run(
        host="0.0.0.0",
        port=5000,
        threaded=True
    )
