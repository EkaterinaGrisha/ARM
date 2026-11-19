"""Designations of specification rows checked against the designations of the kit (ADR-0020).

The designations are invented ones of the look of the synthetic kits, never the customer's.
"""

import pytest
from kdcheck.schemas import DocumentInfo, Extracted, SourceRef, Specification, SpecRow
from kdcheck.tree.rows import check_first_uses, check_rows

KIT = {"ВЕДС.732211.001", "ВЕДС.711141.001", "ТЕХМ.305312.000 СБ", "ТЕХМ.305312.100", "ТЕХМ.305312.100-01"}


def _check(settings, eskd, reading: str, *, section: str = "Детали", method: str = "ocr", kit=KIT):
    value = eskd.normalize_designation(reading) or reading
    source = SourceRef(document_id="s", page=1, bbox=(0.1, 0.2, 0.3, 0.25), text=reading, method=method)
    row = SpecRow(id="s-r1", section=section, position=Extracted(value="3", raw="3"))
    row.designation = Extracted(value=value, raw=reading, source=source)
    info = DocumentInfo(id="s", file_name="s.jpg", path="s.jpg", kind="specification")
    changed = check_rows(
        [(info, Specification(document_id="s", rows=[row]))], set(kit), eskd, settings.dictionaries.row_designations
    )
    return row.designation, info.notes, changed


def test_a_reading_that_lost_its_letters_and_dots_gets_the_designation_of_the_kit(settings, eskd):
    ex, notes, changed = _check(settings, eskd, "8ЕДС732211001")
    assert (ex.value, ex.raw, ex.status, changed) == ("ВЕДС.732211.001", "8ЕДС732211001", "conflict", 1)
    assert ex.source.bbox == (0.1, 0.2, 0.3, 0.25) and ex.source.method == "ocr"  # the cell stays the evidence
    assert notes == ["Спецификация, строка поз. 3: прочитано «8ЕДС732211001», в комплекте «ВЕДС.732211.001»"]


def test_a_dropped_digit_is_forgiven_a_digit_read_as_another_is_not(settings, eskd):
    assert _check(settings, eskd, "ВЕДС71141001")[0].value == "ВЕДС.711141.001"  # a thin «1» lost
    ex, notes, changed = _check(settings, eskd, "ВЕДС711142001")
    assert (ex.value, ex.status, notes, changed) == ("ВЕДС.711142.001", None, [], 0)  # as the grammar reads it


def test_a_document_missing_from_the_kit_stays_missing(settings, eskd):
    """Its neighbour differs by one digit: the row keeps its reading, and the check reports the missing drawing."""
    ex, notes, changed = _check(settings, eskd, "ТЕХМ.305312.103")
    assert (ex.value, ex.status, changed) == ("ТЕХМ.305312.103", None, 0)


def test_digits_of_two_designations_of_the_kit_name_neither(settings, eskd):
    kit = KIT | {"ВГДЕ.732211.001"}
    ex, _, changed = _check(settings, eskd, "8ЕДС732211001", kit=kit)
    assert (ex.value, changed) == ("8ЕДС732211001", 0)


def test_a_designation_with_other_letters_but_the_digits_of_the_kit_is_its_own(settings, eskd):
    assert _check(settings, eskd, "ПЕДС.732211.001")[0].value == "ВЕДС.732211.001"


@pytest.mark.parametrize(
    ("reading", "section", "want"),
    [
        ("ТЕХМ305312000", "Документация", "ТЕХМ.305312.000 СБ"),  # a document row names the document
        ("ТЕХМ305312000", "Сборочные единицы", "ТЕХМ.305312.000"),  # an assembly row names the item
        ("ТЕХМ305312000 СБ", "Сборочные единицы", "ТЕХМ.305312.000 СБ"),  # the code read is kept
        ("ТЕХМ30531210001", "Детали", "ТЕХМ.305312.100-01"),  # an execution
    ],
)
def test_the_code_of_a_document_follows_the_reading_or_the_section(settings, eskd, reading, section, want):
    assert _check(settings, eskd, reading, section=section)[0].value == want


@pytest.mark.parametrize(("reading", "method"), [("1937", "ocr"), ("АРХИВ", "ocr"), ("8ЕДС732211001", "text_layer")])
def test_rows_left_as_read(settings, eskd, reading, method):
    """Too few digits name nothing; the text layer is read as written."""
    ex, notes, changed = _check(settings, eskd, reading, method=method)
    assert (ex.status, notes, changed) == (None, [], 0)


def test_a_row_already_in_the_kit_is_not_touched(settings, eskd):
    ex, notes, changed = _check(settings, eskd, "ТЕХМ.305312.100")
    assert (ex.value, ex.status, notes, changed) == ("ТЕХМ.305312.100", None, [], 0)


def test_a_row_of_documentation_without_its_document_in_the_kit_is_left_alone(settings, eskd):
    """The assembly drawing is missing, its specification is not: the row of «Документация» must not become the item,
    or the missing drawing would be lost."""
    kit = {"ПМЛС.303411.002", "ПМЛС.732211.002"}
    ex, notes, changed = _check(settings, eskd, "ПМЛС303411002", section="Документация", kit=kit)
    assert (ex.status, notes, changed) == (None, [], 0)
    ex, notes, changed = _check(settings, eskd, "ПМЛС303411002СБ", section="Документация", kit=kit)
    assert (ex.value, ex.status, changed) == ("ПМЛС.303411.002 СБ", None, 0)  # the code the grammar found stays


def _first_use(settings, eskd, reading: str, *, method: str = "ocr"):
    source = SourceRef(document_id="d", page=1, bbox=(0.02, 0.1, 0.04, 0.3), text=reading, method=method)
    ex = Extracted(value=eskd.normalize_designation(reading) or reading, raw=reading, source=source)
    info = DocumentInfo(id="d", file_name="d.jpg", path="d.jpg", kind="part_drawing")
    changed = check_first_uses([(info, ex)], set(KIT), eskd, settings.dictionaries.row_designations)
    return ex, info.notes, changed


def test_first_use_read_with_other_letters_gets_the_assembly_of_the_kit(settings, eskd):
    """A small «П» of the binding field turned on its side PP-OCRv5 reads «Р»; the digits stay."""
    ex, notes, changed = _first_use(settings, eskd, "РЕХМ.305312.100")
    assert (ex.value, ex.status, changed) == ("ТЕХМ.305312.100", "conflict", 1)
    assert notes == ["Перв. примен.: прочитано «РЕХМ.305312.100», в комплекте «ТЕХМ.305312.100»"]


@pytest.mark.parametrize(
    ("reading", "method"),
    [
        ("АБВГ.123456.001", "ocr"),  # an item of another product
        ("РЕХМ.305312.100", "text_layer"),  # the text layer is read as written
        ("ТЕХМ.305312.103", "ocr"),  # a digit read as another is never forgiven
    ],
)
def test_first_use_left_as_read(settings, eskd, reading, method):
    ex, notes, changed = _first_use(settings, eskd, reading, method=method)
    assert (ex.status, notes, changed) == (None, [], 0)
