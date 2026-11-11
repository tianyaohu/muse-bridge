FROM python:3.12-slim

RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin app \
    && mkdir -p /data/inbox && chown -R app:app /data

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY muse_bridge ./muse_bridge
COPY server.py deploy/docker/entrypoint.py ./

ENV MUSE_BRIDGE_HOST=0.0.0.0 \
    MUSE_BRIDGE_PORT=8080 \
    MUSE_BRIDGE_INBOX=/data/inbox \
    MUSE_BRIDGE_DATA_DIR=/data \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=4).read()"]

ENTRYPOINT ["python", "/app/entrypoint.py"]
