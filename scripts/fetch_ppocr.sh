#!/usr/bin/env sh
# Make models/ppocr (or $ARM_MODELS_DIR/ppocr): the PP-OCRv5 recognizer of configs/models.yaml, converted to ONNX
# (ADR-0024). PaddlePaddle and paddle2onnx run in a temporary environment; the project does not depend on them.
# paddle2onnx imports packaging and setuptools without declaring them.
set -eu
uv run --isolated --no-project --python 3.11 \
  --with paddlepaddle==3.3.1 --with paddle2onnx==2.1.0 \
  --with packaging==26.3 --with setuptools==84.2.1 --with pyyaml==6.0.2 \
  python scripts/fetch_ppocr.py "$@"
