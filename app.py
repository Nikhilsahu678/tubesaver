#!/usr/bin/env python3
"""
TubeSaver — an educational YouTube downloader.

Downloads single YouTube videos (as MP4 video or as audio) for personal,
educational and fair-use purposes. Respect copyright and YouTube's Terms
of Service: only download content you have the right to download.
"""

import glob
import os
import re
import threading
import time
import uuid

import imageio_ffmpeg
import yt_dlp
from flask import Flask, jsonify, render_template, request, send_file

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
FFMPEG_BIN = imageio_ffmpeg.get_ffmpeg_exe()
FFMPEG_DIR = os.path.dirname(FFMPEG_BIN)
# yt-dlp looks for a binary literally named `ffmpeg` (or `ffmpeg.exe`) in the
# given location — the imageio bundle ships with a versioned filename, so we
# expose it under the expected name via a symlink.
if os.path.basename(FFMPEG_BIN) not in ("ffmpeg", "ffmpeg.exe"):
    _bin_dir = "/tmp/ytdl/bin"
    os.makedirs(_bin_dir, exist_ok=True)
    _link = os.path.join(_bin_dir, "ffmpeg")
    if not os.path.exists(_link):
        os.symlink(FFMPEG_BIN, _link)
    FFMPEG_DIR = _bin_dir
# Keep downloads outside the workspace so they never bloat your project files.
DOWNLOAD_DIR = os.environ.get("YTDL_DOWNLOAD_DIR", "/tmp/ytdl/downloads")
MAX_DURATION = 4 * 60 * 60          # refuse videos longer than 4 hours
SOCKET_TIMEOUT = 30                 # seconds
MAX_HISTORY = 15                    # entries kept in the in-app history list
MAX_JOBS = 200                      # jobs kept in memory (then pruned)

os.makedirs(DOWNLOAD_DIR, exist_ok=True)

app = Flask(__name__)
app.config["JSON_SORT_KEYS"] = False


# ---------------------------------------------------------------------------
# CORS — the live preview runs in a sandboxed iframe (opaque origin), so the
# browser treats every fetch() as cross-origin. Allow it explicitly.
# ---------------------------------------------------------------------------
@app.after_request
def add_cors_headers(resp):
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return resp


@app.before_request
def handle_preflight():
    if request.method == "OPTIONS":
        return ("", 204)

URL_RE = re.compile(
    r"^(?:https?://)?(?:www\.|m\.|music\.)?(?:youtube\.com|youtu\.be)/",
    re.IGNORECASE,
)
PLAYLIST_RE = re.compile(r"/(?:playlist|playlists|mix)(?:$|[/?])", re.IGNORECASE)

# Strict whitelist of the format strings this app can generate (no injection).
VIDEO_FMT_RE = re.compile(
    r"^(?:bestvideo\+bestaudio/best"
    r"|bestvideo\[height<=\d+\]\[ext=mp4\]\+bestaudio\[ext=m4a\]"
    r"/best\[height<=\d+\]\[ext=mp4\]/best)$"
)
AUDIO_FMT_RE = re.compile(r"^bestaudio/best$")
AUDIO_CODECS = ("mp3", "m4a", "orig")

JOBS = {}               # job_id -> job dict
LOCK = threading.Lock()
HISTORY = []            # most recent completed jobs


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def fmt_duration(seconds):
    if not seconds:
        return "—"
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def fmt_size(num):
    if not num:
        return None
    value = float(num)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024:
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"


def fmt_views(n):
    return f"{n:,}" if n else None


def humanize_error(exc):
    """Turn raw yt-dlp errors into friendly, human-readable messages."""
    msg = str(exc)
    low = msg.lower()
    if "sign in to confirm" in low or "bot" in low or "recaptcha" in low:
        return ("YouTube asked for a captcha / bot-check from this network. "
                "Try again in a moment or use a different video.")
    if "private" in low:
        return "This video is private — only its owner can watch it."
    if "age" in low or "nsfw" in low:
        return "This video is age-restricted and cannot be downloaded with this tool."
    if "unavailable" in low or "does not exist" in low or "not found" in low:
        return "This video is unavailable or has been removed."
    if "live" in low:
        return "This appears to be a live stream, which this app does not download."
    if "unsupported url" in low or "no video" in low:
        return "That URL is not supported by this app. Use a youtube.com or youtu.be link."
    if "timed out" in low or "timeout" in low:
        return "The request timed out — please try again."
    if "unable to extract" in low:
        return "Could not read the video page. It may be region-locked or restricted."
    return msg[:400]


