"""The human-readable report forms keep values from uploaded files as text."""

import io
import re
import zipfile

from kdcheck.report import to_html, to_xlsx
from kdcheck.schemas import DocumentInfo, KitInfo, Report

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


def test_html_escapes_values_from_uploaded_files():
    page = to_html(_report().model_copy(update={"kit": KitInfo(id="k1", name="<script>alert(1)</script>")}))
    assert "<script>alert(1)" not in page and "&lt;script&gt;alert(1)" in page
