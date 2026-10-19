"""The PNG of a drawing: our own PDF rasterised by PDFium (``pypdfium2``, optional extra ``png``).  The one pypdfium2 module.

Only a file this toolkit has just written is rendered: the PDF comes from ``dxf.render_pdf`` in the same call, never
from a caller, so PDFium never parses untrusted input here.  (``drawing_read`` reads other people's PDFs with pdfminer.six.)

* The page is rendered as 8-bit grey, black ink on white, at 150 dpi, or less when the long side would pass 4000 pixels
  (an A0 sheet is 85 dpi).  The pixels are encoded as a PNG by this module with the standard library only: the signature,
  IHDR, one IDAT (zlib level 9) and IEND, no text, time or gamma chunks, so the file holds nothing that varies by run.
* Reproducibility: the same PDF gives the same PNG bytes on one machine and one PDFium build, in any process (the PDF is
  the same for every ``PYTHONHASHSEED``, and PDFium's rendering of it is deterministic).  Across machines the bytes are
  not promised: anti-aliasing belongs to the PDFium build and the zlib stream to the zlib version.
* PDFium is not thread safe and the tools run on a thread pool, so every call into it holds one lock.
"""

from __future__ import annotations

import struct
import threading
import zlib

from eng_toolkit.errors import EngineUnavailable
from eng_toolkit.licenses import install_hint

MODULE = DIST = "pypdfium2"
DPI = 150.0
MAX_SIDE_PX = 4000
_LOCK = threading.Lock()


def require_engine():
    """Import pypdfium2, or raise ``engine.unavailable`` with the install command."""
    try:
        import pypdfium2
    except (ImportError, OSError) as exc:
        raise EngineUnavailable(MODULE, DIST, exc, install=install_hint(DIST)) from exc
    return pypdfium2


def pixels_per_mm(width_mm: float, height_mm: float) -> float:
    """150 dpi, reduced so that the long side is at most 4000 pixels."""
    return min(DPI / 25.4, MAX_SIDE_PX / max(width_mm, height_mm))


def _chunk(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)


def encode_png(gray: bytes, width: int, height: int) -> bytes:
    """An 8-bit greyscale PNG of ``height`` rows of ``width`` bytes, with filter type 0 on every row and no ancillary chunks."""
    if len(gray) != width * height:
        raise ValueError("the pixel data does not match the size")
    rows = b"".join(b"\x00" + gray[y * width : (y + 1) * width] for y in range(height))
    header = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", header) + _chunk(b"IDAT", zlib.compress(rows, 9)) + _chunk(b"IEND", b"")


def render_png(pdf: bytes, width_mm: float, height_mm: float) -> tuple[bytes, int, int, float]:
    """(png bytes, width px, height px, dpi) of the first page of ``pdf``; ``engine.unavailable`` without pypdfium2."""
    pdfium = require_engine()
    per_mm = pixels_per_mm(width_mm, height_mm)
    scale = per_mm * 25.4 / 72.0  # pixels per PDF point
    with _LOCK:
        document = pdfium.PdfDocument(pdf)
        try:
            bitmap = document[0].render(scale=scale, grayscale=True)
            array = bitmap.to_numpy()
        finally:
            document.close()
    height, width = array.shape[:2]
    return encode_png(array.tobytes(), width, height), width, height, round(per_mm * 25.4, 2)


__all__ = ["DPI", "MAX_SIDE_PX", "encode_png", "pixels_per_mm", "render_png", "require_engine"]
