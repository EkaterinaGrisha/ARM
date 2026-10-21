"""Генератор синтетического комплекта КД для демонстрации и тестов.

Создаёт папку с PDF/JPG-документами в стиле ЕСКД (спецификации по ГОСТ 2.106,
чертежи с основной надписью по ГОСТ 2.104), в которые заложены типовые
расхождения, и файл ``<папка>_truth.json`` с эталонной разметкой.

    uv run python -m synth.demo_kit samples/demo_kit
"""

from __future__ import annotations

import io
import json
import os
import random
import sys
from dataclasses import dataclass, field
from itertools import pairwise
from pathlib import Path

import pymupdf
from PIL import Image, ImageFilter

MM = 72 / 25.4
FORMATS = {"A4": (210, 297), "A3": (420, 297)}

FONT_CANDIDATES = [
    os.environ.get("ARM_FONT", ""),
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
    "/Library/Fonts/DejaVuSans.ttf",
    "C:/Windows/Fonts/DejaVuSans.ttf",
    "C:/Windows/Fonts/arial.ttf",
]


def _font_buffer() -> bytes:
    for path in FONT_CANDIDATES:
        if path and Path(path).is_file():
            return Path(path).read_bytes()
    try:  # pip install pymupdf-fonts
        return pymupdf.Font("notos").buffer
    except Exception as exc:  # pragma: no cover - depends on the system
        raise RuntimeError("Не найден шрифт с кириллицей: задайте ARM_FONT=путь/к/шрифту.ttf") from exc


# --------------------------------------------------------------------------- данные комплекта


@dataclass
class Change:
    number: int
    notice: str
    date: str


@dataclass
class Drawing:
    file: str
    designation: str
    name: str
    fmt: str = "A3"
    material: str = ""
    mass: str = ""
    scale: str = "1:1"
    litera: str = "О1"
    changes: list[Change] = field(default_factory=list)
    archive: str = ""
    first_use: str = ""
    requirements: list[str] = field(default_factory=list)
    annotations: list[str] = field(default_factory=list)
    general_roughness: str = ""
    sketch: str = "disc"
    output: str = "vector"  # vector | scan_pdf | scan_jpg
    assembly: bool = False
    # эталонные параметры: (категория, имя) — для оценки качества
    expected_params: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class Row:
    section: str
    fmt: str = ""
    pos: str = ""
    designation: str = ""
    name: str = ""
    qty: str = ""
    note: str = ""


@dataclass
class Spec:
    file: str
    designation: str
    name: str
    litera: str = "О1"
    changes: list[Change] = field(default_factory=list)
    rows: list[Row] = field(default_factory=list)


ROOT = "АРМ.303411.000"

