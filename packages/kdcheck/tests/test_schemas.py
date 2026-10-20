import json
from pathlib import Path

import pytest
from kdcheck.schemas import SCHEMA_VERSION, DocumentInfo, Report, report_json_schema
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[3]
SCHEMA_FILE = ROOT / "docs" / "report.schema.json"
DEMO_REPORT = ROOT / "frontend" / "public" / "demo" / "report.json"


def test_committed_schema_matches_models():
    assert SCHEMA_FILE.read_text(encoding="utf-8") == report_json_schema(), (
        "docs/report.schema.json is stale: run sh scripts/export_schema.sh"
    )


def test_demo_report_fixture_matches_contract():
    data = json.loads(DEMO_REPORT.read_text(encoding="utf-8"))
    report = Report.model_validate(data)
    assert report.schema_version == SCHEMA_VERSION
    assert report.documents


def test_unknown_enum_value_is_rejected():
    with pytest.raises(ValidationError):
        DocumentInfo(id="d1", file_name="a.pdf", path="a.pdf", status="lost")  # type: ignore[arg-type]
