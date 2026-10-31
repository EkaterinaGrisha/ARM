"""The boxes of the truth hold the values they name, read back from the PDF by PDFium, not by the core."""

import re

import pypdfium2 as pdfium
import pytest
from synth.truth import Box, DrawingTruth, SpecTruth

from synth import kits


def _text_in(pdf: pdfium.PdfDocument, box: Box) -> str:
    page = pdf[box.page - 1]
    width, height = page.get_size()
    x0, y0, x1, y1 = box.bbox
    text = page.get_textpage().get_text_bounded(
        left=x0 * width, bottom=(1 - y1) * height, right=x1 * width, top=(1 - y0) * height
    )
    return re.sub(r"\s+", " ", text).strip()


@pytest.fixture(scope="module", params=["A", "B"])
def kit(request: pytest.FixtureRequest) -> kits.Kit:
    """A clean kit in each lettering with a drawing of several sheets, a wrapped name, group specifications by both
    variants of GOST 2.113 and a group drawing of a part."""
    for seed in range(1, 120):
        kit = kits.build(seed, [], scan_share=0, raster_share=0)
        truth = kit.truth()
        several = any(isinstance(d, DrawingTruth) and d.sheets > 1 for d in truth.documents.values())
        wrapped = any("\n" in row.name for spec in kit.specs.values() for row in spec.rows)
        groups = {spec.group for spec in kit.specs.values()}
        table = any(d.variants and not d.assembly for d in kit.drawings)
        if kit.font_type == request.param and several and wrapped and {"A", "B"} <= groups and table:
            return kit
    raise AssertionError(f"no kit of type {request.param} has them all")


def test_every_field_box_holds_its_value(kit):
    truth = kit.truth()
    checked = 0
    for path, doc in truth.documents.items():
        pdf = pdfium.PdfDocument(kit.files[path])
        values: dict[str, object] = {
            "designation": doc.designation,
            "name": doc.name,
            "litera": doc.litera,
            "revision": doc.revision,
            "sheets": doc.sheets,
        }
        if isinstance(doc, DrawingTruth):
            values |= {"material": doc.material, "mass": doc.mass, "scale": doc.scale, "first_use": doc.first_use}
        for field, value in values.items():
            if value and field in doc.boxes:
                assert str(value) in _text_in(pdf, doc.boxes[field]), (path, field)
                checked += 1
        if isinstance(doc, SpecTruth):
            for row in doc.rows:
                for column, box in row.boxes.items():
                    if column.startswith("qty:"):
                        value = row.quantities[column.removeprefix("qty:")]
                    else:
                        value = row.written if column == "designation" and row.written else getattr(row, column)
                    assert value in _text_in(pdf, box), (path, column, row.designation)
                    checked += 1
            for variant in doc.variants if doc.group == "A" else []:
                assert variant in _text_in(pdf, doc.boxes[f"variant:{variant}"]), (path, variant)
        else:
            for text, box in zip(doc.requirements, doc.requirement_boxes, strict=True):
                assert box is not None and text[:20] in _text_in(pdf, box), (path, text)
                checked += 1
            for parameter in doc.parameters:
                assert parameter.box is not None and _text_in(pdf, parameter.box), (path, parameter.name)
            for variant in doc.variants:
                values = {"designation": variant.written or variant.designation, "mass": variant.mass, **variant.data}
                assert set(variant.boxes) == set(values), (path, variant.designation)
                for column, value in values.items():
                    assert value in _text_in(pdf, variant.boxes[column]), (path, variant.designation, column)
                    checked += 1
    assert checked > 100


def test_a_drawing_of_several_sheets_has_them_all_and_form_2a_on_the_later_ones(kit):
    truth = kit.truth()
    path, doc = next((p, d) for p, d in truth.documents.items() if isinstance(d, DrawingTruth) and d.sheets > 1)
    pdf = pdfium.PdfDocument(kit.files[path])
    assert len(pdf) == doc.sheets
    assert str(doc.sheets) in _text_in(pdf, doc.boxes["sheets"])
    later = pdf[1].get_textpage().get_text_range()
    assert doc.designation in later and "Лист" in later
    on_later_sheets = [p for p in doc.parameters if p.box and p.box.page > 1]
    assert on_later_sheets


def test_a_dropped_sheet_is_missing_from_the_file_but_counted_in_the_title_block():
    for seed in range(1, 60):
        kit = kits.build(seed, ["drop_sheet"], scan_share=0, raster_share=0)
        if kit.mutations == ["drop_sheet"]:
            break
    truth = kit.truth()
    [expected] = truth.discrepancies
    path, doc = next((p, d) for p, d in truth.documents.items() if d.designation == expected.designation)
    assert expected.type == "sheets_missing" and expected.rule_id is None
    assert len(pdfium.PdfDocument(kit.files[path])) == doc.sheets - 1
    assert all(p.box is None or p.box.page < doc.sheets for p in doc.parameters)


def test_a_long_name_wraps_and_the_truth_joins_its_lines(kit):
    truth = kit.truth()
    for spec in kit.specs.values():
        doc = truth.documents[spec.file]
        for row, truth_row in zip(spec.rows, doc.rows, strict=True):
            if "\n" in row.name:
                assert truth_row.name == row.name.replace("\n", " ")
                box = truth_row.boxes["name"].bbox
                assert box[3] - box[1] > 0.03  # more than one line of 8 mm on an A4 sheet
                return
    raise AssertionError("no wrapped name")


def test_the_pages_are_drawn_in_the_lettering_of_the_kit_with_the_signs_type_b_lacks_from_type_a(kit):
    """Every page names the font of the kit; a type B page that writes «Ø» takes the sign from type A."""
    fonts: set[bytes] = set()
    for path, data in kit.files.items():
        types = set(re.findall(rb"/BaseFont /\w+\+OpenGostType([AB])TT", data))  # the subsets of the fonts
        assert kit.font_type.encode() in types, path
        fonts |= types
    assert fonts == ({b"A", b"B"} if kit.font_type == "B" else {b"A"})
    assert all(d.font_type == kit.font_type for d in kit.truth().documents.values())
