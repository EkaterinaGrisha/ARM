"""Title block and specification extraction on the vector documents of the committed demo kit."""

import json
from pathlib import Path

import pytest
from kdcheck.config import Review
from kdcheck.extract import review, specification, tech_requirements, title_block
from kdcheck.ingest.document import LoadedDocument, Page, load_document
from kdcheck.ingest.text import Line, Word
from kdcheck.normalize.designations import Designations
from kdcheck.schemas import DocumentInfo, Extracted, PartRecord, SourceRef, TitleBlock

KIT = Path(__file__).resolve().parents[3] / "samples" / "demo_kit"
TRUTH = json.loads((KIT.parent / "demo_kit_truth.json").read_text(encoding="utf-8"))["documents"]
DRAWINGS = sorted(p for p, t in TRUTH.items() if t["kind"] != "specification" and t.get("origin") == "vector")
SPECS = sorted(p for p, t in TRUTH.items() if t["kind"] == "specification")
FIELDS = ("designation", "name", "material", "mass", "scale", "litera", "revision", "archive_number", "first_use")


def _text(ex: Extracted | None) -> str | None:
    return None if ex is None or ex.value is None else str(ex.value)


def _quantity(ex: Extracted | None) -> float | None:
    return None if ex is None or ex.value is None else float(ex.value)


@pytest.mark.parametrize("path", DRAWINGS)
def test_title_block_of_vector_drawing(path, settings, eskd):
    doc = load_document(KIT / path, KIT, settings)
    page = doc.pages[0]
    geom = title_block.locate(page, settings.forms)
    assert geom is not None and geom.form == 1
    tb = title_block.parse(doc, page, geom, settings.forms, eskd)
    assert {f: _text(getattr(tb, f)) for f in FIELDS} == {f: TRUTH[path][f] for f in FIELDS}


@pytest.mark.parametrize("path", SPECS)
def test_specification_rows(path, settings, eskd):
    doc = load_document(KIT / path, KIT, settings)
    assert specification.is_specification(doc.pages[0], settings.forms)
    spec = specification.parse(doc, settings.forms, eskd)
    assert (_text(spec.designation), _text(spec.name)) == (TRUTH[path]["designation"], TRUTH[path]["name"])
    got = [(r.section, _text(r.designation), _text(r.name), _quantity(r.quantity)) for r in spec.rows]
    want = [
        (
            r["section"],
            r["designation"] or None,
            r["name"] or None,
            float(r["qty"].replace(",", ".")) if r["qty"] else None,
        )
        for r in TRUTH[path]["rows"]
    ]
    assert got == want


def test_foreign_document_has_no_title_block_or_spec_table(settings):
    doc = load_document(KIT / "Акт приёмки КД.pdf", KIT, settings)
    page = doc.pages[0]
    assert page.lines
    assert title_block.locate(page, settings.forms) is None
    assert not specification.is_specification(page, settings.forms)


def _read(method: str, confidence: float | None, value: str | None = "АРМ.303411.001") -> Extracted:
    return Extracted(value=value, source=SourceRef(document_id="d1", method=method, confidence=confidence))


def test_value_status_follows_the_review_settings(settings):
    rules = settings.ingest.review
    assert review.status(_read("text_layer", 0.95), rules) == "ok"
    assert review.status(_read("text_layer", 0.5), rules) == "low_confidence"  # below min_confidence
    assert review.status(_read("ocr", 0.9), rules) == "low_confidence"  # OCR is not calibrated yet
    assert review.status(_read("filename", None), rules) == "low_confidence"
    assert review.status(_read("text_layer", 0.95, value=None), rules) == "missing"
    calibrated = Review(methods=(), min_confidence=rules.min_confidence)
    assert review.status(_read("ocr", 0.9), calibrated) == "ok"


def test_marking_reaches_nested_values_and_keeps_a_status_set_before(settings):
    conflict = _read("text_layer", 0.95).model_copy(update={"status": "conflict"})
    part = PartRecord(
        document_id="d1",
        title_block=TitleBlock(designation=conflict, name=_read("ocr", 0.9, value="Вал")),
        requirements=[_read("text_layer", 0.95, value="1. *Размеры для справок.")],
    )
    review.mark(part, settings.ingest.review)
    assert [ex.status for ex in review.values(part)] == ["conflict", "low_confidence", "ok"]


