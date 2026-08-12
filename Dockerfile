# TubeSaver — deployable container image
# Works on Render, Hugging Face Spaces (Docker), Railway, Fly.io, etc.
FROM python:3.12-slim

# ffmpeg is bundled via imageio-ffmpeg (no system install needed), but the
# system copy is installed too as a belt-and-suspenders fallback.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .
COPY templates ./templates

# Render injects PORT at runtime; HF Spaces uses 7860. This default covers
# everything (runtime env vars override Dockerfile ENV).
ENV PORT=7860
EXPOSE 7860

# Single worker keeps the in-memory job store consistent (progress polling
# must reach the worker that started the job); threads serve concurrent users.
CMD ["sh", "-c", "gunicorn --bind 0.0.0.0:${PORT:-7860} --workers 1 --threads 8 --timeout 120 app:app"]
