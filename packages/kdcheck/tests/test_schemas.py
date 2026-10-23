import json
from pathlib import Path

import pytest
from kdcheck.schemas import SCHEMA_VERSION, Discrepancy, DocumentInfo, Report, report_json_schema
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


def test_unknown_fields_are_rejected_and_extensions_take_pipeline_data():
    data = json.loads(DEMO_REPORT.read_text(encoding="utf-8"))
    data["documents"][0]["page_total"] = 3  # a typo, or data of another pipeline
    with pytest.raises(ValidationError, match="page_total"):
        Report.model_validate(data)
    del data["documents"][0]["page_total"]
    data["extensions"] = {"team2": {"model": "qwen-vl", "pages": [1, 2]}}
    assert Report.model_validate(data).extensions["team2"]["model"] == "qwen-vl"


def test_reports_of_earlier_versions_stay_valid():
    data = json.loads(DEMO_REPORT.read_text(encoding="utf-8"))
    data["schema_version"] = "1.0"
    for key in ("engines", "config_sha256"):
        data["kit"].pop(key, None)
    for item in data["discrepancies"]:
        item.pop("rule_id", None)
    for doc in data["documents"]:
        for page in doc["pages"]:
            page.pop("format", None)
            page.pop("problem", None)
    data.pop("extensions", None)
    assert Report.model_validate(data).schema_version == "1.0"


def test_rule_id_follows_the_naming_rule():
    found = {"id": "x1", "type": "name_mismatch", "title": "—", "message": "—"}
    assert Discrepancy(**found, rule_id="B1.NAME.001").rule_id == "B1.NAME.001"
    with pytest.raises(ValidationError, match="rule_id"):
        Discrepancy(**found, rule_id="name-1")
