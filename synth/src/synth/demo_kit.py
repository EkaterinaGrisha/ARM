"""Synthetic ESKD doc set for demos and tests.

Writes a folder of PDF and JPG documents in the ESKD style (specifications by GOST 2.106, drawings with the title
block of GOST 2.104) with typical discrepancies planted, and ``<folder>_truth.json`` with the ground truth.

The documents are described with :mod:`synth.model`, drawn by :mod:`synth.render` with reportlab (BSD) in the
bundled DejaVu Sans, and scanned by :mod:`synth.scans`: rendered with PDFium (pypdfium2), with noise from the seeded
generator. So the kit does not depend on the fonts or the C library of the machine.

    uv run python -m synth.demo_kit samples/demo_kit
"""

from __future__ import annotations

import io
import random
import sys
from pathlib import Path

from synth.model import Change, Drawing, Row, Spec
from synth.render import draw_drawing, draw_foreign, draw_spec
from synth.scans import scan, scan_pdf
from synth.truth import DrawingTruth, ExpectedDiscrepancy, KitTruth, SpecTruth

ROOT = "АРМ.303411.000"

SPECS = [
    Spec(
        "АРМ.303411.000 Редуктор колёсный.pdf",
        ROOT,
        "Редуктор колёсный",
        changes=[Change(3, "АРМ.И.0587-25", "06.25"), Change(2, "АРМ.И.0412-25", "03.25")],
        archive="3411-000",
        rows=[
            Row("Документация", "А3", "", "АРМ.303411.000 СБ", "Сборочный чертёж"),
            Row("Сборочные единицы", "А3", "1", "АРМ.303411.100", "Корпус в сборе", "1"),
            Row("Сборочные единицы", "А3", "2", "АРМ.303411.200", "Вал ведомый в сборе", "1"),
            Row("Сборочные единицы", "А4", "3", "АРМ.303411.300", "Сателлит в сборе", "3"),
            Row("Детали", "А3", "4", "АРМ.303411.001", "Шестерня солнечная", "1"),
            Row("Детали", "А3", "5", "АРМ.303411.002", "Водило", "1"),
            Row("Детали", "А4", "6", "АРМ.303411.003", "Крышка торцевая", "1"),
            Row("Детали", "А4", "7", "АРМ.303411.004", "Прокладка", "1"),
            Row("Детали", "А4", "8", "АРМ.303411.005", "Гайка специальная", "1"),
            Row("Стандартные изделия", "", "9", "", "Болт М12×40 ГОСТ 7798-70", "12"),
            Row("Стандартные изделия", "", "10", "", "Манжета 1.2-80×105-1 ГОСТ 8752-79", "1"),
            Row("Стандартные изделия", "", "11", "", "Кольцо 050-055-30 ГОСТ 9833-73", "2"),
            Row("Материалы", "", "12", "", "Масло ТМ-5-18 ГОСТ 17479.2-2015", "2,5", "л"),
        ],
    ),
    Spec(
        "АРМ.303411.100 Корпус в сборе.pdf",
        "АРМ.303411.100",
        "Корпус в сборе",
        changes=[Change(1, "АРМ.И.0412-25", "03.25")],
        archive="3411-100",
        first_use=ROOT,
        rows=[
            Row("Документация", "А3", "", "АРМ.303411.100 СБ", "Сборочный чертёж"),
            Row("Детали", "А3", "1", "АРМ.303411.101", "Корпус", "1"),
            Row("Детали", "А3", "2", "АРМ.303411.102", "Крышка", "1"),
            Row("Детали", "А4", "3", "АРМ.303411.103", "Втулка", "2"),
            Row("Стандартные изделия", "", "4", "", "Болт М10×30 ГОСТ 7798-70", "8"),
            Row("Стандартные изделия", "", "5", "", "Шайба 10 65Г ГОСТ 6402-70", "8"),
        ],
    ),
    Spec(
        "АРМ.303411.200 Вал ведомый в сборе.pdf",
        "АРМ.303411.200",
        "Вал ведомый в сборе",
        archive="3411-200",
        first_use=ROOT,
        rows=[
            Row("Документация", "А3", "", "АРМ.303411.200 СБ", "Сборочный чертёж"),
            Row("Детали", "А3", "1", "АРМ.303411.201", "Вал ведомый", "1"),
            Row("Детали", "А3", "2", "АРМ.303411.202", "Колесо зубчатое", "1"),
            Row("Детали", "А4", "3", "АРМ.303411.203", "Кольцо распорное", "1"),
            Row("Стандартные изделия", "", "4", "", "Подшипник 7310 ГОСТ 27365-87", "2"),
            Row("Стандартные изделия", "", "5", "", "Шпонка 2-16×10×50 ГОСТ 23360-78", "1"),
        ],
    ),
]

