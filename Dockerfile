# Stage 1: Build frontend
FROM node:26-alpine AS frontend-build
ARG VERSION=dev
ENV VITE_APP_VERSION=$VERSION
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# Stage 2: Runtime
FROM python:3.14-slim
WORKDIR /app

COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./
COPY --from=frontend-build /app/frontend/dist ./static
# pushdeer.py loads the frontend i18n JSON at runtime so push messages stay
# in sync with the UI. Copy the source JSON next to the backend.
COPY frontend/src/locales ./locales

ENV HOST=0.0.0.0
ENV PORT=8500
ENV YUKETANG_STORE_DIR=/data

VOLUME /data
EXPOSE 8500

# `exec sh -c` so uvicorn replaces sh as PID 1 → signals (docker stop) propagate.
CMD ["sh", "-c", "exec uvicorn main:app --host $HOST --port $PORT"]
