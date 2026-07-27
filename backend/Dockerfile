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

# Corporate TLS-inspecting proxies (see the corporate-CA note above) don't
# just fail closed -- they sometimes intermittently corrupt small binary
# responses in transit, which pip catches as a hash mismatch against
# requirements.txt's pinned sha256s. That's treated as fatal, not a network
# error, so pip's own --retries flag never kicks in for it -- the build dies
# outright and previously needed a full manual `docker compose up --build`
# re-run per bad package. `retry` wraps any command in an outer retry loop
# (6 attempts, linear backoff) so a single corrupted download or transient
# network blip self-heals inside the build instead of failing the whole
# thing. Used below for every pip install and every live model download.
RUN printf '%s\n' \
    '#!/bin/sh' \
    'set -e' \
    'attempt=1' \
    'max=6' \
    'until "$@"; do' \
    '  if [ "$attempt" -ge "$max" ]; then' \
    '    echo "retry: giving up after $max attempts: $*" >&2' \
    '    exit 1' \
    '  fi' \
    '  echo "retry: attempt $attempt/$max failed for: $* -- retrying in $((attempt * 5))s" >&2' \
    '  sleep $((attempt * 5))' \
    '  attempt=$((attempt + 1))' \
    'done' \
    > /usr/local/bin/retry && chmod +x /usr/local/bin/retry

# CPU-only torch + torchvision. The default PyPI wheels for linux/amd64 drag
# in the *entire* NVIDIA CUDA stack (nvidia-cublas-cu12, nvidia-cudnn-cu12,
# etc) -- several extra GB -- even though this app only runs on CPU
# (EMBEDDING_DEVICE=cpu). torchvision is pre-installed here too because
# docling hard-depends on easyocr (its *default* OCR engine, even though we
# override it to RapidOCR at runtime -- see pdf_parser.py), and easyocr
# depends on torchvision; without pre-satisfying it from the CPU index,
# pip would still pull the CUDA torchvision build when installing
# requirements.txt below. Installing both from PyTorch's dedicated CPU
# wheel index avoids all of that: smaller download, smaller final image
# (which matters doubly for air-gapped hosts where the image has to be
# saved and transferred), and far less to fail downloading on a slow link.
#
# torch's wheel (174.7 MB) is by far the largest single download in this
# build, and on some corporate networks it comes back with a different
# wrong sha256 on every attempt -- not the same wrong hash each time, which
# would mean a bad/tampered file, but a *different* one every time, which
# means a TLS-inspecting proxy is corrupting the bytes differently on each
# re-interception of this one large, long-lived transfer. HTTP/2 response
# reassembly bugs in exactly this kind of proxy are a well-documented cause.
# pip's own downloader gives no control over the HTTP version it speaks, so
# fetch this one file with curl instead, forced onto HTTP/1.1, and verify
# its sha256 locally before installing -- torchvision (1.8 MB) has never
# shown this problem, so it's left on the normal pip path below.
RUN retry sh -c 'rm -f /tmp/torch.whl && curl -fSL --http1.1 --retry 3 --retry-all-errors \
      -o /tmp/torch.whl "https://download-r2.pytorch.org/whl/cpu/torch-2.5.1%2Bcpu-cp311-cp311-linux_x86_64.whl" \
      && echo "07d7c9e069123d5af08b0cf0013d74f680b2d8be7d9e2cf561a52c90c55d9409  /tmp/torch.whl" | sha256sum -c -' \
    && pip install --no-cache-dir /tmp/torch.whl \
    && rm /tmp/torch.whl
RUN retry pip install --no-cache-dir --timeout 100 --retries 5 \
    torchvision==0.20.1 --index-url https://download.pytorch.org/whl/cpu

