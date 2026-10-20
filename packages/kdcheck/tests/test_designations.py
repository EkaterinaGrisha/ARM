import pytest
from kdcheck.normalize.designations import (
    find_designations,
    litera_rank,
    normalize_designation,
    normalize_litera,
    normalize_name,
)


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
    assert normalize_designation(text) == expected


def test_find_designations_with_assembly_code():
    found = [d for d, _, _ in find_designations("АРМ.303411.000 СБ Сборочный чертёж; АРМ.303411.100")]
    assert found == ["АРМ.303411.000 СБ", "АРМ.303411.100"]


def test_normalize_name_ignores_case_yo_and_punctuation():
    assert normalize_name("Колесо  зубчатое.") == normalize_name("колесо зубчатое")
    assert normalize_name("Редуктор колёсный") == normalize_name("Редуктор колесный")
    assert normalize_name("Водило") != normalize_name("Водило планетарное")


def test_litera_order():
    assert normalize_litera("01") == "О1"
    assert litera_rank("О") < litera_rank("О1") < litera_rank("А")
    assert litera_rank("X") is None
