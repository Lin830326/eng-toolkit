"""The PNG of a chart: our SVG -> a one-page PDF (:mod:`_svgpdf`) -> PDFium (``drawing/raster.py``, optional extra ``png``) -> an RGB PNG.

* Size: ``PNG_WIDTH`` = 1600 px wide at the SVG's aspect (the SVG is 800 px wide by default, so 2x), or the SVG's own width when that is
  larger (a 2400 px SVG is a 2400 px PNG); never more than ``MAX_SIDE_PX`` = 4000 px on the long side.
* Colours are those of the light theme for ``theme=auto`` (a PNG has no dark-mode switch); ``dark`` and ``print`` are drawn as such.
* The PNG is written by this module with the standard library and numpy: signature, IHDR, one IDAT (zlib level 9, filter 'Up' on every
  row), IEND, no ancillary chunks, so nothing in the file varies by run.  The same SVG gives the same bytes on one machine and one
  PDFium build; across machines the anti-aliasing (PDFium build) and the zlib stream (zlib version) may differ.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass

import numpy as np

from eng_toolkit.domains.drawing import raster
from eng_toolkit.domains.plot._svgpdf import svg_to_pdf
from eng_toolkit.errors import EngineUnavailable

PNG_WIDTH = 1600
MAX_SIDE_PX = 4000


@dataclass
class Picture:
    png: bytes
    width: int
    height: int
    scale: float  # PNG pixels per SVG pixel
    substituted: int  # characters drawn as '?' (no glyph in the PDF standard fonts)
    cjk: bool


def _chunk(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)


def encode_png_rgb(rgb: bytes, width: int, height: int) -> bytes:
    """An 8-bit RGB PNG; every row uses filter type 2 (Up), which makes the flat white of a chart a run of zeros."""
    if len(rgb) != width * height * 3:
        raise ValueError("the pixel data does not match the size")
    rows = np.frombuffer(rgb, dtype=np.uint8).reshape(height, width * 3)
    filtered = np.empty((height, width * 3 + 1), dtype=np.uint8)
    filtered[:, 0] = 2
    filtered[0, 1:] = rows[0]
    filtered[0, 0] = 0
    filtered[1:, 1:] = rows[1:] - rows[:-1]  # uint8 arithmetic wraps modulo 256, as the filter requires
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", header) + _chunk(b"IDAT", zlib.compress(filtered.tobytes(), 9)) + _chunk(b"IEND", b"")


def scale_for(width: float, height: float) -> float:
    """PNG pixels per SVG pixel: 1600 px wide, at least 1, at most 4000 px on the long side.

    PDFium sizes the bitmap with ceil(size x scale); the scale is taken a hair (1e-9) lower so that a rounding error in
    1600 / 900 x 900 can never give 1601 pixels."""
    return min(max(PNG_WIDTH / width, 1.0), MAX_SIDE_PX / max(width, height)) * (1 - 1e-9)


def available() -> None:
    """Raise ``engine.unavailable`` (with the install command) when pypdfium2 is not installed."""
    raster.require_engine()


def render(svg: str) -> Picture:
    """The PNG of ``svg``; ``engine.unavailable`` (:class:`EngineUnavailable`) without pypdfium2."""
    available()
    converted = svg_to_pdf(svg)
    scale = scale_for(converted.width, converted.height)
    rgb, width, height = raster.render_rgb(converted.pdf, scale)
    return Picture(encode_png_rgb(rgb, width, height), width, height, scale, converted.substituted, converted.cjk)


__all__ = ["EngineUnavailable", "MAX_SIDE_PX", "PNG_WIDTH", "Picture", "available", "encode_png_rgb", "render", "scale_for"]