GENERAL_TOL = "Неуказанные предельные отклонения размеров: H14, h14, ±IT14/2."

DRAWINGS = [
    Drawing(
        "АРМ.303411.000 СБ.pdf",
        "АРМ.303411.000 СБ",
        "Редуктор колёсный",
        mass="48",
        scale="1:2",
        litera="О",
        changes=[Change(3, "АРМ.И.0587-25", "06.25")],
        archive="3411-000",
        first_use="",
        requirements=[
            "Размеры для справок.",
            "Момент затяжки болтов поз. 9 — 80...90 Н·м.",
            "Заправить маслом поз. 12 в объёме 2,5 л.",
        ],
        annotations=["Ø280", "1", "2", "3", "4", "5"],
        sketch="assembly",
        assembly=True,
    ),
    Drawing(
        "АРМ.303411.100 СБ.pdf",
        "АРМ.303411.100 СБ",
        "Корпус в сборе",
        mass="27",
        scale="1:2",
        changes=[Change(1, "АРМ.И.0412-25", "03.25")],
        archive="3411-100",
        first_use=ROOT,
        requirements=["Размеры для справок.", "Момент затяжки болтов поз. 4 — 45...50 Н·м."],
        annotations=["Ø250", "1", "2", "3"],
        sketch="assembly",
        assembly=True,
    ),
    Drawing(
        "АРМ.303411.200 СБ.pdf",
        "АРМ.303411.200 СБ",
        "Вал ведомый в сборе",
        mass="9,6",
        scale="1:1",
        archive="3411-200",
        first_use=ROOT,
        requirements=["Размеры для справок.", "Осевой люфт подшипников 0,05...0,10 мм."],
        annotations=["315", "1", "2", "3"],
        sketch="assembly",
        assembly=True,
    ),
    Drawing(
        "АРМ.303411.300 СБ.pdf",
        "АРМ.303411.300 СБ",
        "Сателлит в сборе",
        fmt="A4",
        mass="1,9",
        scale="1:1",
        archive="3411-300",
        first_use=ROOT,
        requirements=["Размеры для справок."],
        annotations=["Ø96"],
        sketch="assembly",
        assembly=True,
    ),
    Drawing(
        "АРМ.303411.001 Шестерня солнечная.pdf",
        "АРМ.303411.001",
        "Шестерня солнечная",
        material="Сталь 20ХН3А ГОСТ 4543-2016",
        mass="1,85",
        litera="О1",
        changes=[Change(1, "АРМ.И.0412-25", "03.25")],
        archive="3411-001",
        first_use=ROOT,
        requirements=["Цементировать h 0,8...1,2 мм, 58...62 HRC.", GENERAL_TOL, "Острые кромки притупить R0,5."],
        annotations=["Ø40h6", "Ø62,5", "Ra 0,8", "Ra 1,6", "⊥ 0,02 А", "48"],
        general_roughness="Ra 6,3",
        sketch="gear",
        expected_params=[
            ("heat_treatment", "Цементация"),
            ("hardness", "HRC"),
            ("general_tolerance", "IT14"),
            ("dimension_tolerance", "Ø40h6"),
            ("roughness", "Ra 0,8"),
            ("roughness", "Ra 1,6"),
            ("geometric", "⊥"),
            ("roughness", "Ra 6,3"),
        ],
    ),
    Drawing(
        "АРМ.303411.002 Водило.pdf",
        "АРМ.303411.002",
        "Водило планетарное",
        material="Сталь 45 ГОСТ 1050-2013",
        mass="6,4",
        scale="1:2",
        archive="3411-002",
        first_use=ROOT,
        requirements=["235...262 HB.", GENERAL_TOL, "Позиционный допуск осей отверстий под оси сателлитов Ø0,02 мм."],
        annotations=["Ø62H7", "3 отв. Ø20H7", "Ø180", "Ra 1,6", "Ra 0,8", "◎ Ø0,03 А"],
        general_roughness="Ra 6,3",
        sketch="disc",
        expected_params=[
            ("hardness", "HB"),
            ("general_tolerance", "IT14"),
            ("geometric", "Позиционный"),
            ("dimension_tolerance", "Ø62H7"),
            ("dimension_tolerance", "Ø20H7"),
            ("roughness", "Ra 1,6"),
            ("roughness", "Ra 0,8"),
            ("geometric", "◎"),
            ("roughness", "Ra 6,3"),
        ],
    ),
    Drawing(
        "АРМ.303411.003 Крышка торцевая.pdf",
        "АРМ.303411.003",
        "Крышка торцевая",
        fmt="A4",
        material="Ст3сп ГОСТ 380-2005",
        mass="1,2",
        archive="3411-003",
        first_use=ROOT,
        requirements=["Покрытие Хим.Окс.прм.", GENERAL_TOL],
        annotations=["Ø105h8", "Ø80", "Ra 3,2"],
        general_roughness="Ra 12,5",
        sketch="disc",
        output="scan_pdf",
        expected_params=[
            ("coating", "Хим.Окс.прм"),
            ("general_tolerance", "IT14"),
            ("dimension_tolerance", "Ø105h8"),
            ("roughness", "Ra 3,2"),
            ("roughness", "Ra 12,5"),
        ],
    ),
    Drawing(
        "АРМ.303411.005 Гайка специальная.pdf",
        "АРМ.303411.005",
        "Гайка специальная",
        fmt="A4",
        material="Сталь 45 ГОСТ 1050-2013",
        mass="0,35",
        scale="2:1",
        archive="3411-005",
        first_use=ROOT,
        requirements=["30...35 HRC.", "Покрытие Ц9.хр.", GENERAL_TOL],
        annotations=["М48×1,5-6H", "Ø70", "Ra 3,2"],
        general_roughness="Ra 12,5",
        sketch="nut",
        expected_params=[
            ("hardness", "HRC"),
            ("coating", "Ц9.хр"),
            ("general_tolerance", "IT14"),
            ("dimension_tolerance", "М48×1,5-6H"),
            ("roughness", "Ra 3,2"),
            ("roughness", "Ra 12,5"),
        ],
    ),
    Drawing(
        "АРМ.303411.101 Корпус.pdf",
        "АРМ.303411.101",
        "Корпус",
        material="СЧ 20 ГОСТ 1412-85",
        mass="18,4",
        scale="1:2",
        litera="О",
        changes=[Change(1, "АРМ.И.0412-25", "03.25")],
        archive="3411-101",
        first_use="АРМ.303411.100",
        requirements=[
            "Неуказанные литейные радиусы R3...5 мм.",
            GENERAL_TOL,
            "Покрытие наружных необработанных поверхностей: Грунт ГФ-021, Эмаль ПФ-115 серая.",
        ],
        annotations=["Ø110H7", "Ø90H8", "Ø240", "Ra 1,6", "◎ Ø0,03 А", "∥ 0,05 Б"],
        general_roughness="Ra 12,5",
        sketch="housing",
        expected_params=[
            ("general_tolerance", "IT14"),
            ("coating", "Грунт ГФ-021"),
            ("dimension_tolerance", "Ø110H7"),
            ("dimension_tolerance", "Ø90H8"),
            ("roughness", "Ra 1,6"),
            ("geometric", "◎"),
            ("geometric", "∥"),
            ("roughness", "Ra 12,5"),
        ],
    ),
    Drawing(
        "АРМ.303411.102 Крышка.pdf",
        "АРМ.303411.102",
        "Крышка",
        material="СЧ 20 ГОСТ 1412-85",
        mass="5,6",
        scale="1:2",
        archive="3411-102",
        first_use="АРМ.303411.100",
        requirements=[
            "Неуказанные литейные радиусы R3 мм.",
            GENERAL_TOL,
            "Покрытие наружных необработанных поверхностей: Грунт ГФ-021, Эмаль ПФ-115 серая.",
        ],
        annotations=["Ø110h8", "Ø240", "Ra 3,2", "⏥ 0,05"],
        general_roughness="Ra 12,5",
        sketch="disc",
        expected_params=[
            ("general_tolerance", "IT14"),
            ("coating", "Грунт ГФ-021"),
            ("dimension_tolerance", "Ø110h8"),
            ("roughness", "Ra 3,2"),
            ("geometric", "⏥"),
            ("roughness", "Ra 12,5"),
        ],
    ),
    Drawing(
        "АРМ.303411.201 Вал ведомый.pdf",
        "АРМ.303411.201",
        "Вал ведомый",
        material="Сталь 40Х ГОСТ 4543-2016",
        mass="4,2",
        changes=[Change(2, "АРМ.И.0412-25", "03.25"), Change(1, "АРМ.И.0233-24", "11.24")],
        archive="3411-201",
        first_use="АРМ.303411.200",
        requirements=[
            "269...302 HB.",
            "Поверхности Б закалить ТВЧ h 1,5...2,0 мм, 48...53 HRC.",
            GENERAL_TOL,
            "Центровые отверстия B4 ГОСТ 14034-74.",
        ],
        annotations=["Ø50k6", "Ø56", "Ø45h6", "16N9", "Ra 0,8", "Ra 1,6", "↗ 0,016 А-Б", "315"],
        general_roughness="Ra 6,3",
        sketch="shaft",
        expected_params=[
            ("hardness", "HB"),
            ("heat_treatment", "Закалка ТВЧ"),
            ("hardness", "HRC"),
            ("general_tolerance", "IT14"),
            ("dimension_tolerance", "Ø50k6"),
            ("dimension_tolerance", "Ø45h6"),
            ("dimension_tolerance", "16N9"),
            ("roughness", "Ra 0,8"),
            ("roughness", "Ra 1,6"),
            ("geometric", "↗"),
            ("roughness", "Ra 6,3"),
        ],
    ),
    Drawing(
        "Вал_изм3.pdf",
        "АРМ.303411.201",
        "Вал ведомый",
        material="Сталь 40Х ГОСТ 4543-2016",
        mass="4,2",
        changes=[Change(3, "АРМ.И.0587-25", "06.25"), Change(2, "АРМ.И.0412-25", "03.25")],
        archive="3411-201",
        first_use="АРМ.303411.200",
        requirements=[
            "269...302 HB.",
            "Поверхности Б закалить ТВЧ h 1,5...2,0 мм, 48...53 HRC.",
            GENERAL_TOL,
            "Центровые отверстия B4 ГОСТ 14034-74.",
        ],
        annotations=["Ø50k6", "Ø56", "Ø45h6", "16N9", "Ra 0,8", "Ra 1,6", "↗ 0,016 А-Б", "315"],
        general_roughness="Ra 6,3",
        sketch="shaft",
        expected_params=[
            ("hardness", "HB"),
            ("heat_treatment", "Закалка ТВЧ"),
            ("hardness", "HRC"),
            ("general_tolerance", "IT14"),
            ("dimension_tolerance", "Ø50k6"),
            ("dimension_tolerance", "Ø45h6"),
            ("dimension_tolerance", "16N9"),
            ("roughness", "Ra 0,8"),
            ("roughness", "Ra 1,6"),
            ("geometric", "↗"),
            ("roughness", "Ra 6,3"),
        ],
    ),
    Drawing(
        "АРМ.303411.202 Колесо зубчатое.pdf",
        "АРМ.303411.202",
        "Колесо зубчатое",
        material="Сталь 20ХН3А ГОСТ 4543-2016",
        mass="3,1",
        archive="3411-202",
        first_use="АРМ.303411.200",
        requirements=["Цементировать h 0,8...1,2 мм, 58...62 HRC.", GENERAL_TOL],
        annotations=["Ø50H7", "16Js9", "Ø148,8", "Ra 0,4", "Ra 0,8", "↗ 0,02 А"],
        general_roughness="Ra 6,3",
        sketch="gear",
        expected_params=[
            ("heat_treatment", "Цементация"),
            ("hardness", "HRC"),
            ("general_tolerance", "IT14"),
            ("dimension_tolerance", "Ø50H7"),
            ("dimension_tolerance", "16Js9"),
            ("roughness", "Ra 0,4"),
            ("roughness", "Ra 0,8"),
            ("geometric", "↗"),
            ("roughness", "Ra 6,3"),
        ],
    ),
    Drawing(
        "АРМ.303411.203.jpg",
        "АРМ.303411.203",
        "Кольцо распорное",
        fmt="A4",
        material="Сталь 45 ГОСТ 1050-2013",
        mass="0,22",
        archive="3411-203",
        first_use="АРМ.303411.200",
        requirements=[GENERAL_TOL],
        annotations=["Ø50H9", "Ø62", "Ra 3,2"],
        general_roughness="Ra 12,5",
        sketch="disc",
        output="scan_jpg",
        expected_params=[
            ("general_tolerance", "IT14"),
            ("dimension_tolerance", "Ø50H9"),
            ("roughness", "Ra 3,2"),
            ("roughness", "Ra 12,5"),
        ],
    ),
    Drawing(
        "АРМ.303412.007 Кронштейн.pdf",
        "АРМ.303412.007",
        "Кронштейн",
        material="Сталь 09Г2С ГОСТ 19281-2014",
        mass="2,7",
        scale="1:2",
        litera="О",
        archive="3412-007",
        first_use="АРМ.303412.000",
        requirements=["Покрытие: Грунт ГФ-021, Эмаль ПФ-115 серая.", GENERAL_TOL],
        annotations=["Ø18H12", "140", "Ra 12,5"],
        general_roughness="Ra 25",
        sketch="bracket",
        expected_params=[
            ("coating", "Грунт ГФ-021"),
            ("general_tolerance", "IT14"),
            ("dimension_tolerance", "Ø18H12"),
            ("roughness", "Ra 12,5"),
            ("roughness", "Ra 25"),
        ],
    ),
]

