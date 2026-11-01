FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY server.py .
ENV MUSE_BRIDGE_HOST=0.0.0.0 MUSE_BRIDGE_PORT=8080 MUSE_BRIDGE_INBOX=/data/inbox
EXPOSE 8080
CMD ["python", "server.py"]
