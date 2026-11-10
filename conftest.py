"""Every test reads the cells of scans with Tesseract, as CI does without the files of the PP-OCRv5 recognizer: the
models folder of the run is empty. A test of the recognizer asks for its files with the ``ppocr_models`` fixture and is
skipped without them (ADR-0024). Jobs the tests start in other processes inherit the empty folder."""

import os
from pathlib import Path

import pytest

MODELS = Path(os.environ.get("ARM_MODELS_DIR") or Path(__file__).resolve().parent / "models")


def pytest_configure(config: pytest.Config) -> None:
    os.environ["ARM_MODELS_DIR"] = str(Path(__file__).resolve().parent / ".pytest-no-models")


@pytest.fixture
def ppocr_models(monkeypatch: pytest.MonkeyPatch) -> Path:
    """The models folder with the files of the recognizer, made by scripts/fetch_ppocr.sh."""
    if not any((MODELS / "ppocr").glob("*.onnx")):
        pytest.skip("нет models/ppocr: sh scripts/fetch_ppocr.sh")
    monkeypatch.setenv("ARM_MODELS_DIR", str(MODELS))
    return MODELS
