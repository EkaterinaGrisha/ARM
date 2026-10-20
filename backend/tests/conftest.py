import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("ARM_OCR", "0")  # результаты тестов не зависят от наличия Tesseract


@pytest.fixture(scope="session")
def demo_kit(tmp_path_factory) -> tuple[Path, dict]:
    from arm_web import demo_kit

    root = tmp_path_factory.mktemp("kit") / "demo_kit"
    truth = demo_kit.build(root)
    return root, truth


@pytest.fixture(scope="session")
def demo_report(demo_kit):
    from arm_web.pipeline.baseline.pipeline import BaselinePipeline

    root, _ = demo_kit
    return BaselinePipeline(use_ocr=False).run(root, kit_id="test", kit_name="Тест")
