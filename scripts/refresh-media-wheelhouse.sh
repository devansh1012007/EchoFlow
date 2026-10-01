#!/bin/bash
# Add the pinned Python 3.11 media-worker dependencies to an existing
# wheelhouse. The wheelhouse is intentionally gitignored, so a fresh VPS must
# run this once before building the all-in-one media image.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
WHEELHOUSE_DIR="${PROJECT_DIR}/wheelhouse"

mkdir -p "${WHEELHOUSE_DIR}"

docker run --rm \
    -v "${PROJECT_DIR}/requirements-base.txt:/req/requirements-base.txt:ro" \
    -v "${PROJECT_DIR}/requirements-media.txt:/req/requirements-media.txt:ro" \
    -v "${PROJECT_DIR}/constraints.txt:/req/constraints.txt:ro" \
    -v "${WHEELHOUSE_DIR}:/out" \
    python:3.11-slim-bookworm sh -ceu '
        pip wheel --no-deps -w /out "dj-rest-auth==7.2.0"
        pip download --prefer-binary --retries 10 --timeout 120 \
            --extra-index-url https://download.pytorch.org/whl/cpu \
            -c /req/constraints.txt \
            -r /req/requirements-media.txt \
            -d /out
        pip install --dry-run --no-index --find-links=/out \
            -c /req/constraints.txt \
            -r /req/requirements-media.txt
    '

echo "Media wheelhouse is complete: ${WHEELHOUSE_DIR}"
