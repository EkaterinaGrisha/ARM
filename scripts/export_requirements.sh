#!/usr/bin/env sh
# Regenerate backend/requirements.txt from uv.lock. The Dockerfile and pip users install from it.
set -eu
uv export --frozen --package arm-web --all-extras --no-dev --no-hashes --no-emit-workspace --quiet \
  --output-file backend/requirements.txt
