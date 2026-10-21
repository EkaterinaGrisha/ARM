"""Title block and specification extraction on the vector documents of the committed demo kit."""

import json
from pathlib import Path

import pytest
from kdcheck.extract import specification, title_block
from kdcheck.ingest.document import load_document
from kdcheck.schemas import Extracted

KIT = Path(__file__).resolve().parents[3] / "samples" / "demo_kit"
TRUTH = json.loads((KIT.parent / "demo_kit_truth.json").read_text(encoding="utf-8"))["documents"]
DRAWINGS = sorted(p for p, t in TRUTH.items() if t["kind"] != "specification" and t.get("origin") == "vector")
SPECS = sorted(p for p, t in TRUTH.items() if t["kind"] == "specification")
FIELDS = ("designation", "name", "material", "mass", "scale", "litera", "revision", "archive_number", "first_use")


def _text(ex: Extracted | None) -> str | None:
    return None if ex is None or ex.value is None else str(ex.value)


def _quantity(ex: Extracted | None) -> float | None:
    return None if ex is None or ex.value is None else float(ex.value)


@pytest.mark.parametrize("path", DRAWINGS)
def test_title_block_of_vector_drawing(path):
    doc = load_document(KIT / path, KIT, use_ocr=False)
    page = doc.pages[0]
    geom = title_block.locate(page)
    assert geom is not None and geom.form == 1
    tb = title_block.parse(doc, page, geom)
    assert {f: _text(getattr(tb, f)) for f in FIELDS} == {f: TRUTH[path][f] for f in FIELDS}


@pytest.mark.parametrize("path", SPECS)
def test_specification_rows(path):
    doc = load_document(KIT / path, KIT, use_ocr=False)
    assert specification.is_specification(doc.pages[0])
    spec = specification.parse(doc)
    assert (_text(spec.designation), _text(spec.name)) == (TRUTH[path]["designation"], TRUTH[path]["name"])
    got = [(r.section, _text(r.designation), _text(r.name), _quantity(r.quantity)) for r in spec.rows]
    want = [
        (
            r["section"],
            r["designation"] or None,
            r["name"] or None,
            float(r["qty"].replace(",", ".")) if r["qty"] else None,
        )
        for r in TRUTH[path]["rows"]
    ]
    assert got == want


def test_foreign_document_has_no_title_block_or_spec_table():
    doc = load_document(KIT / "Акт приёмки КД.pdf", KIT, use_ocr=False)
    page = doc.pages[0]
    assert page.lines
    assert title_block.locate(page) is None
    assert not specification.is_specification(page)
