# Stage 1: Build frontend
FROM node:20-alpine AS frontend-build
ARG VERSION=dev
ENV VITE_APP_VERSION=$VERSION
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# Stage 2: Runtime
FROM python:3.12-slim
WORKDIR /app

COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./
COPY --from=frontend-build /app/frontend/dist ./static

ENV HOST=0.0.0.0
ENV PORT=8500
ENV YUKETANG_STORE_DIR=/data

VOLUME /data
EXPOSE 8500

# `exec sh -c` so uvicorn replaces sh as PID 1 → signals (docker stop) propagate.
CMD ["sh", "-c", "exec uvicorn main:app --host $HOST --port $PORT"]