# Installed as separate layers (one per requirements/*.txt group) rather
# than a single `-r requirements.txt` so that a mid-install failure -- e.g.
# a corrupted download over an unreliable connection -- only costs a retry
# of that one group. Docker's build cache keeps every group that already
# succeeded, so re-running `docker compose up --build` resumes at the first
# group that hasn't completed yet instead of redownloading everything.
COPY requirements.txt .
COPY requirements/ ./requirements/
RUN retry pip install --no-cache-dir --timeout 100 --retries 5 -r requirements/core.txt
RUN retry pip install --no-cache-dir --timeout 100 --retries 5 -r requirements/llm.txt
RUN retry pip install --no-cache-dir --timeout 100 --retries 5 -r requirements/embeddings.txt
RUN retry pip install --no-cache-dir --timeout 100 --retries 5 -r requirements/graph.txt
RUN retry pip install --no-cache-dir --timeout 100 --retries 5 -r requirements/pdf.txt
RUN retry pip install --no-cache-dir --timeout 100 --retries 5 -r requirements/ocr.txt
RUN retry pip install --no-cache-dir --timeout 100 --retries 5 -r requirements/office.txt
RUN retry pip install --no-cache-dir --timeout 100 --retries 5 -r requirements/web.txt
RUN retry pip install --no-cache-dir --timeout 100 --retries 5 -r requirements/data.txt
RUN retry pip install --no-cache-dir --timeout 100 --retries 5 -r requirements/utils.txt

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
RUN retry python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('${EMBEDDING_MODEL_NAME}')"

# Pre-fetch Docling's layout + table-structure model artifacts (a single HF
# Hub repo, ds4sd/docling-models) into the image, mirroring the exact
# pipeline config used at runtime (app/parsers/pdf_parser.py). RapidOCR's
# own detection/recognition/classification models ship inside the
# rapidocr-onnxruntime wheel already installed above, so this step triggers
# no network call for the OCR side — only for Docling's layout/table model.
#
# IMPORTANT: merely constructing DocumentConverter(...) does NOT download
# anything — docling/document_converter.py builds it as an empty shell
# (self.initialized_pipelines = {}) and only downloads model weights
# lazily, inside StandardPdfPipeline.__init__ -> download_models_hf(),
# the first time a real conversion actually runs a PDF/image through that
# pipeline. An earlier version of this step called only the bare
# constructor, which built and shipped successfully every time while
# silently caching nothing — the exact failure mode a user hit here
# (a live snapshot_download() attempt from inside pdf_parser.py's
# .convert() call, on a host with no route to huggingface.co) was masked
# until someone's first real PDF upload, on a container that could be
# months old. Call .initialize_pipeline(...) explicitly below so this step
# does what its name says: it forces the same model download the real
# pipeline needs, during the build, where a failure is loud and fixable —
# not on a user's first PDF upload on an already-shipped air-gapped host.
#
# No `|| true`: if this download fails, the image must NOT ship anyway and
# silently defer the failure to that first PDF upload. Let the build fail
# here, loudly, on a connected machine where it's actually fixable (see
# the corporate-CA note above).
RUN retry python -c "\
from docling.datamodel.base_models import InputFormat; \
from docling.datamodel.pipeline_options import PdfPipelineOptions, RapidOcrOptions; \
from docling.document_converter import DocumentConverter, PdfFormatOption; \
opts = PdfPipelineOptions(); \
opts.ocr_options = RapidOcrOptions(); \
conv = DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)}); \
conv.initialize_pipeline(InputFormat.PDF); \
print('Docling model artifacts cached OK.')"

# Force offline mode from here on, for every process the final image runs.
# Set only *after* the model pre-fetch steps above (which still need
# network to populate the cache) -- once set, huggingface_hub/transformers
# will never attempt a live download again, no matter what. A cache miss
# then fails immediately with a clear local "not found, offline mode"
# error instead of a slow, confusing live attempt against a CDN the
# air-gapped host was never supposed to be able to reach in the first
# place.
ENV HF_HUB_OFFLINE=1
ENV TRANSFORMERS_OFFLINE=1

COPY app ./app

ENV PYTHONUNBUFFERED=1
EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
