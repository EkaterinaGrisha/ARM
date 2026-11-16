"""The check of the gold set: requirements and parameters of a drawing, and the labellings of the customer's kits."""

import json

import gold
from synth.truth import Box, DrawingTruth, KitTruth, ParameterTruth


def _drawing(**fields) -> DrawingTruth:
    blank = {"requirements": ["Общие допуски по ГОСТ 30893.1-2002: H14, h14, ±IT14/2."], "parameters": []}
    return DrawingTruth(
        kind="part_drawing",
        origin="raster",
        designation="АБВГ.301411.001",
        name="Втулка",
        material="",
        mass="",
        scale="1:1",
        litera="",
        revision="",
        archive_number="",
        first_use="",
        **(blank | fields),
    )


def test_a_drawing_with_graded_tolerances_and_regions_on_its_sheet_passes():
    item = Box(page=1, bbox=(0.55, 0.73, 0.78, 0.76))
    parameters = [
        ParameterTruth(category="general_tolerance", name="IT14", it_grade=14, box=item),
        ParameterTruth(category="dimension_tolerance", name="Ø39+0,025", value=39, it_grade=7),
    ]
    truth = KitTruth(root="", documents={"a.jpg": _drawing(requirement_boxes=[item], parameters=parameters)})
    assert gold.content_problems(truth) == []


def test_a_grade_out_of_place_a_region_off_the_sheet_and_a_lost_item_region_are_problems():
    parameters = [
        ParameterTruth(category="roughness", name="Ra 5", value=5, unit="мкм", it_grade=7),
        ParameterTruth(category="dimension_tolerance", name="Ø13H7", it_grade=21),
        ParameterTruth(category="tolerance", name="⊥"),
        ParameterTruth(category="geometric", name="⊥", box=Box(page=2, bbox=(0.1, 0.1, 0.2, 0.2))),
        ParameterTruth(category="geometric", name="↗", box=Box(page=1, bbox=(0.3, 0.1, 0.2, 0.2))),
    ]
    boxes = [Box(page=1, bbox=(0.1, 0.7, 0.5, 0.72)), None]
    truth = KitTruth(root="", documents={"a.jpg": _drawing(requirement_boxes=boxes, parameters=parameters)})
    problems = gold.content_problems(truth)
    assert len(problems) == 6
    assert any("пунктов ТТ 2, пунктов 1" in p for p in problems)
    assert sum("квалитет" in p for p in problems) == 2


def test_a_labelling_of_a_customer_kit_names_files_of_its_manifest(tmp_path, monkeypatch, capsys):
    (tmp_path / "gold").mkdir()
    (tmp_path / "manifest.json").write_text(json.dumps({"arm-9": {"a.jpg": "0" * 64}}), encoding="utf-8")
    monkeypatch.setattr(gold, "ARM", tmp_path)
    labelling = tmp_path / "gold" / "arm-9.b.json"
    KitTruth(root="", documents={"a.jpg": _drawing()}).write(labelling)
    assert gold.check() == 0
    KitTruth(root="", documents={"b.jpg": _drawing()}).write(labelling)
    assert gold.check() == 1
    assert "b.jpg — нет в манифесте комплекта arm-9" in capsys.readouterr().out
