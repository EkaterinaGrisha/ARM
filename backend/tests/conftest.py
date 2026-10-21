import json
import os
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
SAMPLES = BACKEND.parent / "samples"

sys.path.insert(0, str(BACKEND))
os.environ.setdefault("ARM_OCR", "0")  # results must not depend on Tesseract being installed


@pytest.fixture(scope="session")
def demo_kit() -> tuple[Path, dict]:
    """The committed demo kit and its ground truth.

    Regenerating the kit here would make the PDFs, and with them the extraction results,
    depend on the fonts installed on the machine.
    """
    truth = json.loads((SAMPLES / "demo_kit_truth.json").read_text(encoding="utf-8"))
    return SAMPLES / "demo_kit", truth


@pytest.fixture(scope="session")
def demo_report(demo_kit):
    from kdcheck.run import BaselinePipeline, load_settings

    root, _ = demo_kit
    return BaselinePipeline(load_settings(use_ocr=False)).run(root, kit_id="test", kit_name="Тест")
