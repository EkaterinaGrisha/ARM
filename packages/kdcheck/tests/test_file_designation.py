"""The designation of a title block decided against the designation of its file name (ADR-0022).

The designations are invented ones of the look of the synthetic kits, never the customer's.
"""

import pytest
from kdcheck.normalize.file_designation import reconcile
from kdcheck.schemas import Extracted, SourceRef

CELL = (0.7, 0.9, 0.95, 0.93)


def _read(value: str, method: str = "vlm") -> Extracted:
    source = SourceRef(document_id="d", page=1, bbox=CELL, text=value, method=method)
    return Extracted(value=value, raw=value, source=source)


def _decide(settings, value: str, named: str, method: str = "vlm"):
    read = _read(value, method)
    note = reconcile(read, named, settings.dictionaries.file_designations)
    return read, note


def test_a_letter_misread_by_the_model_takes_the_designation_of_the_file_name(settings):
    read, note = _decide(settings, "КТПМ.381146.000", "КТРМ.381146.000")
    assert (read.value, read.raw, read.status) == ("КТРМ.381146.000", "КТПМ.381146.000", "conflict")
    assert read.source.bbox == CELL and read.source.method == "vlm"  # the cell stays the evidence
    assert note == (
        "Основная надпись, «Обозначение документа»: прочитано «КТПМ.381146.000», "
        "в имени файла «КТРМ.381146.000»; в отчёте — «КТРМ.381146.000»"
    )


def test_a_dropped_digit_is_forgiven_a_digit_read_as_another_is_not(settings):
    assert _decide(settings, "ВЕДС.71141.001", "ВЕДС.711141.001", "ocr")[0].value == "ВЕДС.711141.001"
    read, note = _decide(settings, "ВЕДС.711142.001", "ВЕДС.711141.001", "ocr")
    assert (read.value, read.status) == ("ВЕДС.711142.001", "conflict")  # the file may be named wrong
    assert note == "Обозначение в имени файла (ВЕДС.711141.001) отличается от основной надписи (ВЕДС.711142.001)"


def test_the_text_layer_is_the_documents_own(settings):
    """A vector title block is read as written: a file named otherwise is a conflict, not a misread."""
    read, note = _decide(settings, "КТПМ.381146.000", "КТРМ.381146.000", "text_layer")
    assert (read.value, read.status) == ("КТПМ.381146.000", "conflict")
    assert note is not None and note.startswith("Обозначение в имени файла")


@pytest.mark.parametrize(
    ("value", "named", "want"),
    [
        ("КТПМ.381146.000 СБ", "КТРМ.381146.000", "КТРМ.381146.000 СБ"),  # the code read is kept
        ("КТПМ.381146.000", "КТРМ.381146.000 СБ", "КТРМ.381146.000 СБ"),  # the code of the file name
        ("КТПМ.381146.100-01", "КТРМ.381146.100-01", "КТРМ.381146.100-01"),  # an execution
    ],
)
def test_the_code_of_a_document_follows_the_file_name_or_the_reading(settings, value, named, want):
    assert _decide(settings, value, named)[0].value == want


@pytest.mark.parametrize(
    ("value", "named"), [("КТРМ.381146.000", "КТРМ.381146.000"), ("КТРМ.381146.000 СБ", "КТРМ.381146.000")]
)
def test_a_file_name_that_agrees_changes_nothing(settings, value, named):
    read, note = _decide(settings, value, named)
    assert (read.value, read.status, note) == (value, None, None)


def test_a_code_missing_from_the_reading_is_only_noted(settings):
    read, note = _decide(settings, "КТРМ.381146.000", "КТРМ.381146.000 СБ")
    assert (read.value, read.status) == ("КТРМ.381146.000", None)
    assert note == "Обозначение в имени файла (КТРМ.381146.000 СБ) отличается от основной надписи (КТРМ.381146.000)"


def test_a_file_name_with_too_few_digits_decides_nothing(settings):
    read, _ = _decide(settings, "АБ.12.34", "АВ.12.34")
    assert (read.value, read.status) == ("АБ.12.34", "conflict")
