#!/usr/bin/env bash
set -euo pipefail

# Builds (or pulls) every image Data Loom needs and bundles them, plus a
# clean copy of this repo's tracked source, into ONE archive that needs
# nothing but Docker to run -- no internet required on the target
# machine. This automates the manual steps documented in README.md under
# "Building for an air-gapped environment"; run this on a machine WITH
# internet access, then carry the resulting archive to the air-gapped one.
#
# Usage:
#   ./scripts/package-airgapped.sh [build|pull]
#     build (default) -- builds the backend/frontend images locally.
#                         Needs internet for pip/npm packages and the BGE/
#                         Docling model weights (see README's corporate-
#                         proxy and model-vendoring sections if that
#                         download is unreliable on your network).
#     pull             -- skips building; pulls the images already built
#                          and published to GHCR by CI instead. Faster,
#                          but requires `docker login ghcr.io` first if
#                          the package is private.
#
# Env vars:
#   OUT_DIR       where to write the archive (default: ./dist)
#   ARCHIVE_NAME  output filename (default: data-loom-airgapped-package.tar.gz)

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

MODE="${1:-build}"
OUT_DIR="${OUT_DIR:-$REPO_ROOT/dist}"
ARCHIVE_NAME="${ARCHIVE_NAME:-data-loom-airgapped-package.tar.gz}"
IMAGE_TAR="data-loom-images.tar"
NEO4J_IMAGE="neo4j:5.26-community"

if ! command -v docker >/dev/null 2>&1; then
  echo "Error: docker is not installed or not on PATH." >&2
  exit 1
fi
if ! docker info >/dev/null 2>&1; then
  echo "Error: the Docker daemon isn't reachable (is Docker Desktop / the docker service running?)." >&2
  exit 1
fi

case "$MODE" in
  build)
    echo "==> Building backend + frontend images locally (needs internet)"
    docker compose build
    ;;
  pull)
    echo "==> Pulling pre-built backend + frontend images from GHCR (needs internet)"
    docker compose pull backend frontend
    ;;
  *)
    echo "Usage: $0 [build|pull]" >&2
    exit 1
    ;;
esac

echo "==> Pulling Neo4j ($NEO4J_IMAGE)"
docker pull "$NEO4J_IMAGE"

BACKEND_IMAGE="$(docker compose config --images backend)"
FRONTEND_IMAGE="$(docker compose config --images frontend)"
echo "==> Images to package:"
echo "      backend:  $BACKEND_IMAGE"
echo "      frontend: $FRONTEND_IMAGE"
echo "      neo4j:    $NEO4J_IMAGE"

mkdir -p "$OUT_DIR"
STAGE="$OUT_DIR/data-loom-package"
rm -rf "$STAGE"
mkdir -p "$STAGE"

echo "==> Copying this repo's tracked source into the package (git archive -- no build artifacts, no local .env)"
git archive --format=tar HEAD | tar -x -C "$STAGE"

echo "==> Saving images to $STAGE/$IMAGE_TAR -- this is the slow, multi-GB step, be patient"
docker save "$BACKEND_IMAGE" "$FRONTEND_IMAGE" "$NEO4J_IMAGE" -o "$STAGE/$IMAGE_TAR"

cp "$REPO_ROOT/scripts/run-airgapped.sh" "$STAGE/run-airgapped.sh"
chmod +x "$STAGE/run-airgapped.sh"

cat > "$STAGE/PACKAGE_README.txt" <<'EOF'
Data Loom -- air-gapped package
================================

Everything needed to run Data Loom on a machine with Docker installed
and NO internet connection: the source code, and the backend/frontend/
Neo4j images already baked into data-loom-images.tar in this folder.

1. Copy this whole folder to the target machine (USB, secure file
   transfer, whatever your air-gapped process requires).
2. cp backend/.env.example backend/.env, then edit it with your real
   on-prem Qwen/Kimi2 endpoints and a real NEO4J_PASSWORD.
3. Run ./run-airgapped.sh
4. Open http://<host>:8080

Nothing in these steps touches the network -- run-airgapped.sh only
`docker load`s the bundled tarball and starts the stack with
`--pull never`, so it can never fall back to fetching anything online.

See README.md in this folder for full configuration details (which
role -> which model, corporate-proxy notes, extending file-type support,
etc) -- this file only covers getting it running.
EOF

echo "==> Compressing into $OUT_DIR/$ARCHIVE_NAME"
# -C "$OUT_DIR" <basename> (not "-C $STAGE .") so the archive extracts
# into its own named top-level folder instead of dumping everything
# straight into whatever directory it's unpacked in.
tar -czf "$OUT_DIR/$ARCHIVE_NAME" -C "$OUT_DIR" "$(basename "$STAGE")"
rm -rf "$STAGE"

echo ""
echo "==> Done: $OUT_DIR/$ARCHIVE_NAME"
echo "    Transfer this single file to the air-gapped machine, extract it, and follow PACKAGE_README.txt inside."
