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
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Optional: trust a corporate TLS-inspecting proxy's root CA (Zscaler,
# Netskope, Fortinet, etc). If your network re-signs outbound HTTPS with a
# private root certificate — common on managed corporate laptops — Windows
# trusts it (IT pushed it via policy) but this minimal Linux image has no
# idea it exists, causing SSL_CERT_VERIFY_FAILED on any HTTPS call the proxy
# intercepts (PyPI, Hugging Face, etc), even though the connection itself
# succeeds. Export your corporate root CA as a base64 .crt file and drop it
# into backend/certs/ before building — see README for exact steps. If you
# don't have this issue, leave the folder empty; this is then a no-op.
COPY certs/ /usr/local/share/ca-certificates/
RUN update-ca-certificates
ENV REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt
ENV SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# huggingface_hub defaults to its newer "Xet" storage backend for large
# files, which downloads from cas-server.xethub.hf.co instead of
# huggingface.co. Some networks/firewalls allow the main HF domain but not
# that separate CDN host, which fails model downloads below with a
# ConnectionError even though general HF connectivity is fine. Forcing the
# classic HTTP download path avoids that extra host dependency entirely.
# HF_HUB_DISABLE_XET is the documented switch, but it's had reliability
# issues in some huggingface_hub versions, so the hf_xet package (if pulled
# in transitively) is also removed as a belt-and-suspenders fix — with it
# gone, huggingface_hub can't use the Xet path even if the env var is
# ignored.
ENV HF_HUB_DISABLE_XET=1
RUN pip uninstall -y hf_xet || true

# Pre-download model weights into the image so the air-gapped container
# never needs network access at runtime. Swap the model name below if you
# change EMBEDDING_MODEL_NAME in .env — it must match at build time.
ARG EMBEDDING_MODEL_NAME=BAAI/bge-large-en-v1.5
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('${EMBEDDING_MODEL_NAME}')"

# Pre-fetch Docling's layout-detection model artifacts (downloaded from HF
# Hub on first use) into the image, mirroring the exact pipeline config used
# at runtime (app/parsers/pdf_parser.py) so this also warms/validates the
# RapidOCR ONNX session. RapidOCR's own detection/recognition/classification
# models ship inside the rapidocr-onnxruntime wheel already installed above,
# so this step triggers no network call for the OCR side — only for Docling's
# layout model.
RUN python -c "\
from docling.datamodel.base_models import InputFormat; \
from docling.datamodel.pipeline_options import PdfPipelineOptions, RapidOcrOptions; \
from docling.document_converter import DocumentConverter, PdfFormatOption; \
opts = PdfPipelineOptions(); \
opts.ocr_options = RapidOcrOptions(); \
DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)})" || true

COPY app ./app

ENV PYTHONUNBUFFERED=1
EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
