# Веб-интерфейс + API + базовый конвейер (с OCR) в одном образе.
#   docker build -t arm-kd .
#   docker run -p 8000:8000 -v arm-data:/data arm-kd
FROM node:22-slim AS frontend
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-rus fonts-dejavu-core \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /app/backend
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY packages/kdcheck /app/packages/kdcheck
RUN pip install --no-cache-dir --no-deps /app/packages/kdcheck
COPY backend/ ./
COPY --from=frontend /app/frontend/dist /app/frontend/dist
ENV ARM_DATA_DIR=/data \
    ARM_FRONTEND_DIST=/app/frontend/dist \
    PYTHONUNBUFFERED=1
VOLUME /data
EXPOSE 8000
CMD ["python", "-m", "arm_web", "serve", "--host", "0.0.0.0", "--port", "8000"]
