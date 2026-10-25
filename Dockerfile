# syntax=docker/dockerfile:1
# Yaan's API with the built web UI, one image (R2 W6). FalkorDB runs as its own service; the
# connector loop runs from this image as the `worker` service (H1, D108).
#   docker compose --profile app up -d --build
#   docker compose --profile app run --rm api yaan seed --from <snapshot path or URL>
FROM node:24-alpine AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.11-slim AS api
COPY --from=ghcr.io/astral-sh/uv:0.11.16 /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_PROJECT_ENVIRONMENT=/app/.venv UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1 PATH="/app/.venv/bin:$PATH"
COPY backend/pyproject.toml backend/uv.lock backend/
RUN uv sync --project backend --frozen --no-dev --no-install-project
COPY backend/ backend/
RUN uv sync --project backend --frozen --no-dev
COPY exports/ exports/
COPY eval/results/ eval/results/
COPY docs/schema_card.md docs/schema_card.md
COPY connectors.yaml people.yaml ./
COPY --from=web /web/dist frontend/dist
COPY --chmod=755 docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
# Not root (H1, D108): the code and the venv stay root-owned and read-only to the app; it writes
# only to /app/data (the ./data bind mount) and /tmp. Compose may run it as another uid
# (YAAN_UID / YAAN_GID). No .pyc writes, and Hugging Face's cache stays in the data folder.
RUN useradd --system --uid 10001 --user-group --home-dir /app --no-create-home yaan \
    && mkdir -p /app/data && chown yaan:yaan /app/data
ENV FALKORDB_HOST=falkordb PYTHONDONTWRITEBYTECODE=1 HF_HOME=/app/data/hf
USER 10001:10001
EXPOSE 8000
ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--app-dir", "backend", "--host", "0.0.0.0", "--port", "8000"]
