"""Sheet images of a kit with names, signatures and dates filled with paper, for a reader outside this machine.

The kits of the customer are confidential, and their title blocks carry real names. A sheet image may be shown to a
person or an AI service outside this machine only with the cells GOST 2.104 gives to people masked
(``forms.title_block.people`` in ``configs/forms.yaml``): «№ докум.», «Подп.» and «Дата» of the change record table,
the name, signature and date of each signer, and the two «Подп. и дата» columns of the binding field. The rest of the
sheet, every field the case reads included, stays as it is.

The title block is found by its ruled lines, as the core finds it (:func:`kdcheck.layout.raster.find_corner`); a PDF
page is rendered first. A check then reads the sheets with Tesseract. First the people columns of every original sheet:
the capitalised words there, but the labels of the form, are the surnames of the kit. They stay in memory, never
printed or kept. Then each sheet whole, the original and the masked one, searched for those surnames and for any name
written with initials. The originals show the search finds them; a masked sheet with one left, or a sheet without a
title block found, is not written, and the summary names its file to be masked by hand. Only counts and file names are
printed and kept.

    uv run python eval/mask_people.py arm-1                 # data/arm/masked/arm-1 and its manifest.json
    uv run python eval/mask_people.py ПАПКА --out data/ПАПКА_2
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from pathlib import Path

import pypdfium2 as pdfium
import pytesseract
from arm_kits import DATA, KITS, ROOT, load, sha256
from kdcheck.config import PeopleCells, Settings, load_settings, settings_sha256
from kdcheck.layout import raster
from PIL import Image, ImageDraw

MM_PER_INCH = 25.4
IMAGES = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp"}
PAD_MM = 0.5  # around each masked area: a stroke on its border is masked too
# «Иванов И. И.», «ИВАНОВ И.И.», «И. И. Иванов»: a surname of three letters at least and two initials
NAME = re.compile(
    r"(?<![А-ЯЁа-яё])(?:[А-ЯЁ][А-ЯЁа-яё]{2,}\s?[А-ЯЁ]\.\s?[А-ЯЁ]\.|[А-ЯЁ]\.\s?[А-ЯЁ]\.\s?[А-ЯЁ][А-ЯЁа-яё]{2,})"
)
CAPITALISED = re.compile(r"(?<![А-ЯЁа-яё])[А-ЯЁ][а-яё]{2,}|(?<![А-ЯЁа-яё])[А-ЯЁ]{3,}(?![А-ЯЁа-яё])")
WORD = re.compile(r"[А-ЯЁа-яё]{3,}")

Point = tuple[float, float]
Page = tuple[int, Image.Image, float]


@dataclass
class Sheet:
    file: str  # path in the kit
    page: int
    sha256: str  # of the file
    form: str | None  # «1», «2» or «2а»; None without a title block found
    people_words: int  # surnames of the kit read in its people columns
    names_before: int | None  # surnames of the kit and names with initials read on the whole original sheet
    names_after: int | None  # on the whole masked sheet
    written: str | None  # path of the masked image in the output folder; None when it is not written


def count_names(text: str) -> int:
    """Names written with initials in ``text``."""
    return len(NAME.findall(text))


def surnames_in(text: str, people: PeopleCells) -> set[str]:
    """Capitalised words of ``text`` of ``people.min_surname`` letters at least but the labels of the form, lower
    case: the surnames of a people column. A shorter word there is an abbreviation or a misread stroke, and the same
    letters elsewhere on the sheet would stop it."""
    words = (w.lower() for w in CAPITALISED.findall(text) if len(w) >= people.min_surname)
    return {w for w in words if w not in people.labels}


def count_kit_names(text: str, surnames: set[str]) -> int:
    """Surnames of the kit and names with initials in ``text``."""
    return sum(w.lower() in surnames for w in WORD.findall(text)) + count_names(text)


def page_images(path: Path, pdf_dpi: int, image_dpi: int) -> Iterator[Page]:
    """Each page of a file as a grey image with its pixels per mm; an image without its resolution gets
    ``image_dpi``, and the block is searched within the scale range of it."""
    if path.suffix.lower() == ".pdf":
        pdf = pdfium.PdfDocument(path)
        try:
            for index in range(len(pdf)):
                yield index + 1, pdf[index].render(scale=pdf_dpi / 72).to_pil().convert("L"), pdf_dpi / MM_PER_INCH
        finally:
            pdf.close()
        return
    with Image.open(path) as image:
        dpi = image.info.get("dpi")
        resolution = float(dpi[0]) if dpi and dpi[0] and float(dpi[0]) >= 72 else float(image_dpi)
        for index in range(getattr(image, "n_frames", 1)):
            image.seek(index)
            yield index + 1, image.convert("L"), resolution / MM_PER_INCH


def _straightened(turn: raster.Turn, x: float, y: float) -> Point:
    """The point of the straightened image of an image point: :meth:`raster.Turn.original` undone."""
    a, (px, py) = math.radians(-turn.angle), turn.pivot
    dx, dy = x - px, y - py
    return px + dx * math.cos(a) - dy * math.sin(a), py + dx * math.sin(a) + dy * math.cos(a)


def _column_box(corner: raster.Corner, settings: Settings) -> raster.Box:
    """The people columns over the whole block, on the straightened image."""
    block, (c0, c1) = corner.block, settings.forms.title_block.people.columns
    return block.x0 + c0 * block.s, block.y0, block.x0 + c1 * block.s, block.y1


def people_areas(corner: raster.Corner, settings: Settings) -> list[list[Point]]:
    """The areas to mask on the image, as polygons: the people columns over the whole block, and the «Подп. и дата»
    columns of the binding field, from the left edge of the image to the frame."""
    block, turn = corner.block, corner.turn
    s = block.s
    boxes = [_column_box(corner, settings)]
    _, bottom = turn.original(block.x1, block.y1)
    left = min(0.0, _straightened(turn, 0.0, bottom)[0])
    binding = left + settings.forms.sheet.binding_margin * s
    rows = settings.forms.title_block.people.margin_rows
    boxes += [(left, block.y1 - r1 * s, binding, block.y1 - r0 * s) for r0, r1 in rows]
    pad = PAD_MM * s
    return [
        [
            turn.original(x, y)
            for x, y in ((x0 - pad, y0 - pad), (x1 + pad, y0 - pad), (x1 + pad, y1 + pad), (x0 - pad, y1 + pad))
        ]
        for x0, y0, x1, y1 in boxes
    ]


def find_block(gray: Image.Image, ppmm: float, settings: Settings) -> raster.Corner | None:
    return raster.find_corner(gray, ppmm, settings.forms, settings.ingest.regions)


def mask_sheet(gray: Image.Image, corner: raster.Corner, settings: Settings) -> Image.Image:
    """The sheet with the people areas filled with paper."""
    masked = gray.copy()
    draw = ImageDraw.Draw(masked)
    for polygon in people_areas(corner, settings):
        draw.polygon(polygon, fill=raster.WHITE)
    return masked


def read_people(gray: Image.Image, corner: raster.Corner, settings: Settings) -> str:
    """Text of the people columns of an original sheet, read at its own resolution."""
    return str(
        pytesseract.image_to_string(corner.turn.crop(gray, _column_box(corner, settings)), lang="rus", config="--psm 6")
    )


def read_sheet(gray: Image.Image, ppmm: float, check_dpi: int) -> str:
    """Text of the whole sheet, read at ``check_dpi``."""
    k = check_dpi / MM_PER_INCH / ppmm
    small = gray.resize((round(gray.width * k), round(gray.height * k)), Image.Resampling.LANCZOS) if k < 1 else gray
    return str(pytesseract.image_to_string(small, lang="rus", config="--psm 11"))


def _form(corner: raster.Corner) -> str:
    return "2а" if corner.block.form is None else str(corner.block.form)


def _outside_git(out: Path) -> bool:
    """Whether ``out`` keeps the masked sheets out of git: outside the repository or in its ignored ``data/``."""
    out = out.resolve()
    return not out.is_relative_to(ROOT) or out.is_relative_to(ROOT / "data")


def _pages(folder: Path, pdf_dpi: int, image_dpi: int) -> Iterator[tuple[Path, int, Page]]:
    files = sorted(p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in IMAGES | {".pdf"})
    for n, path in enumerate(files, 1):
        for page in page_images(path, pdf_dpi, image_dpi):
            print(f"{n}/{len(files)} {path.relative_to(folder)}, лист {page[0]}", flush=True)
            yield path, n, page


def mask_kit(
    folder: Path, out: Path, settings: Settings, *, pdf_dpi: int, image_dpi: int, check_dpi: int
) -> list[Sheet]:
    """Mask every sheet of ``folder`` into ``out``: the surnames of the kit first, then each sheet masked, checked."""
    people = settings.forms.title_block.people
    surnames: set[str] = set()
    sheets: list[Sheet] = []
    print("Фамилии комплекта по графам подписантов", flush=True)
    for path, _, (page, gray, ppmm) in _pages(folder, pdf_dpi, image_dpi):
        corner = find_block(gray, ppmm, settings)
        found = surnames_in(read_people(gray, corner, settings), people) if corner else set()
        surnames |= found
        rel = str(path.relative_to(folder))
        sheets.append(Sheet(rel, page, sha256(path), _form(corner) if corner else None, len(found), None, None, None))
    print("Маскирование и проверка", flush=True)
    by_page = {(s.file, s.page): s for s in sheets}
    pages_of: dict[str, int] = {}
    for s in sheets:
        pages_of[s.file] = max(pages_of.get(s.file, 0), s.page)
    for path, _, (page, gray, ppmm) in _pages(folder, pdf_dpi, image_dpi):
        rel = path.relative_to(folder)
        sheet = by_page[(str(rel), page)]
        corner = find_block(gray, ppmm, settings)
        if corner is None:
            continue
        sheet.names_before = count_kit_names(read_sheet(gray, ppmm, check_dpi), surnames)
        masked = mask_sheet(gray, corner, settings)
        sheet.names_after = count_kit_names(read_sheet(masked, ppmm, check_dpi), surnames)
        if sheet.names_after:
            continue
        name = rel.with_suffix(".png") if pages_of[str(rel)] == 1 else rel.with_name(f"{rel.stem}.{page}.png")
        target = out / name
        target.parent.mkdir(parents=True, exist_ok=True)
        masked.save(target, optimize=True)
        sheet.written = str(name)
    return sheets


def summary(sheets: list[Sheet]) -> list[str]:
    written = [s for s in sheets if s.written]
    no_block = [s for s in sheets if s.form is None]
    left = [s for s in sheets if s.form is not None and not s.written]
    people = [s for s in sheets if s.people_words]
    before = [s for s in sheets if s.names_before]
    lines = [
        f"Листов: {len(sheets)}, замаскировано и записано: {len(written)}.",
        f"Фамилии в графах подписантов прочитаны на {len(people)} листах. Проверка всего листа находит их "
        f"на {len(before)} исходных листах и ни на одном записанном маскированном.",
    ]
    if no_block:
        lines.append(f"Не записаны, основная надпись не найдена ({len(no_block)}), закрасить вручную:")
        lines += [f"  {s.file}, лист {s.page}" for s in no_block]
    if left:
        lines.append(f"Не записаны, после маскирования фамилия осталась ({len(left)}), закрасить вручную:")
        lines += [f"  {s.file}, лист {s.page}: {s.names_after}" for s in left]
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("kit", help="идентификатор комплекта из data/arm/kits.json или папка с листами")
    parser.add_argument("--out", type=Path, help="папка маскированных листов; по умолчанию data/arm/masked/<комплект>")
    parser.add_argument("--pdf-dpi", type=int, default=200, help="разрешение рендера страниц PDF")
    parser.add_argument("--image-dpi", type=int, default=300, help="разрешение изображения без записанного dpi")
    parser.add_argument("--check-dpi", type=int, default=150, help="разрешение, в котором Tesseract ищет ФИО")
    args = parser.parse_args(argv)
    kits = load(KITS)
    if args.kit in kits:
        folder, out = DATA / kits[args.kit]["path"], args.out or DATA / "masked" / args.kit
    else:
        folder = Path(args.kit)
        if not folder.is_dir():
            parser.error(f"нет комплекта {args.kit} в реестре и нет такой папки")
        out = args.out or DATA / "masked" / folder.name
    if not _outside_git(out):
        parser.error(f"{out}: маскированные листы — данные заказчика, их место вне git, в data/ или вне репозитория")
    if out.exists() and any(out.iterdir()):
        parser.error(f"{out} не пуста: удалите её или укажите другую в --out")
    settings = load_settings(use_ocr=False)
    sheets = mask_kit(folder, out, settings, pdf_dpi=args.pdf_dpi, image_dpi=args.image_dpi, check_dpi=args.check_dpi)
    out.mkdir(parents=True, exist_ok=True)
    manifest = {
        "source": str(folder),
        "settings_sha256": settings_sha256(settings),
        "tesseract": str(pytesseract.get_tesseract_version()),
        "pdf_dpi": args.pdf_dpi,
        "check_dpi": args.check_dpi,
        "sheets": [asdict(s) for s in sheets],
    }
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("\n".join(summary(sheets)))
    return 0 if all(s.written for s in sheets) else 1


if __name__ == "__main__":
    sys.exit(main())