@pytest.mark.parametrize(
    ("field", "text", "value"),
    [
        ("scale", "1:2", "1:2"),
        ("scale", "11", "1:1"),  # OCR lost the colon: the one scale of GOST 2.302 these digits spell
        ("scale", "1 2", "1:2"),
        ("scale", "125", "1:25"),
        ("scale", "2,51", "2,5:1"),
        ("scale", "17", None),  # 1:7 and 17:1 are no scales of GOST 2.302
        ("mass", "12,5", "12,5"),
        ("mass", "См. табл.", "См. табл."),  # a group drawing, GOST 2.113
        ("mass", "(м. табл.", "См. табл."),
        ("name", "‚Корпус |", "Корпус"),  # noise and ruled lines at the edges of a recognized cell
        ("material", "Сталь 45 ГОСТ 1050-2013", "Сталь 45 ГОСТ 1050-2013"),
    ],
)
def test_cell_value_by_gost(settings, field, text, value):
    assert title_block.cell_value(field, text, Designations(settings.dictionaries)) == value


def test_section_header_read_with_errors(settings):
    sections = {"детали": "Детали", "стандартныеизделия": "Стандартные изделия"}
    similarity = settings.forms.specification.section_similarity
    assert specification._section("детали", sections, similarity) == "Детали"
    assert specification._section("детолу", sections, similarity) == "Детали"  # OCR of «Детали» in GOST lettering
    assert specification._section("тондортныеизделия", sections, similarity) == "Стандартные изделия"
    assert specification._section("всборе", sections, similarity) is None  # the second line of a name


def test_items_of_a_requirements_block(settings):
    """A numbered line starts an item; a line that starts with a range or a decimal continues the item before."""
    lines = [
        "Технические требования",
        "1. 269...302 HB.",
        "2. Поверхности Б закалить ТВЧ h 1,5...2,0 мм,",
        "48...53 HRC.",
        "3) Покрытие Хим.Окс.прм.",
        "2,5 мм от торца.",
    ]
    items = tech_requirements.item_texts(lines, settings.forms.tech_requirements)
    assert items == [
        ("269...302 HB.", [1]),
        ("Поверхности Б закалить ТВЧ h 1,5...2,0 мм, 48...53 HRC.", [2, 3]),
        ("Покрытие Хим.Окс.прм. 2,5 мм от торца.", [4, 5]),
    ]
    single = tech_requirements.item_texts(
        ["Неуказанные предельные отклонения размеров: H14, h14, ±IT14/2."], settings.forms.tech_requirements
    )
    assert [text for text, _ in single] == [
        "Неуказанные предельные отклонения размеров: H14, h14, ±IT14/2."
    ]  # one item, unnumbered


def test_without_item_numbers_the_block_ends_at_a_row_of_the_field(settings, eskd):
    """OCR missed every item number: the block is taken all the same, a piece of a row stays with its row, and the
    text of the field that starts far right of the block is left to the parameters of the field."""
    from kdcheck.ingest.text import Line

    doc = load_document(KIT / "Чертежи/АРМ.303411.201 Вал ведомый.pdf", KIT, settings)
    page = doc.pages[0]
    geom = title_block.locate(page, settings.forms)
    block = settings.forms.tech_requirements
    mm = geom.s
    x, y = geom.x0, geom.y0
    pieces = [
        Line("р. Покрытие Ц9.хр", (x, y - 16 * mm, x + 60 * mm, y - 12 * mm), 3.5 * mm),
        Line("Общие допуски по ГОСТ 30893.1:", (x, y - 22 * mm, x + 70 * mm, y - 18 * mm), 3.5 * mm),
        Line("H14, h14, ±IT14/2", (x + 72 * mm, y - 22 * mm, x + 110 * mm, y - 18 * mm), 3.5 * mm),  # same row
        Line("Ø42 h6 (-0,016)", (x + 90 * mm, y - 28 * mm, x + 130 * mm, y - 24 * mm), 3.5 * mm),  # the field
    ]
    page.lines = pieces
    assert tech_requirements.requirement_lines(page, geom, block) == []  # no item number read
    taken = tech_requirements.requirement_lines(page, geom, block, numbered=False)
    assert [ln.text for ln in taken] == [pieces[1].text, pieces[2].text, pieces[0].text]


