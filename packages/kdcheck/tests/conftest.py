"""Shared fixtures: the committed settings with OCR off, so results do not depend on Tesseract being installed."""

import pytest
from kdcheck.config import Settings, load_settings
from kdcheck.normalize.designations import Designations


@pytest.fixture(scope="session")
def settings() -> Settings:
    return load_settings(use_ocr=False)


@pytest.fixture(scope="session")
def eskd(settings: Settings) -> Designations:
    return Designations(settings.dictionaries)
