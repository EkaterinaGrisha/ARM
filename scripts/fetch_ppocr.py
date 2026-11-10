"""Make the files of the PP-OCRv5 recognizer of ``ocr.ppocr`` in configs/models.yaml (ADR-0024).

The official PaddlePaddle files of the pinned revision are downloaded from Hugging Face and checked against their
SHA-256, then converted to ONNX with paddle2onnx. Written to ``models/ppocr`` of the checkout, or to
``$ARM_MODELS_DIR/ppocr``: ``<model>.onnx`` and ``<model>.json`` with the alphabet of the model. The conversion gives
the same bytes each time; the report names the SHA-256 of the ONNX file.

Run it through scripts/fetch_ppocr.sh: PaddlePaddle and paddle2onnx are needed only here, in a temporary environment,
and are not dependencies of the project.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
FILES = ("inference.json", "inference.pdiparams", "inference.yml")
OPSET = 14  # of ONNX: the recognizer takes lines of any width
TIMEOUT = 300  # s, one file


def main() -> int:
    configs = Path(os.environ.get("ARM_CONFIG_DIR") or ROOT / "configs")
    rules = yaml.safe_load((configs / "models.yaml").read_text(encoding="utf-8"))["ocr"]["ppocr"]
    model = rules["model"]
    target = Path(os.environ.get("ARM_MODELS_DIR") or ROOT / "models") / "ppocr"
    target.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp)
        for name in FILES:
            url = f"https://huggingface.co/{rules['repository']}/resolve/{rules['revision']}/{name}"
            with urllib.request.urlopen(url, timeout=TIMEOUT) as answer:
                data = answer.read()
            digest = hashlib.sha256(data).hexdigest()
            if digest != rules["sha256"][name]:
                print(f"{name}: SHA-256 {digest}, в models.yaml {rules['sha256'][name]}", file=sys.stderr)
                return 1
            (source / name).write_bytes(data)
        onnx = source / f"{model}.onnx"
        converter = shutil.which("paddle2onnx") or "paddle2onnx"
        subprocess.run(
            [
                converter,
                "--model_dir",
                str(source),
                "--model_filename",
                "inference.json",
                "--params_filename",
                "inference.pdiparams",
                "--save_file",
                str(onnx),
                "--opset_version",
                str(OPSET),
            ],
            check=True,
        )
        alphabet = yaml.safe_load((source / "inference.yml").read_text(encoding="utf-8"))["PostProcess"]
        about = {
            "model": model,
            "repository": rules["repository"],
            "revision": rules["revision"],
            "onnx_sha256": hashlib.sha256(onnx.read_bytes()).hexdigest(),
            "alphabet": [str(ch) for ch in alphabet["character_dict"]],
        }
        shutil.copyfile(onnx, target / onnx.name)
    (target / f"{model}.json").write_text(json.dumps(about, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"{target / onnx.name}: SHA-256 {about['onnx_sha256']}, символов {len(about['alphabet'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
