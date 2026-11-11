"""The human-readable report forms keep values from uploaded files as text."""

import csv
import io
import re
import zipfile

from kdcheck.report import to_csv, to_html, to_xlsx
from kdcheck.schemas import DocumentInfo, KitInfo, Parameter, PartRecord, Report

FORMULA = '=HYPERLINK("http://example.invalid","открыть")'


def _report() -> Report:
    doc = DocumentInfo(id="d1", file_name=f"{FORMULA}.pdf", path=f"{FORMULA}.pdf", status="damaged", problem=FORMULA)
    return Report(kit=KitInfo(id="k1", name=FORMULA), documents=[doc])


def test_excel_keeps_a_value_that_looks_like_a_formula_as_text():
    with zipfile.ZipFile(io.BytesIO(to_xlsx(_report()))) as book:
        sheets = [book.read(n).decode("utf-8") for n in book.namelist() if n.startswith("xl/worksheets/sheet")]
        strings = book.read("xl/sharedStrings.xml").decode("utf-8") if "xl/sharedStrings.xml" in book.namelist() else ""
    assert sheets and not any(re.search(r"<f[ >]", s) for s in sheets)
    assert "HYPERLINK" in strings + "".join(sheets)


def test_csv_holds_a_row_for_each_parameter_with_its_deviations_and_no_formulas():
    fit = Parameter(
        id="p1",
        category="dimension_tolerance",
        name="Ø40H7",
        text=FORMULA,
        value=40.0,
        value_min=40.0,
        value_max=40.025,
        lower_deviation=0.0,
        upper_deviation=0.025,
        unit="мм",
        qualifier="H7",
        it_grade=7,
    )
    part = PartRecord(document_id="d1", designation="АБВГ.715211.001", name="Вал", parameters=[fit])
    text = to_csv(_report().model_copy(update={"parts": [part]}))
    assert text.startswith("\ufeff")
    header, row = list(csv.reader(text[1:].splitlines()))
    values = dict(zip(header, row, strict=True))
    assert (values["Параметр"], values["Нижн. откл."], values["Верхн. откл."], values["IT"]) == (
        "Ø40H7",
        "0.0",
        "0.025",
        "7",
    )
    assert values["Исходный текст"] == "'" + FORMULA  # a spreadsheet would run it


def test_html_escapes_values_from_uploaded_files():
    page = to_html(_report().model_copy(update={"kit": KitInfo(id="k1", name="<script>alert(1)</script>")}))
    assert "<script>alert(1)" not in page and "&lt;script&gt;alert(1)" in page
