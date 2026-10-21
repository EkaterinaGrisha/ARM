import pytest
from kdcheck.config import load_settings
from kdcheck.normalize.designations import Designations

ESKD = Designations(load_settings().dictionaries)


@pytest.mark.parametrize(
    "text, expected",
    [
        ("АРМ.303411.000", "АРМ.303411.000"),
        ("APM.303411.000 СБ", "АРМ.303411.000 СБ"),  # Latin letters instead of Cyrillic
        ("АРМ 303411.201", "АРМ.303411.201"),
        ("АБВГ.123456.789-01", "АБВГ.123456.789-01"),
        ("АРМ.3О3411.0О5", "АРМ.303411.005"),  # letter О instead of zero (OCR error)
        ("Чертёж АРМ.303411.101 Корпус.pdf", "АРМ.303411.101"),
        ("АРМ.И.0412-25", None),  # a change notice number is not a designation
        ("Болт М12×40 ГОСТ 7798-70", None),
        ("", None),
    ],
)
def test_normalize_designation(text, expected):
    assert ESKD.normalize_designation(text) == expected


def test_find_designations_with_assembly_code():
    found = [d for d, _, _ in ESKD.find_designations("АРМ.303411.000 СБ Сборочный чертёж; АРМ.303411.100")]
    assert found == ["АРМ.303411.000 СБ", "АРМ.303411.100"]


def test_normalize_name_ignores_case_yo_and_punctuation():
    assert ESKD.normalize_name("Колесо  зубчатое.") == ESKD.normalize_name("колесо зубчатое")
    assert ESKD.normalize_name("Редуктор колёсный") == ESKD.normalize_name("Редуктор колесный")
    assert ESKD.normalize_name("Водило") != ESKD.normalize_name("Водило планетарное")


def test_litera_order():
    assert ESKD.normalize_litera("01") == "О1"
    assert ESKD.litera_rank("О") < ESKD.litera_rank("О1") < ESKD.litera_rank("А")
    assert ESKD.litera_rank("X") is None


def test_document_codes_come_from_the_dictionary():
    words = ESKD.words
    assert ESKD.normalize_designation("АРМ.303411.000 ЖЖ") == "АРМ.303411.000"  # not a code: left out
    eskd = Designations(words.model_copy(update={"document_codes": (*words.document_codes, "ЖЖ")}))
    assert eskd.normalize_designation("АРМ.303411.000 ЖЖ") == "АРМ.303411.000 ЖЖ"
