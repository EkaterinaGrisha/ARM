"""Light scans of the demo kit: rendered by PDFium (pypdfium2), turned a little, with noise from a seeded generator.

The synthetic kits scan their sheets with :mod:`synth.degrade`; the demo kit keeps these, so it stays as committed.
"""

from __future__ import annotations

import io
import random

import pypdfium2 as pdfium
from PIL import Image, ImageFilter
from reportlab.lib.utils import ImageReader

from synth.render import MM, Sheet, new_canvas


def noise_plane(size: tuple[int, int], sigma: float, rng: random.Random) -> Image.Image:
    """Grey noise around 128 with the deviation ``sigma``: the mean of four uniform images is close to normal."""
    planes = [Image.frombytes("L", size, rng.randbytes(size[0] * size[1])) for _ in range(4)]
    mean = Image.blend(Image.blend(planes[0], planes[1], 0.5), Image.blend(planes[2], planes[3], 0.5), 0.5)
    return Image.blend(Image.new("L", size, 128), mean, sigma / 37)  # the mean of four uniform bytes deviates by 37


def scan(pdf: bytes, *, rng: random.Random, noise: random.Random, unreadable: bool = False) -> Image.Image:
    """The first page as a grey scan: rendered by PDFium, turned a little, with noise."""
    dpi = 40 if unreadable else 150
    img: Image.Image = pdfium.PdfDocument(pdf)[0].render(scale=dpi / 72, grayscale=True).to_pil()
    if unreadable:
        img = img.resize((img.width * 150 // 40, img.height * 150 // 40)).filter(ImageFilter.GaussianBlur(4))
        img = img.point(lambda v: 112 + v * 30 // 255)
    img = img.rotate(rng.uniform(-0.8, 0.8), resample=Image.Resampling.BICUBIC, expand=False, fillcolor=255)
    img = Image.blend(img, noise_plane(img.size, 28 if unreadable else 12, noise), 0.25 if unreadable else 0.08)
    return img.point(lambda v: min(255, int(v * (0.92 if unreadable else 1.0) + (6 if not unreadable else 0))))


def scan_pdf(jpeg: bytes, fmt: str) -> bytes:
    """A page that holds only the scan, as a scanner writes it; the JPEG is embedded as is."""
    c = new_canvas()
    sh = Sheet(c, fmt)
    c.drawImage(ImageReader(io.BytesIO(jpeg)), 0, 0, sh.w * MM, sh.h * MM, preserveAspectRatio=True, anchor="c")
    return c.getpdfdata()