def _page_of_words(*words: tuple[str, float, float]) -> LoadedDocument:
    """A page of a text layer with each word centred at (x, y) mm."""
    mm = 72 / 25.4
    lines = []
    for text, x, y in words:
        half = len(text) * 0.9
        box = ((x - half) * mm, (y - 1.5) * mm, (x + half) * mm, (y + 1.5) * mm)
        lines.append(Line(text, box, 3.5 * mm, words=[Word(text, box)]))
    page = Page(number=1, width=595.0, height=842.0, lines=lines)
    page.read_by("text_layer", "pdfium", "1", 1.0)
    return LoadedDocument(DocumentInfo(id="d", file_name="a.pdf", path="a.pdf"), Path("a.pdf"), pages=[page])


def test_a_group_specification_of_form_5_on_a_text_layer(settings, eskd):
    """The numbers of the variants in the header tell form 5 of GOST 2.113 and place its columns of quantities."""
    x = 20  # the left line of the table, mm
    doc = _page_of_words(
        ("Обозначение", x + 55, 12),
        ("Наимено-", x + 111.5, 10),
        ("вание", x + 111.5, 14),
        ("–", x + 138, 17.5),
        ("01", x + 148, 17.5),
        ("02", x + 158, 17.5),
        ("Детали", x + 111.5, 26),
        ("1", x + 16, 34),
        ("АБВГ.715211.001", x + 40, 34),
        ("Вал", x + 100, 34),
        ("2", x + 138, 34),
        ("2", x + 148, 34),
        ("-02", x + 58, 42),
        ("Вал", x + 100, 42),
        ("1", x + 158, 42),
    )
    spec = specification.parse(doc, settings.forms, eskd)
    assert spec.variants == ["–", "01", "02"]
    first, second = spec.rows
    assert (first.section, first.quantity) == ("Детали", None)
    assert {k: v.value for k, v in first.quantities.items()} == {"–": 2.0, "01": 2.0}
    assert second.designation.value == "АБВГ.715211.001-02"
    assert {k: v.value for k, v in second.quantities.items()} == {"02": 1.0}


def test_a_group_specification_by_variant_a_on_a_text_layer(settings, eskd):
    """Form 1 with the variable data (GOST 2.113, 5.5.1): the headings set the variant of the rows below and never
    join the name of the row above, wherever the columns cut them (ADR-0026)."""
    x = 20
    doc = _page_of_words(
        ("Обозначение", x + 55, 12),
        ("Наименование", x + 121.5, 12),
        ("Детали", x + 121.5, 26),
        ("1", x + 16, 34),
        ("АБВГ.715211.001", x + 40, 34),
        ("Вал", x + 100, 34),
        ("2", x + 158, 34),
        ("Переменные", x + 108, 42),
        ("данные", x + 128, 42),
        ("для", x + 142, 42),
        ("исполнений:", x + 160, 42),
        ("АБВГ.300000.001", x + 121.5, 50),
        ("Детали", x + 121.5, 58),
        ("2", x + 16, 66),
        ("АБВГ.715211.002", x + 40, 66),
        ("Втулка", x + 100, 66),
        ("1", x + 158, 66),
        ("АБВГ.300000.001-01", x + 125, 74),
        ("Детали", x + 121.5, 82),
        ("2", x + 16, 90),
        ("АБВГ.715211.003", x + 40, 90),
        ("Втулка", x + 100, 90),
        ("3", x + 158, 90),
    )
    spec = specification.parse(doc, settings.forms, eskd)
    assert spec.variants == ["–", "01"]
    got = [(r.section, r.designation.value, r.name.value, r.quantity.value, r.variant) for r in spec.rows]
    assert got == [
        ("Детали", "АБВГ.715211.001", "Вал", 2.0, None),
        ("Детали", "АБВГ.715211.002", "Втулка", 1.0, "–"),
        ("Детали", "АБВГ.715211.003", "Втулка", 3.0, "01"),
    ]


def test_a_designation_alone_in_its_column_is_a_row_and_not_a_heading(settings, eskd):
    """Before the variable data a row whose name was not read writes its designation in its own column: no heading."""
    x = 20
    doc = _page_of_words(
        ("Обозначение", x + 55, 12),
        ("Наименование", x + 121.5, 12),
        ("Документация", x + 121.5, 26),
        ("АБВГ.300000.001 СБ", x + 45, 34),
        ("АБВГ.300000.002", x + 45, 42),
    )
    spec = specification.parse(doc, settings.forms, eskd)
    assert spec.variants == []
    assert [(r.designation.value, r.variant) for r in spec.rows] == [
        ("АБВГ.300000.001 СБ", None),
        ("АБВГ.300000.002", None),
    ]
