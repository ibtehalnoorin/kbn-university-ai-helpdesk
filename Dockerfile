# --- 1. build the React app -------------------------------------------------
FROM node:20-slim AS frontend
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# --- 2. runtime: API + built frontend on one origin -------------------------
FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    FASTEMBED_CACHE_PATH=/opt/fastembed \
    KBN_HOST=0.0.0.0 \
    KBN_ENVIRONMENT=production
WORKDIR /app

COPY backend/requirements-prod.txt backend/requirements-prod.txt
RUN pip install -r backend/requirements-prod.txt

# Download the embedding model at build time so cold starts do not fetch it.
RUN python -c "from fastembed import TextEmbedding; TextEmbedding('BAAI/bge-small-en-v1.5')"

COPY . .
COPY --from=frontend /app/frontend/dist frontend/dist

RUN useradd --create-home app && chown -R app:app /app /opt/fastembed
USER app

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s \
  CMD python -c "import os,urllib.request;urllib.request.urlopen('http://127.0.0.1:%s/health' % os.environ.get('PORT','8000'),timeout=8)"
# Hosts such as Render provide the port in $PORT.
CMD ["sh", "-c", "KBN_PORT=${PORT:-8000} python backend/run_server.py"]