EXPECTED_DISCREPANCIES = [
    ("missing_drawing", "АРМ.303411.004"),
    ("missing_drawing", "АРМ.303411.103"),
    ("missing_specification", "АРМ.303411.300"),
    ("orphan_drawing", "АРМ.303412.007"),
    ("name_mismatch", "АРМ.303411.002"),
    ("duplicate_designation", "АРМ.303411.201"),
    ("revision_mismatch", "АРМ.303411.201"),
    ("revision_mismatch", "АРМ.303411.000"),
    ("revision_mismatch", "АРМ.303411.101"),
]

PROBLEM_FILES = {
    "Чертежи_архив.pdf": "damaged",
    "скан_0034.jpg": "unreadable",
    "Акт приёмки КД.pdf": "foreign",
    "Thumbs.db": "unsupported",
}


def build(out: Path, truth_path: Path | None = None, seed: int = 7) -> KitTruth:
    """Write the kit to ``out`` and the ground truth to ``truth_path``, by default ``<out>_truth.json``."""
    rng = random.Random(seed)
    noise = random.Random(seed + 1)  # a stream of its own, so the noise does not shift the other random choices
    out.mkdir(parents=True, exist_ok=True)
    spec_dir = out / "Спецификации"
    draw_dir = out / "Чертежи"
    spec_dir.mkdir(exist_ok=True)
    draw_dir.mkdir(exist_ok=True)
    truth = KitTruth(root=ROOT, documents={})

    for s in SPECS:
        (spec_dir / s.file).write_bytes(draw_spec(s))
        truth.documents[f"Спецификации/{s.file}"] = SpecTruth.of(s)
    for d in DRAWINGS:
        pdf = draw_drawing(d)
        target = draw_dir / d.file
        if d.output == "vector":
            target.write_bytes(pdf)
        else:
            img = scan(pdf, rng=rng, noise=noise)
            if d.output == "scan_jpg":
                img.save(target, "JPEG", quality=72, dpi=(150, 150))
            else:
                buf = io.BytesIO()
                img.save(buf, "JPEG", quality=72)
                target.write_bytes(scan_pdf(buf.getvalue(), d.fmt))
        truth.documents[f"Чертежи/{d.file}"] = DrawingTruth.of(d)
    # problem files
    (out / "Чертежи_архив.pdf").write_bytes(b"%PDF-1.7\n" + bytes(rng.randrange(256) for _ in range(4000)))
    unreadable = scan(draw_drawing(DRAWINGS[8]), rng=rng, noise=noise, unreadable=True)
    unreadable.save(out / "скан_0034.jpg", "JPEG", quality=40)
    (out / "Акт приёмки КД.pdf").write_bytes(draw_foreign(ROOT, SPECS[0].name))
    (out / "Thumbs.db").write_bytes(bytes(rng.randrange(256) for _ in range(1024)))
    truth.problem_files = PROBLEM_FILES
    truth.discrepancies = [ExpectedDiscrepancy(type=t, designation=d) for t, d in EXPECTED_DISCREPANCIES]
    truth.write(truth_path or out.with_name(out.name + "_truth.json"))
    return truth


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    out = Path(argv[0] if argv else "samples/demo_kit")
    build(out)
    print(f"Демо-комплект записан в {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