SPECS = [
    Spec(
        "АРМ.303411.000 Редуктор колёсный.pdf",
        ROOT,
        "Редуктор колёсный",
        changes=[Change(3, "АРМ.И.0587-25", "06.25"), Change(2, "АРМ.И.0412-25", "03.25")],
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


# --------------------------------------------------------------------------- рисование


class Sheet:
    """Страница с координатами в миллиметрах (начало — левый верхний угол)."""

    CONDENSE = 0.85  # узкий шрифт, как чертёжный по ГОСТ 2.304

    def __init__(self, doc: pymupdf.Document, fmt: str, font: bytes):
        w, h = FORMATS[fmt]
        self.w, self.h = w, h
        self.page = doc.new_page(width=w * MM, height=h * MM)
        self.page.insert_font(fontname="gost", fontbuffer=font)
        self.font = pymupdf.Font(fontbuffer=font)

    def line(self, x0, y0, x1, y1, width=0.25):
        self.page.draw_line((x0 * MM, y0 * MM), (x1 * MM, y1 * MM), width=width * MM, color=(0, 0, 0))

    def rect(self, x0, y0, x1, y1, width=0.25):
        self.page.draw_rect(pymupdf.Rect(x0 * MM, y0 * MM, x1 * MM, y1 * MM), width=width * MM, color=(0, 0, 0))

    def circle(self, cx, cy, r, width=0.5, dashes=None):
        self.page.draw_circle((cx * MM, cy * MM), r * MM, width=width * MM, color=(0, 0, 0), dashes=dashes)

    def width(self, s: str, size: float = 3.5) -> float:
        return self.font.text_length(s, size * MM * 1.35) * self.CONDENSE / MM

    def text(self, x, y, s, size=3.5, rotate=0, max_width=None):
        """Текст с базовой линией в точке (x, y); size — высота прописных букв в мм."""
        if not s:
            return
        fontsize = size * MM * 1.35
        point = pymupdf.Point(x * MM, y * MM)
        if rotate:
            self.page.insert_text(point, s, fontname="gost", fontsize=fontsize, rotate=rotate)
            return
        sx = self.CONDENSE
        if max_width:
            natural = self.width(s, size)
            if natural > max_width:
                k = max_width / natural
                sx *= max(k, 0.75)
                if k < 0.75:
                    fontsize *= k / 0.75
        self.page.insert_text(point, s, fontname="gost", fontsize=fontsize, morph=(point, pymupdf.Matrix(sx, 1)))

    def text_center(self, x0, x1, y_base, s, size=3.5):
        if not s:
            return
        avail = x1 - x0 - 1.6
        width = min(self.width(s, size), avail)
        self.text(x0 + (x1 - x0 - width) / 2, y_base, s, size, max_width=avail)


def _frame(sh: Sheet, archive: str, first_use: str) -> None:
    sh.rect(20, 5, sh.w - 5, sh.h - 5, width=0.7)
    # графы 19–23 (левое поле, снизу) и 24–25 (левое поле, сверху)
    x0, x1, x2 = 8, 13, 20
    labels = [
        ("Инв. № подл.", archive, 25),
        ("Подп. и дата", "", 35),
        ("Взам. инв. №", "", 25),
        ("Инв. № дубл.", "", 25),
        ("Подп. и дата", "", 35),
    ]
    y = sh.h - 5
    for label, value, height in labels:
        sh.rect(x0, y - height, x1, y)
        sh.rect(x1, y - height, x2, y)
        sh.text(x0 + 3.6, y - 2, label, 2.5, rotate=90)
        sh.text(x1 + 5, y - 2, value, 3.5, rotate=90)
        y -= height
    top = [("Перв. примен.", first_use, 60), ("Справ. №", "", 60)]
    y = 5
    for label, value, height in top:
        sh.rect(x0, y, x1, y + height)
        sh.rect(x1, y, x2, y + height)
        sh.text(x0 + 3.6, y + height - 2, label, 2.5, rotate=90)
        sh.text(x1 + 5, y + height - 2, value, 3.5, rotate=90)
        y += height


def _title_block(
    sh: Sheet,
    d: Drawing | Spec,
    *,
    material: str = "",
    mass: str = "",
    scale: str = "",
    spec_form: bool = False,
    sheets: int = 1,
) -> None:
    """Основная надпись: форма 1 (чертежи, 185×55) или форма 2 (спецификации, 185×40)."""
    height = 40 if spec_form else 55
    X, Y = sh.w - 5 - 185, sh.h - 5 - height
    sh.rect(X, Y, X + 185, Y + height, width=0.7)
    # левая часть: изменения и подписи
    cols = [0, 7, 17, 40, 55, 65]
    for c in cols[1:-1]:
        sh.line(X + c, Y, X + c, Y + 25)
    sh.line(X + 65, Y, X + 65, Y + height, width=0.7)
    for r in range(1, height // 5):
        sh.line(X, Y + r * 5, X + 65, Y + r * 5)
    change_rows = 4 if not spec_form else 4
    # шапка изменений — 5-я строка
    header_y = Y + change_rows * 5
    for (c0, c1), label in zip(pairwise(cols), ["Изм.", "Лист", "№ докум.", "Подп.", "Дата"], strict=True):
        sh.text_center(X + c0, X + c1, header_y + 4, label, 2.5)
    for i, ch in enumerate(sorted(d.changes, key=lambda c: -c.number)[:change_rows]):
        yb = header_y - i * 5 - 1
        sh.text_center(X + cols[0], X + cols[1], yb, str(ch.number), 2.5)
        sh.text_center(X + cols[1], X + cols[2], yb, "—", 2.5)
        sh.text(X + cols[2] + 0.8, yb, ch.notice, 2.5, max_width=21.4)
        sh.text_center(X + cols[4], X + cols[5], yb, ch.date, 2.5)
    roles = ["Разраб.", "Пров.", "Т.контр.", "", "Н.контр.", "Утв."]
    names = ["Иванов", "Петров", "", "", "Сидорова", "Кузнецов"]
    for i, (role, name) in enumerate(zip(roles, names, strict=True)):
        yb = header_y + 5 * (i + 1) + 4
        if yb > Y + height:
            break
        sh.text(X + 0.8, yb, role, 2.5, max_width=15.4)
        sh.text(X + 17.8, yb, name, 2.5, max_width=21.4)
    # правая часть
    sh.line(X + 65, Y + 15, X + 185, Y + 15, width=0.7)
    sh.text_center(X + 65, X + 185, Y + 11, d.designation, 7)
    if spec_form:
        sh.line(X + 135, Y + 15, X + 135, Y + 40, width=0.7)
        sh.text(X + 68, Y + 25, d.name, 5, max_width=65)
        for x in (150, 165):
            sh.line(X + x, Y + 15, X + x, Y + 25)
        sh.line(X + 135, Y + 20, X + 185, Y + 20)
        sh.line(X + 135, Y + 25, X + 185, Y + 25, width=0.7)
        sh.text_center(X + 135, X + 150, Y + 19, "Лит.", 2.5)
        sh.text_center(X + 150, X + 165, Y + 19, "Лист", 2.5)
        sh.text_center(X + 165, X + 185, Y + 19, "Листов", 2.5)
        sh.text_center(X + 135, X + 140, Y + 24, d.litera, 3.5)
        sh.text_center(X + 150, X + 165, Y + 24, "1", 3.5)
        sh.text_center(X + 165, X + 185, Y + 24, str(sheets), 3.5)
        sh.text_center(X + 135, X + 185, Y + 34, "ООО «АРМ»", 3.5)
        return
    sh.line(X + 135, Y + 15, X + 135, Y + 55, width=0.7)
    sh.line(X + 65, Y + 40, X + 135, Y + 40, width=0.7)
    lines = d.name.split("\n")
    for i, part in enumerate(lines):
        sh.text(X + 68, Y + 27 + i * 6 - (len(lines) - 1) * 3, part, 5, max_width=65)
    sh.text_center(X + 65, X + 135, Y + 49.5, material, 3.5)
    sh.line(X + 135, Y + 20, X + 185, Y + 20)
    sh.line(X + 135, Y + 35, X + 185, Y + 35, width=0.7)
    sh.line(X + 135, Y + 40, X + 185, Y + 40, width=0.7)
    for x in (140, 145, 150, 167):
        sh.line(X + x, Y + 20 if x < 150 else Y + 15, X + x, Y + 35)
    sh.text_center(X + 135, X + 150, Y + 19, "Лит.", 2.5)
    sh.text_center(X + 150, X + 167, Y + 19, "Масса", 2.5)
    sh.text_center(X + 167, X + 185, Y + 19, "Масштаб", 2.5)
    sh.text_center(X + 135, X + 140, Y + 29.5, d.litera, 3.5)
    sh.text_center(X + 150, X + 167, Y + 29.5, mass, 3.5)
    sh.text_center(X + 167, X + 185, Y + 29.5, scale, 3.5)
    sh.line(X + 155, Y + 35, X + 155, Y + 40)
    sh.text(X + 136, Y + 39, "Лист 1", 2.5)
    sh.text(X + 156, Y + 39, f"Листов {sheets}", 2.5)
    sh.text_center(X + 135, X + 185, Y + 49.5, "ООО «АРМ»", 3.5)


def _sketch(sh: Sheet, kind: str, annotations: list[str]) -> None:
    cx, cy = (20 + sh.w - 5) / 2 - (40 if sh.w > 300 else 0), 95 if sh.w > 300 else 90
    if kind == "shaft":
        steps = [(-110, -80, 14), (-80, -20, 25), (-20, 30, 28), (30, 90, 25), (90, 110, 22.5)]
        for x0, x1, r in steps:
            sh.rect(cx + x0, cy - r, cx + x1, cy + r, width=0.5)
        sh.line(cx - 120, cy, cx + 120, cy, width=0.18)
    elif kind == "gear":
        sh.circle(cx - 45, cy, 48, dashes="[4 2] 0")
        sh.circle(cx - 45, cy, 52)
        sh.circle(cx - 45, cy, 25)
        sh.rect(cx + 30, cy - 52, cx + 70, cy + 52, width=0.5)
        sh.rect(cx + 30, cy - 25, cx + 70, cy + 25, width=0.25)
    elif kind == "housing":
        sh.rect(cx - 100, cy - 60, cx + 60, cy + 60, width=0.5)
        sh.rect(cx - 85, cy - 45, cx + 45, cy + 45, width=0.5)
        sh.circle(cx - 20, cy, 30)
        sh.line(cx - 110, cy, cx + 70, cy, width=0.18)
    elif kind == "nut":
        sh.circle(cx, cy - 10, 35)
        sh.circle(cx, cy - 10, 24)
        sh.circle(cx, cy - 10, 22, dashes="[2 1] 0")
    elif kind == "bracket":
        sh.rect(cx - 90, cy - 10, cx + 50, cy + 5, width=0.5)
        sh.rect(cx - 90, cy - 60, cx - 75, cy + 5, width=0.5)
        sh.circle(cx + 30, cy - 2, 9)
    elif kind == "assembly":
        sh.rect(cx - 110, cy - 55, cx + 70, cy + 55, width=0.5)
        sh.circle(cx - 20, cy, 40)
        sh.circle(cx - 20, cy, 15)
        sh.rect(cx - 140, cy - 12, cx + 100, cy + 12, width=0.3)
    else:  # disc
        sh.circle(cx - 40, cy, 45)
        sh.circle(cx - 40, cy, 20)
        sh.rect(cx + 30, cy - 45, cx + 50, cy + 45, width=0.5)
        sh.rect(cx + 30, cy - 20, cx + 50, cy + 20, width=0.25)
    # размеры и знаки — вокруг изображения
    slots = [(-95, -70), (-10, -72), (60, -60), (-100, 75), (0, 78), (60, 70), (85, -10), (-115, 0)]
    if sh.w < 300:
        slots = [(-75, -65), (5, -68), (-75, 68), (5, 72), (55, -30), (55, 30), (-80, 0), (40, 0)]
    for (dx, dy), ann in zip(slots, annotations, strict=False):  # draws at most len(slots) annotations
        if ann.isdigit() and len(ann) == 1:  # номер позиции на сборочном чертеже
            sh.circle(cx + dx + 1.8, cy + dy - 1.8, 4, width=0.25)
        sh.text(cx + dx, cy + dy, ann, 5 if ann.isdigit() and len(ann) == 1 else 3.5)
        sh.line(cx + dx, cy + dy + 1, cx + dx + max(12, sh.width(ann)), cy + dy + 1, width=0.18)


def draw_drawing(d: Drawing, font: bytes) -> bytes:
    doc = pymupdf.open()
    sh = Sheet(doc, d.fmt, font)
    _frame(sh, d.archive, d.first_use)
    _title_block(sh, d, material=d.material, mass=d.mass, scale=d.scale)
    _sketch(sh, d.sketch, d.annotations)
    if d.general_roughness:
        sh.text(sh.w - 45, 16, f"√{d.general_roughness} (√)", 5)
    # технические требования — над основной надписью
    X = sh.w - 5 - 185
    y = sh.h - 5 - 55 - 6 - 6 * len(d.requirements)
    if len(d.requirements) > 0:
        for i, req in enumerate(d.requirements, start=1):
            sh.text(X, y + (i - 1) * 6, f"{i}. {req}", 3.5, max_width=185)
    doc.subset_fonts()
    return doc.tobytes(garbage=3, deflate=True)


SPEC_COLS = [
    (0, 6, "Формат"),
    (6, 12, "Зона"),
    (12, 20, "Поз."),
    (20, 90, "Обозначение"),
    (90, 153, "Наименование"),
    (153, 163, "Кол."),
    (163, 185, "Приме-\nчание"),
]


def draw_spec(s: Spec, font: bytes) -> bytes:
    doc = pymupdf.open()
    lines: list[tuple[str, Row | None]] = []
    section = None
    for spec_row in s.rows:
        if spec_row.section != section:
            if section is not None:
                lines.append(("blank", None))
            lines.append(("section", spec_row))
            lines.append(("blank", None))
            section = spec_row.section
        lines.append(("row", spec_row))
    first_capacity, next_capacity = 23, 29
    pages = [lines[:first_capacity]]
    rest = lines[first_capacity:]
    while rest:
        pages.append(rest[:next_capacity])
        rest = rest[next_capacity:]
    for page_no, page_lines in enumerate(pages, start=1):
        sh = Sheet(doc, "A4", font)
        _frame(sh, "3411-" + s.designation[-3:], "" if s.designation == ROOT else ROOT)
        X = 20
        tb_height = 40 if page_no == 1 else 15
        bottom = sh.h - 5 - tb_height
        sh.line(X, 20, X + 185, 20, width=0.7)
        for x0, x1, label in SPEC_COLS:
            sh.line(X + x0, 5, X + x0, bottom, width=0.7)
            parts = label.split("\n")
            if x1 - x0 <= 8:  # узкие графы — вертикальная надпись
                sh.text(X + x0 + 4, 18, label, 2.5, rotate=90)
            else:
                for i, part in enumerate(parts):
                    sh.text_center(X + x0, X + x1, 12 + i * 4 - (len(parts) - 1) * 2, part, 2.5)
        y = 20
        for kind, row in page_lines:
            y += 8
            sh.line(X, y, X + 185, y, width=0.18)
            if kind == "section" and row:
                sh.text_center(X + 90, X + 153, y - 2, row.section, 3.5)
                width = sh.width(row.section)
                mid = X + 121.5
                sh.line(mid - width / 2, y - 1.2, mid + width / 2, y - 1.2, width=0.25)
            elif kind == "row" and row:
                sh.text_center(X + 0, X + 6, y - 2, row.fmt, 2.5)
                sh.text_center(X + 12, X + 20, y - 2, row.pos, 3.5)
                sh.text(X + 21, y - 2, row.designation, 3.5, max_width=68)
                sh.text(X + 91, y - 2, row.name, 3.5, max_width=61)
                sh.text_center(X + 153, X + 163, y - 2, row.qty, 3.5)
                sh.text(X + 164, y - 2, row.note, 3.5, max_width=20)
        if page_no == 1:
            _title_block(sh, s, spec_form=True, sheets=len(pages))
        else:
            Y = sh.h - 5 - 15
            sh.rect(X + 0, Y, X + 185, Y + 15, width=0.7)
            sh.text_center(X + 65, X + 175, Y + 10, s.designation, 7)
            sh.line(X + 175, Y, X + 175, Y + 15)
            sh.text(X + 176, Y + 4, "Лист", 2.5)
            sh.text_center(X + 175, X + 185, Y + 12, str(page_no), 3.5)
    doc.subset_fonts()
    return doc.tobytes(garbage=3, deflate=True)


def draw_foreign(font: bytes) -> bytes:
    doc = pymupdf.open()
    sh = Sheet(doc, "A4", font)
    body = [
        ("АКТ", 6),
        ("приёмки комплекта конструкторской документации", 4),
        ("", 4),
        ("г. Калуга                                                    12.06.2025", 3.5),
        ("", 4),
        ("Комиссия в составе представителей КБ и производства приняла комплект КД", 3.5),
        (f"на изделие {ROOT} «Редуктор колёсный» для запуска в опытное", 3.5),
        ("производство. Комплект передан в электронном виде.", 3.5),
        ("", 4),
        ("Замечания: отсутствуют.", 3.5),
        ("", 4),
        ("Председатель комиссии ____________ / Кузнецов А. В.", 3.5),
        ("Члены комиссии          ____________ / Петров С. Н.", 3.5),
    ]
    y: float = 30
    for line, size in body:
        sh.text(25, y, line, size)
        y += size * 2.2
    doc.subset_fonts()
    return doc.tobytes(garbage=3, deflate=True)


def _scan(pdf_bytes: bytes, *, rng: random.Random, unreadable: bool = False) -> Image.Image:
    src = pymupdf.open("pdf", pdf_bytes)
    dpi = 40 if unreadable else 150
    pix = src[0].get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY)
    img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("L")
    if unreadable:
        img = img.resize((img.width * 150 // 40, img.height * 150 // 40)).filter(ImageFilter.GaussianBlur(4))
        img = img.point(lambda v: 112 + v * 30 // 255)
    img = img.rotate(rng.uniform(-0.8, 0.8), resample=Image.Resampling.BICUBIC, expand=False, fillcolor=255)
    noise = Image.effect_noise(img.size, 28 if unreadable else 12)
    img = Image.blend(img, noise, 0.25 if unreadable else 0.08)
    return img.point(lambda v: min(255, int(v * (0.92 if unreadable else 1.0) + (6 if not unreadable else 0))))


def build(out: Path, truth_path: Path | None = None, seed: int = 7) -> dict:
    """Записывает комплект в ``out``, эталон — рядом, в ``<out>_truth.json``."""
    rng = random.Random(seed)
    out.mkdir(parents=True, exist_ok=True)
    font = _font_buffer()
    spec_dir = out / "Спецификации"
    draw_dir = out / "Чертежи"
    spec_dir.mkdir(exist_ok=True)
    draw_dir.mkdir(exist_ok=True)
    truth: dict = {"root": ROOT, "documents": {}, "discrepancies": [], "problem_files": {}}

    for s in SPECS:
        (spec_dir / s.file).write_bytes(draw_spec(s, font))
        truth["documents"][f"Спецификации/{s.file}"] = {
            "kind": "specification",
            "designation": s.designation,
            "name": s.name,
            "litera": s.litera,
            "revision": str(max((c.number for c in s.changes), default=0) or "") or None,
            "rows": [r.__dict__ for r in s.rows],
        }
    for d in DRAWINGS:
        pdf = draw_drawing(d, font)
        target = draw_dir / d.file
        if d.output == "vector":
            target.write_bytes(pdf)
        else:
            img = _scan(pdf, rng=rng)
            if d.output == "scan_jpg":
                img.save(target, "JPEG", quality=72, dpi=(150, 150))
            else:
                buf = io.BytesIO()
                img.save(buf, "JPEG", quality=72)
                doc = pymupdf.open()
                w, h = FORMATS[d.fmt]
                page = doc.new_page(width=w * MM, height=h * MM)
                page.insert_image(page.rect, stream=buf.getvalue())
                target.write_bytes(doc.tobytes(deflate=True))
        truth["documents"][f"Чертежи/{d.file}"] = {
            "kind": "assembly_drawing" if d.assembly else "part_drawing",
            "origin": "vector" if d.output == "vector" else "scan",
            "designation": d.designation,
            "name": d.name,
            "material": d.material or None,
            "mass": d.mass or None,
            "scale": d.scale,
            "litera": d.litera,
            "revision": str(max((c.number for c in d.changes), default=0) or "") or None,
            "archive_number": d.archive or None,
            "first_use": d.first_use or None,
            "requirements": d.requirements,
            "parameters": [{"category": c, "name": n} for c, n in d.expected_params],
        }
    # проблемные файлы
    (out / "Чертежи_архив.pdf").write_bytes(b"%PDF-1.7\n" + bytes(rng.randrange(256) for _ in range(4000)))
    _scan(draw_drawing(DRAWINGS[8], font), rng=rng, unreadable=True).save(out / "скан_0034.jpg", "JPEG", quality=40)
    (out / "Акт приёмки КД.pdf").write_bytes(draw_foreign(font))
    (out / "Thumbs.db").write_bytes(bytes(rng.randrange(256) for _ in range(1024)))
    truth["problem_files"] = PROBLEM_FILES
    truth["discrepancies"] = [{"type": t, "designation": d} for t, d in EXPECTED_DISCREPANCIES]
    truth_path = truth_path or out.with_name(out.name + "_truth.json")
    truth_path.write_text(json.dumps(truth, ensure_ascii=False, indent=2), encoding="utf-8")
    return truth


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    out = Path(argv[0] if argv else "samples/demo_kit")
    build(out)
    print(f"Демо-комплект записан в {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
