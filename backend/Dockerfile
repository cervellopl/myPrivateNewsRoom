FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY newsroom ./newsroom
COPY plugins_available ./plugins_available

ENV NEWSROOM_DATA_DIR=/data \
    NEWSROOM_PLUGIN_DIR=/data/plugins \
    NEWSROOM_POLL_INTERVAL=900
VOLUME ["/data"]
EXPOSE 8000

CMD ["python", "-m", "newsroom", "--host", "0.0.0.0", "--port", "8000"]