def build_options(formats):
    """Build the quality choices offered in the UI."""
    heights = sorted(
        {f.get("height") for f in formats
         if f.get("height") and f.get("vcodec") not in (None, "none")},
        reverse=True,
    )
    videos = [
        {"label": "Best available quality",
         "format_str": "bestvideo+bestaudio/best"}
    ]
    for h in heights:
        if h < 360:
            continue
        tag = " • 4K" if h == 2160 else (" • 2K" if h == 1440
              else " • Full HD" if h == 1080 else " • HD" if h == 720 else "")
        videos.append({
            "label": f"{h}p{tag}",
            "format_str": (f"bestvideo[height<={h}][ext=mp4]"
                           f"+bestaudio[ext=m4a]/best[height<={h}][ext=mp4]/best"),
        })
    audios = [
        {"label": "MP3 (192 kbps)", "format_str": "bestaudio/best", "audio_fmt": "mp3"},
        {"label": "M4A (lossy, widely compatible)", "format_str": "bestaudio/best", "audio_fmt": "m4a"},
        {"label": "Original audio (best quality)", "format_str": "bestaudio/best", "audio_fmt": "orig"},
    ]
    return videos, audios


def build_download_opts(job):
    # A short job tag makes the output filename unique per job, so concurrent
    # downloads of the same video never collide or confuse file discovery.
    tag = job["job_id"][:8]
    opts = {
        "format": job["format_str"],
        "outtmpl": os.path.join(
            DOWNLOAD_DIR,
            f"%(title).100s [%(id)s] {tag}.%(ext)s",
        ),
        "restrictfilenames": True,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "ffmpeg_location": FFMPEG_DIR,
        "socket_timeout": SOCKET_TIMEOUT,
        "retries": 3,
        "fragment_retries": 3,
        "concurrent_fragment_downloads": 4,
        "progress_hooks": [lambda d: progress_hook(job, d)],
        "postprocessor_hooks": [lambda d: pp_hook(job, d)],
    }
    if job["kind"] == "video":
        opts["merge_output_format"] = "mp4"
    elif job["audio_fmt"] == "mp3":
        opts["postprocessors"] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }]
    elif job["audio_fmt"] == "m4a":
        opts["postprocessors"] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "m4a",
        }]
    return opts


def progress_hook(job, d):
    status = d.get("status")
    if status == "downloading":
        job["status"] = "downloading"
        total = d.get("total_bytes") or d.get("total_bytes_estimate")
        dl = d.get("downloaded_bytes") or 0
        job["downloaded"] = dl
        job["total"] = total
        job["percent"] = round(dl / total * 100, 1) if total else None
        job["speed"] = d.get("speed")
        job["eta"] = d.get("eta")
    elif status == "finished":
        job["status"] = "processing"
        job["percent"] = 100
        job["speed"] = None
        job["eta"] = None


def pp_hook(job, d):
    if d.get("status") == "started":
        job["status"] = "processing"
        job["pp_step"] = str(d.get("postprocessor") or "").split("(")[0]


def run_job(job):
    """Background worker: run yt-dlp, then locate the finished file."""
    try:
        with yt_dlp.YoutubeDL(build_download_opts(job)) as yd:
            info = yd.extract_info(job["url"], download=True)
        job["title"] = info.get("title") or job.get("title") or "video"

        video_id = info.get("id")
        tag = job["job_id"][:8]
        matches = []
        if tag:
            for p in glob.glob(os.path.join(DOWNLOAD_DIR, f"*{tag}*")):
                base = os.path.basename(p)
                if base.endswith((".part", ".ytdl")):
                    continue
                if re.search(r"\.f\d+\.", base):        # leftover merge fragment
                    continue
                matches.append(p)
        if not matches:
            cands = [os.path.join(DOWNLOAD_DIR, f)
                     for f in os.listdir(DOWNLOAD_DIR)]
            cands = [c for c in cands if os.path.isfile(c)]
            if cands:
                matches = [max(cands, key=os.path.getmtime)]
        if not matches:
            raise RuntimeError("The download finished but no file was produced.")

        path = max(matches, key=os.path.getmtime)
        job["path"] = path
        job["basename"] = os.path.basename(path)
        job["size"] = os.path.getsize(path)
        job["status"] = "done"
        job["done_at"] = time.time()
        with LOCK:
            HISTORY.insert(0, {
                "job_id": job["job_id"],
                "title": job["title"],
                "basename": job["basename"],
                "size": job["size"],
                "kind": job["kind"],
                "done_at": job["done_at"],
            })
            del HISTORY[MAX_HISTORY:]
    except Exception as exc:                            # noqa: BLE001
        job["status"] = "error"
        job["error"] = humanize_error(exc)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.get("/")
def index():
    return render_template("index.html")


