# ---- stage 1: build the web UI -------------------------------------------
FROM node:20-alpine AS ui
WORKDIR /ui
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---- stage 2: API server -------------------------------------------------
FROM python:3.12-slim AS app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY famas ./famas
RUN pip install ".[openai,anthropic,postgres]"
COPY --from=ui /ui/dist ./frontend/dist
RUN useradd --create-home famas && mkdir -p /app/data && chown -R famas /app/data
USER famas
ENV FAMAS_FRONTEND_DIST=/app/frontend/dist \
    FAMAS_DATABASE_URL=sqlite:////app/data/famas.db
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health')"
CMD ["famas", "serve", "--host", "0.0.0.0", "--port", "8000"]
