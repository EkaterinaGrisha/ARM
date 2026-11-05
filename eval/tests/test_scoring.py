"""One rule for a correct extraction (eval/gold/README.md, «Что считается верным извлечением»)."""

import scoring
from kdcheck.schemas import Parameter
from synth.truth import ParameterTruth


def test_norm_ignores_what_looks_the_same_on_the_sheet():
    # Latin «A», «C», «E», an en dash, «ё», a decimal point, a line break, quotes and the diameter sign
    typed = "AБВГ.301411.001 — Сталь 45 «ГОСТ»\nКольцо Ø40 4.2 ёмкость"
    read = 'АБВГ.301411.001 - сталь 45 "ГОСТ" Кольцо 40 4,2 емкость'
    assert scoring.norm(typed) == scoring.norm(read)
    assert scoring.norm(None) == scoring.norm("") == ""


def test_norm_keeps_a_wrong_letter_or_digit():
    assert scoring.norm("Кожух") != scoring.norm("Кажух")
    assert scoring.norm("АБВГ.301411.001") != scoring.norm("АБВГ.301411.007")


def _found(**fields) -> Parameter:
    return Parameter(id="p", **{"category": "roughness", "name": "Ra", "text": "Ra 1,6 мкм", **fields})


def test_parameters_match_one_to_one_by_category_and_name():
    want = [ParameterTruth(category="roughness", name="Ra 1,6"), ParameterTruth(category="roughness", name="Ra 1,6")]
    got = [_found(), _found(category="coating", text="Ra 1,6")]
    assert scoring.match_parameters(want, got) == [0, None]


def test_a_value_is_right_only_with_the_numbers_and_unit_of_the_truth():
    want = ParameterTruth(category="roughness", name="Ra 1,6", value=1.6, unit="мкм")
    assert scoring.value_right(want, _found(value=1.6, unit="мкм"))
    assert not scoring.value_right(want, _found(value=3.2, unit="мкм"))
    assert not scoring.value_right(want, _found(value=1.6, unit="мм"))
    assert not scoring.value_right(want, _found())
    plain = ParameterTruth(category="coating", name="Хим. Окс. прм")
    assert not scoring.has_value(plain) and scoring.value_right(plain, _found(category="coating"))
