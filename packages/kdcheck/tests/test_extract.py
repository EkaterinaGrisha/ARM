"""Title block and specification extraction on the vector documents of the committed demo kit."""

import json
from pathlib import Path

import pytest
from kdcheck.config import Review
from kdcheck.extract import review, specification, title_block
from kdcheck.ingest.document import load_document
from kdcheck.normalize.designations import Designations
from kdcheck.schemas import Extracted, PartRecord, SourceRef, TitleBlock

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
def test_title_block_of_vector_drawing(path, settings, eskd):
    doc = load_document(KIT / path, KIT, settings)
    page = doc.pages[0]
    geom = title_block.locate(page, settings.forms)
    assert geom is not None and geom.form == 1
    tb = title_block.parse(doc, page, geom, settings.forms, eskd)
    assert {f: _text(getattr(tb, f)) for f in FIELDS} == {f: TRUTH[path][f] for f in FIELDS}


@pytest.mark.parametrize("path", SPECS)
def test_specification_rows(path, settings, eskd):
    doc = load_document(KIT / path, KIT, settings)
    assert specification.is_specification(doc.pages[0], settings.forms)
    spec = specification.parse(doc, settings.forms, eskd)
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


def test_foreign_document_has_no_title_block_or_spec_table(settings):
    doc = load_document(KIT / "Акт приёмки КД.pdf", KIT, settings)
    page = doc.pages[0]
    assert page.lines
    assert title_block.locate(page, settings.forms) is None
    assert not specification.is_specification(page, settings.forms)


def _read(method: str, confidence: float | None, value: str | None = "АРМ.303411.001") -> Extracted:
    return Extracted(value=value, source=SourceRef(document_id="d1", method=method, confidence=confidence))


def test_value_status_follows_the_review_settings(settings):
    rules = settings.ingest.review
    assert review.status(_read("text_layer", 0.95), rules) == "ok"
    assert review.status(_read("text_layer", 0.5), rules) == "low_confidence"  # below min_confidence
    assert review.status(_read("ocr", 0.9), rules) == "low_confidence"  # OCR is not calibrated yet
    assert review.status(_read("filename", None), rules) == "low_confidence"
    assert review.status(_read("text_layer", 0.95, value=None), rules) == "missing"
    calibrated = Review(methods=(), min_confidence=rules.min_confidence)
    assert review.status(_read("ocr", 0.9), calibrated) == "ok"


def test_marking_reaches_nested_values_and_keeps_a_status_set_before(settings):
    conflict = _read("text_layer", 0.95).model_copy(update={"status": "conflict"})
    part = PartRecord(
        document_id="d1",
        title_block=TitleBlock(designation=conflict, name=_read("ocr", 0.9, value="Вал")),
        requirements=[_read("text_layer", 0.95, value="1. *Размеры для справок.")],
    )
    review.mark(part, settings.ingest.review)
    assert [ex.status for ex in review.values(part)] == ["conflict", "low_confidence", "ok"]


@pytest.mark.parametrize(
    ("field", "text", "value"),
    [
        ("scale", "1:2", "1:2"),
        ("scale", "11", "1:1"),  # OCR lost the colon: the one scale of GOST 2.302 these digits spell
        ("scale", "1 2", "1:2"),
        ("scale", "125", "1:25"),
        ("scale", "2,51", "2,5:1"),
        ("scale", "17", None),  # 1:7 and 17:1 are no scales of GOST 2.302
        ("mass", "12,5", "12,5"),
        ("mass", "См. табл.", "См. табл."),  # a group drawing, GOST 2.113
        ("mass", "(м. табл.", "См. табл."),
        ("name", "‚Корпус |", "Корпус"),  # noise and ruled lines at the edges of a recognized cell
        ("material", "Сталь 45 ГОСТ 1050-2013", "Сталь 45 ГОСТ 1050-2013"),
    ],
)
def test_cell_value_by_gost(settings, field, text, value):
    assert title_block.cell_value(field, text, Designations(settings.dictionaries)) == value


def test_section_header_read_with_errors(settings):
    sections = {"детали": "Детали", "стандартныеизделия": "Стандартные изделия"}
    similarity = settings.forms.specification.section_similarity
    assert specification._section("детали", sections, similarity) == "Детали"
    assert specification._section("детолу", sections, similarity) == "Детали"  # OCR of «Детали» in GOST lettering
    assert specification._section("тондортныеизделия", sections, similarity) == "Стандартные изделия"
    assert specification._section("всборе", sections, similarity) is None  # the second line of a name
