# Веб-интерфейс + API + базовый конвейер (с OCR) в одном образе.
#   docker build -t arm-kd .
#   docker run -p 127.0.0.1:8000:8000 -v arm-data:/data arm-kd   (the API has no login: keep the port local)
FROM node:22-slim AS frontend
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# The PP-OCRv5 recognizer of cells (ADR-0024), converted to ONNX: PaddlePaddle has Linux wheels for x86-64 only, and
# the ONNX file is the same on any platform
FROM --platform=linux/amd64 python:3.11-slim AS models
RUN pip install --no-cache-dir paddlepaddle==3.3.1 paddle2onnx==2.1.0 packaging==26.3 setuptools==84.2.1 pyyaml==6.0.2
COPY configs/models.yaml /app/configs/models.yaml
COPY scripts/fetch_ppocr.py /app/scripts/fetch_ppocr.py
RUN ARM_CONFIG_DIR=/app/configs ARM_MODELS_DIR=/app/models python /app/scripts/fetch_ppocr.py

FROM python:3.12-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-rus fonts-dejavu-core \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /app/backend
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY packages/kdcheck /app/packages/kdcheck
RUN pip install --no-cache-dir --no-deps /app/packages/kdcheck
# Thresholds, templates, dictionaries and limits (ADR-0012); mount another folder over it to change them
COPY configs/ /app/configs/
# Versioned prompts of the vision-language model (ADR-0014)
COPY prompts/ /app/prompts/
COPY --from=models /app/models /app/models
COPY backend/ ./
COPY --from=frontend /app/frontend/dist /app/frontend/dist
ENV ARM_DATA_DIR=/data \
    ARM_CONFIG_DIR=/app/configs \
    ARM_PROMPTS_DIR=/app/prompts \
    ARM_MODELS_DIR=/app/models \
    ARM_FRONTEND_DIST=/app/frontend/dist \
    PYTHONUNBUFFERED=1
VOLUME /data
EXPOSE 8000
CMD ["python", "-m", "arm_web", "serve", "--host", "0.0.0.0", "--port", "8000"]
