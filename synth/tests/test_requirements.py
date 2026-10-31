"""Templates of technical requirements: each item holds the parameters it writes, in the order of GOST 2.316."""

import random
import re

import pytest
from synth.requirements import Requirement, part
from synth.truth import DrawingTruth
from synth.vocab import PART_KINDS, Material, PartKind

from synth import kits

CASES = [(kind, material) for kind in PART_KINDS for material in kind.materials]


def _items(kind: PartKind, material: Material, draws: int = 60) -> list[list[Requirement]]:
    return [part(random.Random(seed), kind, material) for seed in range(draws)]


def _number(text: str) -> float:
    return float(text.replace(",", "."))


@pytest.mark.parametrize(("kind", "material"), CASES, ids=[f"{k.sketch}-{m.grade}" for k, m in CASES])
def test_each_item_writes_the_parameters_it_holds(kind, material):
    for items in _items(kind, material):
        for item in items:
            for p in item.params:
                match p.category:
                    case "hardness":
                        assert p.name in item.text and p.unit == p.name, item.text
                        assert f"{p.value_min:g}...{p.value_max:g}" in item.text, item.text
                    case "heat_treatment" if p.value_min is not None:
                        low, high = re.search(r"h (\S+)\.\.\.(\S+) мм", item.text).groups()
                        assert (_number(low), _number(high), p.unit) == (p.value_min, p.value_max, "мм"), item.text
                    case "general_tolerance":
                        grade = p.name.removeprefix("IT")
                        assert f"H{grade}, " in item.text or f"H{grade} " in item.text, item.text
                    case "coating" | "roughness":
                        assert p.name in item.text, item.text
                    case "geometric" if p.value is not None:
                        assert f"{p.value:g}".replace(".", ",") + " мм" in item.text, item.text


@pytest.mark.parametrize(("kind", "material"), CASES, ids=[f"{k.sketch}-{m.grade}" for k, m in CASES])
def test_the_material_decides_the_heat_treatment_and_the_coating(kind, material):
    treatments = {
        p.name for items in _items(kind, material) for i in items for p in i.params if p.category == "heat_treatment"
    }
    coatings = {p.name for items in _items(kind, material) for i in items for p in i.params if p.category == "coating"}
    match material.grade:
        case "case":
            assert treatments and treatments <= {"Цементация", "Нитроцементация"}
        case "steel":
            assert treatments and not treatments & {"Цементация", "Нитроцементация"}
        case _:  # a low carbon steel, a casting, a bronze
            assert not treatments
    if material.grade == "bronze":
        assert not coatings
    if material.grade == "iron":
        assert coatings and coatings <= {"Грунт ГФ-021", "Эмаль ПФ-115", "Эмаль МЛ-12"}


ORDER = ("heat_treatment", "hardness", "general_tolerance", "geometric", "roughness", "coating")


def test_items_follow_the_order_of_gost_2_316():
    """GOST 2.316-2008, 4.12: the material and its treatment, then dimensions and tolerances, then surfaces and
    coatings; marking after all of them."""
    for kind, material in CASES:
        for items in _items(kind, material):
            groups = [ORDER.index(p.category) // 2 for item in items for p in item.params[:1]]
            assert groups == sorted(groups), [i.text for i in items]
            texts = [i.text for i in items]
            assert "Маркировать обозначение детали." not in texts[:-1]


def test_templates_word_each_kind_of_item_several_ways():
    texts = {i.text for kind, material in CASES for items in _items(kind, material, 200) for i in items}
    general = {
        re.sub(r"\d+", "N", t) for t in texts if any(w in t for w in ("Неуказанные предельные", "Общие допуски по"))
    }
    assert len(general) >= 4
    assert len({t for t in texts if "закалить твч" in t.lower()}) >= 10
    assert len({t for t in texts if t.startswith("Покрытие") or "окрасить" in t}) >= 10


def test_the_truth_of_a_drawing_keeps_the_values_of_its_parameters_and_its_items_on_one_line():
    for seed in range(1, 30):
        kit = kits.build(seed, [], scan_share=0, raster_share=0, type_b_share=1)
        if any("\n" in r for d in kit.drawings for r in d.requirements):
            break
    truth = kit.truth()
    for drawing in kit.drawings:
        doc = truth.documents[drawing.file]
        assert isinstance(doc, DrawingTruth)
        assert doc.requirements == [r.replace("\n", " ") for r in drawing.requirements]
        for param, expected in zip(doc.parameters, drawing.expected_params, strict=True):
            assert (param.value, param.value_min, param.value_max, param.unit) == expected[2:]
    assert any(
        p.value_min is not None for d in truth.documents.values() if isinstance(d, DrawingTruth) for p in d.parameters
    )
