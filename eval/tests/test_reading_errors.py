"""Kinds of reading errors (eval/reading_errors.py): each kind on the examples of the common list and a near miss."""

import reading_errors as re_
from kdcheck.schemas import Extracted, SourceRef
from synth.truth import Box


def test_a_letter_read_as_its_twin_digit():
    assert re_.kind("О", "0") == re_.TWIN
    assert re_.kind("Ст3сп ГОСТ 380-2005", "СмЗсп Г0СТ 380-2005") == re_.TWIN
    assert re_.twins("Крышка в сборе", "Крышка в сдоре") == [("б", "д")]


def test_many_letters_changed_are_no_twin():
    assert re_.kind("Фланец", "Кольцо") != re_.TWIN


def test_a_lost_sign():
    assert re_.kind("1:1", "11") == re_.LOST_SIGN
    assert re_.kind("HRC 40...45", "HRC 4045") == re_.LOST_SIGN


def test_a_lost_digit_is_no_lost_sign():
    assert re_.kind("12,55", "12,5") == re_.LOSSY


def test_a_special_symbol():
    assert re_.kind("⌀18H12", "018Н12") == re_.SPECIAL
    assert re_.kind("√Ra 25 (√)", "VRa 25 (V)") == re_.SPECIAL


def test_a_twin_beside_a_symbol_is_no_special_symbol():
    assert re_.kind("⌀18H12", "⌀1ВН12") != re_.SPECIAL


def test_the_label_of_a_cell_glued_to_its_value():
    assert re_.kind("2", "Изм. 2") == re_.LABEL_GLUED
    assert re_.kind("2", "22") == re_.NEIGHBOUR


def test_a_line_read_with_losses_and_a_wrong_value():
    assert re_.kind("Закалить ТВЧ h 2...3 мм, 45...50 HRC", "Закалить ТВ2мм455НR") == re_.LOSSY
    assert re_.kind("Ротор", "Ромашка") == re_.WRONG
    assert re_.kind("Ротор", "Ромашка", model=True) == re_.MADE_UP


def test_missed_and_extra_values():
    assert re_.kind("Фланец", "") == re_.MISSED_CELL
    assert re_.kind("Размеры для справок.", "", line=True) == re_.MISSED_LINE
    assert re_.kind("", "Лит.") == re_.EXTRA_VALUE
    assert re_.kind("", "Острые кромки притупить.", line=True) == re_.EXTRA_LINE
    assert re_.kind("", "2", model=True) == re_.MADE_UP


def test_the_value_of_another_cell_is_a_wrong_region():
    others = frozenset({"сталь45"})
    assert re_.kind("Ст3сп", "Сталь 45", others=others) == re_.WRONG_REGION
    assert re_.kind("Ст3сп", "Сталь 40", others=others) != re_.WRONG_REGION


def _item(text: str, bbox: tuple[float, float, float, float]) -> Extracted:
    return Extracted(value=text, source=SourceRef(document_id="d", page=1, bbox=bbox))


def test_items_pair_by_region_before_likeness():
    want = ["Размеры для справок.", "Острые кромки притупить."]
    boxes = [Box(page=1, bbox=(0.1, 0.70, 0.6, 0.72)), Box(page=1, bbox=(0.1, 0.72, 0.6, 0.74))]
    got = [_item("Острые кромку притупить.", (0.1, 0.721, 0.5, 0.739)), _item("Ра3меры", (0.1, 0.701, 0.3, 0.719))]
    assert re_.pair_items(want, boxes, got) == [1, 0]
    assert re_.pair_items(want, boxes, [_item("Совсем другое", (0.1, 0.9, 0.2, 0.95))]) == [None, None]


def test_a_sign_before_an_item_and_a_twin_beside_a_lost_sign():
    assert re_.kind("Размеры для справок.", "* Размеры для справок.") == re_.EXTRA_SIGN
    assert re_.kind("Размеры для справок.", "Размеры для спрабок") == re_.TWIN
    assert [what for what, _, _ in re_.changes("Размеры для справок.", "Размеры для спрабок")] == [
        re_.TWIN,
        re_.LOST_SIGN,
    ]


def test_a_whole_number_of_the_report_is_written_as_the_sheet_writes_it():
    assert re_._text(2.0) == "2"
    assert re_._text(2.5) == "2.5"


def test_an_item_read_inside_its_neighbour_is_covered_by_it():
    glued = "Острые кромки притупить & Маркиробать обозначение детали"
    assert re_.covered("Острые кромки притупить.", glued) >= 0.7
    assert re_.covered("Размеры для справок.", glued) < 0.7
