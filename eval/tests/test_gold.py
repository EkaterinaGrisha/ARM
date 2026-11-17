"""The check of the gold set: requirements and parameters of a drawing, and the labellings of the customer's kits."""

import json

import gold
from synth.truth import Box, DrawingTruth, KitTruth, ParameterTruth


def _drawing(**fields) -> DrawingTruth:
    blank = {
        "kind": "part_drawing",
        "origin": "raster",
        "designation": "АБВГ.301411.001",
        "name": "Втулка",
        "material": "",
        "mass": "",
        "scale": "1:1",
        "litera": "",
        "revision": "",
        "archive_number": "",
        "first_use": "",
        "requirements": ["Общие допуски по ГОСТ 30893.1-2002: H14, h14, ±IT14/2."],
        "parameters": [],
    }
    return DrawingTruth(**(blank | fields))


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


def test_an_item_one_labelling_misses_shifts_no_other_and_a_name_read_two_ways_is_one_disagreement():
    items = ["Общие допуски по ГОСТ 30893.1-2002: H14, h14, ±IT14/2.", "Покрытие Ц9.хр.", "Острые кромки притупить."]
    boxes = [Box(page=1, bbox=(0.55, 0.70 + 0.03 * i, 0.78, 0.72 + 0.03 * i)) for i in range(3)]
    field = [Box(page=1, bbox=(0.2, 0.3, 0.25, 0.32)), Box(page=1, bbox=(0.4, 0.3, 0.45, 0.32))]
    a = _drawing(
        requirements=items,
        requirement_boxes=boxes,
        parameters=[
            ParameterTruth(category="roughness", name="Ra 3,2", value=3.2, unit="мкм", box=field[0]),
            ParameterTruth(category="roughness", name="Ra 3,2", value=3.2, unit="мкм", box=field[1]),
            ParameterTruth(category="general_tolerance", name="IT14", it_grade=14, box=boxes[0]),
        ],
    )
    b = _drawing(
        requirements=[items[0], items[2]],
        requirement_boxes=[boxes[0], boxes[2]],
        parameters=[
            ParameterTruth(category="general_tolerance", name="IT14", it_grade=12, box=boxes[0]),
            ParameterTruth(category="roughness", name="Ra 3,2", value=3.2, unit="мкм", box=field[1]),
            ParameterTruth(category="roughness", name="Ra 6,3", value=6.3, unit="мкм", box=field[0]),
        ],
    )
    compared = gold.compare(KitTruth(root="", documents={"a.jpg": a}), KitTruth(root="", documents={"a.jpg": b}))
    differ = {where.removeprefix("a.jpg: "): (x, y) for _, where, x, y, same in compared if not same}
    assert differ == {
        "пункт ТТ 2/—, text": ("Покрытие Ц9.хр.", "—"),
        "пункт ТТ 2/—, box": ("лист 1: 0.550 0.730 0.780 0.750", "—"),
        "параметр 1/3 roughness, name": ("Ra 3,2", "Ra 6,3"),
        "параметр 1/3 roughness, value": ("3.2", "6.3"),
        "параметр 3/1 general_tolerance, it_grade": ("14", "12"),
    }
    assert {part for part, *_ in compared} == {"документы", "пункты ТТ", "параметры", "области"}


def test_the_agreement_of_a_customer_kit_gives_numbers_and_leaves_its_disagreements_by_its_drafts(
    tmp_path, monkeypatch
):
    (tmp_path / "gold").mkdir()
    (tmp_path / "drafts").mkdir()
    monkeypatch.setattr(gold, "ARM", tmp_path)
    monkeypatch.setattr(gold, "DRAFTS", tmp_path / "drafts")
    monkeypatch.setattr(gold, "OUT", tmp_path / "agreement.md")
    for label, material in (("a", "Сталь 45 ГОСТ 1050-2013"), ("b", "Сталь 40Х ГОСТ 4543-2016")):
        KitTruth(root="", documents={"a.jpg": _drawing(material=material)}).write(
            tmp_path / "gold" / f"arm-9.{label}.json"
        )
    KitTruth(root="", documents={}).write(tmp_path / "gold" / "arm-9.json")
    assert gold.agree() == 0
    numbers = (tmp_path / "agreement.md").read_text(encoding="utf-8")
    assert "## arm-9: a и b" in numbers and "Сталь" not in numbers
    assert "| a.jpg: material | Сталь 45 ГОСТ 1050-2013 | Сталь 40Х ГОСТ 4543-2016 |" in (
        tmp_path / "gold" / "arm-9.a-b.md"
    ).read_text(encoding="utf-8")
