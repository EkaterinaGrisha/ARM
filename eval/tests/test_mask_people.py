"""Names, signatures and dates masked on a sheet image before it leaves the machine (eval/mask_people.py)."""

from pathlib import Path

import mask_people
import pytest
from kdcheck.config import load_settings
from kdcheck.layout import raster
from PIL import Image

DEMO = Path(__file__).resolve().parents[2] / "samples" / "demo_kit"
DRAWING = DEMO / "Чертежи" / "АРМ.303411.001 Шестерня солнечная.pdf"


@pytest.fixture(scope="module")
def settings():
    return load_settings(use_ocr=False)


@pytest.fixture(scope="module")
def sheet():
    [(_, gray, ppmm)] = list(mask_people.page_images(DRAWING, 200, 300))
    return gray, ppmm


def _ink(gray: Image.Image, box: raster.Box) -> int:
    """Dark pixels of ``box``."""
    part = raster.crop(gray, box)
    return sum(part.histogram()[:128])


def _mm(corner: raster.Corner, u0: float, v0: float, u1: float, v1: float) -> raster.Box:
    """A box mm from the top left corner of the block, on the image (the synthetic sheet is not turned)."""
    b = corner.block
    return (b.x0 + u0 * b.s, b.y0 + v0 * b.s, b.x0 + u1 * b.s, b.y0 + v1 * b.s)


def _masked(sheet, settings):
    gray, ppmm = sheet
    corner = mask_people.find_block(gray, ppmm, settings)
    return gray, mask_people.mask_sheet(gray, corner, settings), corner


def test_the_names_of_the_signers_and_the_change_record_are_masked(sheet, settings):
    gray, masked, corner = _masked(sheet, settings)
    signers = _mm(corner, 17.5, 25, 64.5, 54.5)  # names, signatures and dates of the signers in form 1
    assert _ink(gray, signers) > 0 and _ink(masked, signers) == 0


def test_the_fields_the_case_reads_stay(sheet, settings):
    gray, masked, corner = _masked(sheet, settings)
    for box in (
        _mm(corner, 66, 1, 184, 14),  # designation
        _mm(corner, 66, 16, 134, 39),  # name
        _mm(corner, 136, 21, 184, 34),  # litera, mass, scale
        _mm(corner, 0.5, 0.5, 6.5, 19.5),  # «Изм.»: the number of the last change
    ):
        assert raster.crop(masked, box).tobytes() == raster.crop(gray, box).tobytes()


def test_the_signature_columns_of_the_binding_field_are_masked_and_the_archive_number_stays(sheet, settings):
    gray, masked, corner = _masked(sheet, settings)
    b, s = corner.block, corner.block.s
    signature = (0, b.y1 - 59 * s, 20 * s, b.y1 - 26 * s)  # column 20 «Подп. и дата»
    archive = (0, b.y1 - 24 * s, 20 * s, b.y1 - 1 * s)  # column 19 «Инв. № подл.»
    assert _ink(gray, signature) > 0 and _ink(masked, signature) == 0
    assert raster.crop(masked, archive).tobytes() == raster.crop(gray, archive).tobytes()


def test_a_sheet_without_a_title_block_has_no_block_to_mask(settings):
    assert mask_people.find_block(Image.new("L", (1700, 1200), 255), 200 / 25.4, settings) is None


def test_names_with_initials_are_found_and_other_text_is_not():
    text = "Разраб. Иванов И.И.\nПров. П. С. Петров\nН.контр. СИДОРОВА А. Б.\nУтв. Кузнецов"
    assert mask_people.count_names(text) == 3  # without initials, a surname is searched among those of the kit
    assert mask_people.count_names("Сталь 45 ГОСТ 1050-2013\nАРМ.303411.001 СБ\nМасса 3,01 М 1:2") == 0


def test_the_surnames_of_a_people_column_are_its_capitalised_words_but_the_labels(settings):
    people = settings.forms.title_block.people
    column = "Изм. Лист № докум. Подп. Дата\nРазраб. Иванов\nПров. ПЕТРОВ 12.03 ГАЗ\nН.контр. Сидорова\nУтв. Сдо"
    assert mask_people.surnames_in(column, people) == {"иванов", "петров", "сидорова"}  # «ГАЗ», «Сдо» are too short


def test_a_surname_of_the_kit_is_found_anywhere_on_the_sheet():
    surnames = {"иванов", "сидорова"}
    assert mask_people.count_kit_names("Согласовано ИВАНОВ\nСталь 45", surnames) == 1
    assert mask_people.count_kit_names("Утв. П. С. Петров", surnames) == 1  # a name with initials, not of the kit
    assert mask_people.count_kit_names("Иванова\nСталь 45 ГОСТ 1050-2013", surnames) == 0


def test_masked_sheets_stay_out_of_git(tmp_path):
    assert mask_people._outside_git(tmp_path)
    assert mask_people._outside_git(mask_people.ROOT / "data" / "arm" / "masked" / "arm-1")
    assert not mask_people._outside_git(mask_people.ROOT / "eval" / "masked")
