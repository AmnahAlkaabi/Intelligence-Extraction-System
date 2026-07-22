# syntax=docker/dockerfile:1
FROM python:3.11-slim AS base

# tesseract-ocr: local OCR engine. libgl1/libglib2.0-0: required by docling's
# image-processing deps (opencv). All installed from the base image's apt
# mirror at BUILD time on a connected machine; the resulting image is then
# transferred whole into the air-gapped environment (docker save/load).
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-eng \
    libgl1 \
    libglib2.0-0 \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Pre-download model weights into the image so the air-gapped container
# never needs network access at runtime. Swap the model name below if you
# change EMBEDDING_MODEL_NAME in .env — it must match at build time.
ARG EMBEDDING_MODEL_NAME=BAAI/bge-large-en-v1.5
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('${EMBEDDING_MODEL_NAME}')"

# Pre-fetch Docling's layout/OCR model artifacts for the same reason.
RUN python -c "from docling.document_converter import DocumentConverter; DocumentConverter()" || true

COPY app ./app

ENV PYTHONUNBUFFERED=1
EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