@app.post("/api/info")
def api_info():
    payload = request.get_json(silent=True) or {}
    url = (payload.get("url") or "").strip()
    if not url:
        return jsonify(error="Paste a YouTube link first."), 400
    if not URL_RE.match(url):
        return jsonify(error="That doesn't look like a YouTube URL (youtube.com or youtu.be)."), 400
    if PLAYLIST_RE.search(url):
        return jsonify(error="This app downloads a single video, not a whole playlist."), 400

    try:
        with yt_dlp.YoutubeDL({
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "noplaylist": True,
            "socket_timeout": SOCKET_TIMEOUT,
            "ffmpeg_location": FFMPEG_DIR,
        }) as yd:
            info = yd.extract_info(url, download=False)
    except Exception as exc:                            # noqa: BLE001
        return jsonify(error=humanize_error(exc)), 422

    if not info or info.get("_type", "video") != "video":
        return jsonify(error="No downloadable video found at that URL."), 422
    if info.get("is_live"):
        return jsonify(error="This is a live stream — live streams are not supported."), 422

    duration = info.get("duration") or 0
    if duration and duration > MAX_DURATION:
        return jsonify(error=(
            f"This video is {fmt_duration(duration)} long — this app limits "
            f"downloads to {fmt_duration(MAX_DURATION)}.")), 422

    videos, audios = build_options(info.get("formats") or [])
    return jsonify({
        "title": info.get("title"),
        "thumbnail": info.get("thumbnail"),
        "uploader": info.get("uploader") or info.get("channel"),
        "duration": fmt_duration(duration),
        "views": fmt_views(info.get("view_count")),
        "upload_date": info.get("upload_date"),
        "description": (info.get("description") or "")[:600],
        "videos": videos,
        "audios": audios,
        "video_id": info.get("id"),
    })


@app.post("/api/download")
def api_download():
    payload = request.get_json(silent=True) or {}
    url = (payload.get("url") or "").strip()
    kind = (payload.get("kind") or "").strip().lower()
    format_str = (payload.get("format_str") or "").strip()
    audio_fmt = (payload.get("audio_fmt") or "orig").strip().lower()

    if not url or not URL_RE.match(url):
        return jsonify(error="Invalid YouTube URL."), 400
    if kind not in ("video", "audio"):
        return jsonify(error="Unknown format kind."), 400
    if kind == "video":
        if not VIDEO_FMT_RE.match(format_str):
            return jsonify(error="Invalid video format."), 400
        audio_fmt = "orig"
    else:
        if not AUDIO_FMT_RE.match(format_str):
            return jsonify(error="Invalid audio format."), 400
        if audio_fmt not in AUDIO_CODECS:
            return jsonify(error="Invalid audio codec."), 400

    job = {
        "job_id": uuid.uuid4().hex,
        "url": url,
        "kind": kind,
        "format_str": format_str,
        "audio_fmt": audio_fmt,
        "title": (payload.get("title") or "video")[:120],
        "status": "queued",
        "percent": 0,
        "speed": None,
        "eta": None,
        "downloaded": 0,
        "total": None,
        "path": None,
        "basename": None,
        "size": None,
        "error": None,
        "created_at": time.time(),
    }
    with LOCK:
        JOBS[job["job_id"]] = job
        # prune old, finished jobs so the table never grows unbounded
        if len(JOBS) > MAX_JOBS:
            for jid in [k for k, v in JOBS.items()
                        if v["status"] in ("done", "error")]:
                if len(JOBS) <= MAX_JOBS:
                    break
                JOBS.pop(jid, None)

    threading.Thread(target=run_job, args=(job,), daemon=True).start()
    return jsonify(job_id=job["job_id"])


@app.get("/api/progress/<job_id>")
def api_progress(job_id):
    job = JOBS.get(job_id)
    if not job:
        return jsonify(error="Unknown download job."), 404
    return jsonify({
        "status": job["status"],
        "percent": job.get("percent"),
        "speed": job.get("speed"),
        "eta": job.get("eta"),
        "downloaded": job.get("downloaded"),
        "total": job.get("total"),
        "title": job.get("title"),
        "error": job.get("error"),
        "filename": job.get("basename"),
        "size": job.get("size"),
    })


@app.get("/api/file/<job_id>")
def api_file(job_id):
    job = JOBS.get(job_id)
    if not job:
        return jsonify(error="Unknown download job."), 404
    if job["status"] != "done" or not job.get("path") or not os.path.isfile(job["path"]):
        return jsonify(error="That file is not ready yet."), 409
    return send_file(job["path"], as_attachment=True, download_name=job["basename"])


@app.get("/api/history")
def api_history():
    with LOCK:
        return jsonify(history=list(HISTORY))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, threaded=True, debug=False)
