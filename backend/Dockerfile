# Manthan RAG — one image for the API (uvicorn) and the ingestion worker (Celery).
# docker-compose.yml picks the command per service.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/models \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1

# libpq5 + curl: database access and health checks.
# libgl1 / libglib2.0-0 / libgomp1: runtime libraries for docling's layout and table models.
# poppler-utils: PDF backend. tesseract-ocr(-eng): OCR for scanned pages and images.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 curl libgl1 libglib2.0-0 libgomp1 poppler-utils tesseract-ocr tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

# torch + torchvision together from the CPU index (a mismatched torchvision fails at import
# with "operator torchvision::nms does not exist").
COPY requirements.txt /tmp/requirements.txt
RUN pip install torch==2.5.1 torchvision==0.20.1 --index-url https://download.pytorch.org/whl/cpu \
    && pip install -r /tmp/requirements.txt

RUN useradd --create-home --uid 10001 app
WORKDIR /app
COPY --chown=app:app . /app
# Model caches and file storage are volumes (see docker-compose.yml).
RUN mkdir -p /models /app/docling_models /app/local_storage && chown -R app:app /models /app/docling_models /app/local_storage
USER app

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=5 \
    CMD curl -fs http://localhost:8000/api/v1/health || exit 1
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
