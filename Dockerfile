# ---- 前端：React + Vite + DaisyUI ----
FROM node:22-alpine AS web

WORKDIR /app/web
COPY web/package.json web/pnpm-lock.yaml ./
RUN npm install -g pnpm && pnpm install && pnpm run build

# ---- 后端 ----
FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml README.md LICENSE sources.example.yaml ./
COPY feicms ./feicms
COPY --from=web /app/web/dist ./web/dist

RUN pip install --no-cache-dir -e .

ENV FEICMS_HOST=0.0.0.0
ENV FEICMS_PORT=8000
ENV FEICMS_DB=sqlite+aiosqlite:///data/feicms.db

RUN mkdir -p data

EXPOSE 8000

CMD ["sh", "-c", "python -m feicms.cli init-db && python -m feicms.cli seed && uvicorn feicms.api:app --host 0.0.0.0 --port 8000"]
