import pytest
from kdcheck.config import load_settings
from kdcheck.normalize.designations import (
    Designations,
    shortened,
    split_variant,
    variant_designation,
    variant_number,
)

ESKD = Designations(load_settings().dictionaries)
GRAMMARS = ESKD.words.designations


@pytest.mark.parametrize(
    "text, expected",
    [(text, want) for g in GRAMMARS for text, want in g.examples.items()],
    ids=[f"{g.name}: {text}" for g in GRAMMARS for text in g.examples],
)
def test_examples_of_every_grammar(text, expected):
    assert ESKD.normalize_designation(text) == expected


@pytest.mark.parametrize(
    "text",
    [text for g in GRAMMARS for text in g.counterexamples],
    ids=[f"{g.name}: {text}" for g in GRAMMARS for text in g.counterexamples],
)
def test_counterexamples_hold_no_designation(text):
    assert ESKD.find_designations(text) == []


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Чертёж АРМ.303411.101 Корпус.pdf", "АРМ.303411.101"),  # within a longer text
        ("от 27.05.2010 по АБВГ.02.115", "АБВГ.02.115"),  # a date in lower case is not one
        ("абвг.02.115", None),  # a maker's system is compared as typed: its letters are capitals
        ("", None),
    ],
)
def test_normalize_designation(text, expected):
    assert ESKD.normalize_designation(text) == expected


def test_find_designations_with_assembly_code():
    found = [d for d, _, _ in ESKD.find_designations("АРМ.303411.000 СБ Сборочный чертёж; АРМ.303411.100")]
    assert found == ["АРМ.303411.000 СБ", "АРМ.303411.100"]


def test_designations_of_several_systems_in_one_text():
    found = ESKD.find_designations("АРМ.303411.000 СБ; АБВГ.02.115; А1Б23-1234567 СБ; АБ12.34-56.78.901")
    assert [d for d, _, _ in found] == ["АРМ.303411.000 СБ", "АБВГ.02.115", "А1Б23-1234567 СБ", "АБ12.34-56.78.901"]


def test_a_code_of_file_names_hints_and_stays_out_of_the_designation():
    stem = "АБВГ.02.115 СП Рама в сборе"
    assert ESKD.normalize_designation(stem, file_name=True) == "АБВГ.02.115"
    (_, _, end), *_ = ESKD.find_designations(stem, file_name=True)
    assert stem[end:] == " Рама в сборе"  # the code is taken up with the designation, so the name follows
    (_, _, end), *_ = ESKD.find_designations(stem)
    assert stem[end:] == " СП Рама в сборе"  # in a document «СП» is no code
    assert ESKD.normalize_designation("А1Б23-1234567СБ - Рама", file_name=True) == "А1Б23-1234567 СБ"


def test_the_look_of_unknown_designations_is_named():
    stems = ["ЖЖ-12-345 Рама", "ЖЖ-12-346 Ось", "ЖЖ-12-347 Вал(2)", "АБВГ.02.115 Плита", "IMG_2041"]
    assert ESKD.unknown_shape(stems, 0.5) == ("АА-99-999", 3)
    assert ESKD.unknown_shape(stems, 0.8) is None  # three of five
    assert ESKD.unknown_shape(["АБВГ.02.115 Плита", "АБВГ.02.116 Рама"], 0.5) is None  # every one is known


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


def test_a_kit_reads_designations_by_the_grammars_its_file_names_show():
    cut = "ИМКР.7152.001"  # a classifier designation OCR has cut: it looks like the decimal system
    assert ESKD.find_designations(cut) != []  # every grammar is on outside a kit
    classifier = ESKD.for_kit(["АБВГ.123456.001 Вал", "АБВГ.123456.002 СБ Привод"])
    assert classifier.grammars == ["classifier"] and classifier.find_designations(cut) == []
    maker = ESKD.for_kit(["АБВГ.02.115 Плита", "АБВГ.02.116 СБ Рама"])
    assert maker.grammars == ["classifier", "decimal"]  # the classifier is always on: borrowed documents keep it
    assert [d for d, _, _ in maker.find_designations("АБВГ.02.115; АРМ.303411.000; А1Б23-1234567")] == [
        "АБВГ.02.115",
        "АРМ.303411.000",
    ]
    assert ESKD.for_kit(["Плита", "IMG_2041"]) is ESKD  # no designation in the names: every grammar stays on


@pytest.mark.parametrize(
    ("text", "number"),
    [("–", "–"), ("-", "–"), ("01", "01"), ("1", "01"), ("-03", "03"), ("Кол.", None), ("", None), ("x", None)],
)
def test_the_number_of_a_variant_at_the_top_of_its_column(text, number):
    assert variant_number(text) == number


@pytest.mark.parametrize(
    ("text", "short"),
    [("-03", ("03", None)), ("– 01", ("01", None)), ("-01СБ", ("01", "СБ")), ("-01 СБ", ("01", "СБ")), ("-1", None)],
)
def test_a_shortened_designation_names_a_variant(text, short):
    assert shortened(text) == short


def test_a_variant_splits_into_its_base_and_number():
    assert split_variant("АБВГ.545648.519-02 СБ") == ("АБВГ.545648.519", "02")
    assert split_variant("АБВГ.545648.519") == ("АБВГ.545648.519", "–")
    assert variant_designation("АБВГ.545648.519", "02") == "АБВГ.545648.519-02"
    assert variant_designation("АБВГ.545648.519", "–") == "АБВГ.545648.519"
