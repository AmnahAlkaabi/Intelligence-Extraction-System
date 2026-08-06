#!/usr/bin/env bash
set -euo pipefail

# Run this from inside an extracted air-gapped package (the folder
# produced by scripts/package-airgapped.sh, containing
# data-loom-images.tar). Loads the bundled images and starts the stack.
# Never touches the network -- this is the whole point of the package.

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

IMAGE_TAR="data-loom-images.tar"

if ! command -v docker >/dev/null 2>&1; then
  echo "Error: docker is not installed or not on PATH." >&2
  exit 1
fi
if ! docker info >/dev/null 2>&1; then
  echo "Error: the Docker daemon isn't reachable (is Docker Desktop / the docker service running?)." >&2
  exit 1
fi
if [ ! -f "$IMAGE_TAR" ]; then
  echo "Error: $IMAGE_TAR not found in $DIR." >&2
  echo "Run this script from inside the extracted package, not a copy of just this file." >&2
  exit 1
fi

if [ ! -f backend/.env ]; then
  echo "==> No backend/.env found -- creating one from the template."
  cp backend/.env.example backend/.env
  echo ""
  echo "Edit backend/.env now: set QWEN_BASE_URL / KIMI_BASE_URL to your real on-prem"
  echo "endpoints and NEO4J_PASSWORD to a real password. Then run this script again."
  exit 0
fi

echo "==> Loading images from $IMAGE_TAR (no network used -- this reads the file already on disk)"
docker load -i "$IMAGE_TAR"

echo "==> Starting the stack"
# --pull never: use only the images just loaded. Without this flag,
# `docker compose up` can try to pull from GHCR since docker-compose.yml
# configures both `image:` and `build:` for backend/frontend -- exactly
# the wrong thing to attempt with no internet connection.
docker compose up -d --pull never

echo "==> Waiting for the backend to report healthy (up to ~60s on first start)..."
BACKEND_UP=0
for _ in $(seq 1 30); do
  if docker compose exec -T backend python -c "
import urllib.request
urllib.request.urlopen('http://localhost:8000/api/health', timeout=2)
" >/dev/null 2>&1; then
    BACKEND_UP=1
    break
  fi
  sleep 2
done

echo ""
if [ "$BACKEND_UP" = "1" ]; then
  echo "Data Loom is up:"
else
  echo "Backend didn't report healthy within the wait window -- check its logs:"
  echo "  docker compose logs -f backend"
  echo "It may still be starting (BGE model load can take a bit on first run). Once up:"
fi
echo "  App:            http://localhost:8080"
echo "  API:            http://localhost:8000/api"
echo "  Neo4j Browser:  http://localhost:7474"
