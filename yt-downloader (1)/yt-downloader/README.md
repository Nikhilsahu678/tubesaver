# TubeSaver — Educational YouTube Downloader

A small web app that downloads single YouTube videos (MP4) or extracts audio
(MP3 / M4A) **for personal, educational and fair-use purposes only**.

![stack](https://img.shields.io/badge/stack-Flask%20%2B%20yt--dlp%20%2B%20ffmpeg-ff2d4d)

> ⚠️ **Educational use only.** Please respect copyright and YouTube's Terms of
> Service. Only download content you have the right to download (your own
> videos, Creative-Commons / royalty-free material, or anything you're
> permitted to keep for personal study).

---

## Features

- 🔍 Paste a YouTube link → instant metadata (title, thumbnail, uploader,
  duration, views, description)
- 🎬 Download video as MP4 — quality picker (360p → 4K + "best available")
- 🎧 Extract audio as **MP3 (192 kbps)**, **M4A**, or original quality
- 📊 Live progress bar with speed / ETA, then a direct "Save file" link
- 🕘 In-app history of your recent downloads
- 🛡 Friendly error messages (private videos, age-restriction, captcha checks,
  live streams, playlists…)

## How it works

```
Browser UI  ──▶  Flask API  ──▶  yt-dlp  ──▶  ffmpeg (merge / convert)
   ▲                                        │
   └────────  progress polling  ◀───────────┘
```

- `POST /api/info` — reads video metadata and lists available formats
- `POST /api/download` — starts a background download job
- `GET  /api/progress/<job_id>` — job status/percent/speed/ETA
- `GET  /api/file/<job_id>` — downloads the finished file
- `GET  /api/history` — recent completed jobs

## Run it

```bash
pip install -r requirements.txt
python app.py            # → http://0.0.0.0:5000
```

For production use a proper server instead of the Flask dev server:

```bash
gunicorn --bind 0.0.0.0:$PORT --workers 1 --threads 8 --timeout 120 app:app
```

(`workers 1` keeps the in-memory job store consistent; threads handle
concurrency.)

---

## Deploy it online (free) — and fix "file doesn't save on my device"

The preview inside this chat runs in a **sandboxed iframe**, which can block
file downloads. Once the app is hosted on a real domain and you open it in a
normal browser tab, **"Save file" (and auto-save) downloads straight to your
device's Downloads folder** — no sandbox, no blocks.

The project ships with a `Dockerfile` and a `render.yaml`, so deployment is a
few clicks on any container platform.

### Option A — Render.com (recommended, free)

1. Push this folder to a **GitHub repository** (e.g. `tubesaver`).
2. Go to [render.com](https://render.com) → **New → Web Service**.
3. Connect your GitHub repo. Render auto-detects the `Dockerfile`.
4. Name it `tubesaver`, pick the **Free** plan → **Deploy**.
5. Done — you get a public URL like `https://tubesaver.onrender.com`.
6. Open it in a normal tab and download away; files land in your Downloads.

> Free-tier notes: the service **sleeps after ~15 min of inactivity** and takes
> ~1 min to wake on the first request. (Or use the `render.yaml` blueprint:
> New → Blueprint → select repo.)

### Option B — Hugging Face Spaces (free)

1. Create a new **Docker Space** at [huggingface.co/new-space](https://huggingface.co/new-space)
   (SDK: **Docker**, license: pick any).
2. Push this folder to the Space repo (`git clone` the space, copy files in,
   commit, push — the `Dockerfile` is already set up for Spaces' port 7860).
3. Wait for the build, then open `https://<your-name>-tubesaver.hf.space`.

### Option C — any container host (Railway, Fly.io, Fly Machines…)

Point it at this repo / `Dockerfile`. Set env `PORT` if the platform doesn't
inject it.

### Platform notes

- **YouTube may occasionally show a captcha** to datacenter IPs (that's a
  YouTube-side anti-abuse measure, not a bug). The app shows a clear message
  when it happens — retry later or use a video that's not blocked.
- Keep the app **educational-use only** — it already carries that notice in the
  UI and in the footer.

---

## Configuration (environment variables)

| Variable            | Default                | Meaning                                  |
|---------------------|------------------------|------------------------------------------|
| `PORT`              | `5000`                 | HTTP port                                |
| `YTDL_DOWNLOAD_DIR` | `/tmp/ytdl/downloads`  | Where finished files are stored (served for download, not kept in your project) |

## Where do the files go?

Two places, with a clear split:

| Copy | Location | Purpose |
|------|----------|---------|
| **Your device** | Your browser's downloads folder (e.g. `C:\Users\You\Downloads`, `~/Downloads`) | The file you actually keep — saved when you click **Save file** (or auto-saved when a download completes) |
| **Server staging** | `/tmp/ytdl/downloads` (set via `YTDL_DOWNLOAD_DIR`) | Temporary staging so the web app can serve the file to you — like a CDN. May be cleared when the server/sandbox restarts |

The copy on your device is the permanent one. If you run the app on your own
machine (`python app.py`), the server staging is local to that machine too.

## Notes & limitations

- **Single videos only** — playlists and live streams are rejected with a
  friendly message.
- Downloads are **capped at 4 hours** of video length to keep things sane.
- Files are stored outside the project directory and served to your browser;
  they are not written into your workspace.
- YouTube occasionally rate-limits datacenter IPs with a captcha check —
  the app reports this clearly when it happens; retry later or try another video.
- Merging high-quality video + audio requires ffmpeg (bundled automatically).

## Disclaimer

This tool is for **education, research and personal study**. Downloading or
redistributing copyrighted content without permission may violate copyright
law and YouTube's Terms of Service. You are responsible for how you use it.
