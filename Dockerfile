# Chess Companion: adaptive Stockfish opponent + spoken coach, one container.
#   docker compose up -d        (see README "Run with Docker")
FROM python:3.12-slim

# Stockfish from Debian (CPU only, works on amd64 and arm64)
RUN apt-get update \
 && apt-get install -y --no-install-recommends stockfish \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY server/ server/
COPY web/ web/
COPY run.py .

RUN useradd --system --uid 10001 --home /data chess && mkdir -p /data && chown chess /data
USER chess

ENV CC_DATA=/data \
    CC_STOCKFISH=/usr/games/stockfish \
    CC_HOST=0.0.0.0 \
    CC_PORT=8765 \
    CC_SERVE_WEB=1 \
    CC_AI_PROVIDERS=openrouter \
    CC_AI_PROFILES=all \
    PYTHONUNBUFFERED=1
VOLUME /data
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8765/health',timeout=4).status==200 else 1)"
CMD ["python", "run.py"]
