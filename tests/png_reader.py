"""An independent reader of the PNGs the preview writes: ``struct`` and ``zlib`` only, nothing from eng_toolkit.

``read_png`` checks the container (signature, chunk order, every CRC, one IHDR first, IEND last, nothing after it), undoes any
of the five PNG filters and returns (width, height, rows) with rows an (h, w, 3) numpy array.  Used by the preview tests, which
therefore check the pixels with code that shares nothing with the encoder.
"""

import struct
import zlib

import numpy as np

SIGNATURE = b"\x89PNG\r\n\x1a\n"


def read_png(data: bytes) -> tuple[int, int, np.ndarray]:
    assert data[:8] == SIGNATURE, "PNG signature"
    pos = 8
    chunks = []
    while pos < len(data):
        (length,) = struct.unpack(">I", data[pos : pos + 4])
        kind = data[pos + 4 : pos + 8]
        payload = data[pos + 8 : pos + 8 + length]
        (crc,) = struct.unpack(">I", data[pos + 8 + length : pos + 12 + length])
        assert crc == zlib.crc32(kind + payload) & 0xFFFFFFFF, f"CRC of {kind!r}"
        chunks.append((kind, payload))
        pos += 12 + length
    assert pos == len(data), "no bytes after IEND"
    assert chunks[0][0] == b"IHDR" and chunks[-1][0] == b"IEND" and len(chunks[-1][1]) == 0
    width, height, depth, colour, compression, filtering, interlace = struct.unpack(">IIBBBBB", chunks[0][1])
    assert (depth, colour, compression, filtering, interlace) == (8, 2, 0, 0, 0), "8-bit truecolour, not interlaced"
    raw = zlib.decompress(b"".join(p for k, p in chunks if k == b"IDAT"))
    stride = width * 3
    assert len(raw) == height * (stride + 1)
    out = np.zeros((height, stride), dtype=np.int64)
    for y in range(height):
        kind = raw[y * (stride + 1)]
        line = np.frombuffer(raw, dtype=np.uint8, count=stride, offset=y * (stride + 1) + 1).astype(np.int64)
        prev = out[y - 1] if y else np.zeros(stride, dtype=np.int64)
        if kind == 0:
            row = line
        elif kind == 2:
            row = (line + prev) & 255
        elif kind == 1:  # Sub: left neighbour 3 bytes back
            row = line.copy()
            for i in range(3, stride):
                row[i] = (row[i] + row[i - 3]) & 255
        elif kind == 3:
            row = line.copy()
            for i in range(stride):
                left = row[i - 3] if i >= 3 else 0
                row[i] = (row[i] + ((left + prev[i]) >> 1)) & 255
        elif kind == 4:
            row = line.copy()
            for i in range(stride):
                a = row[i - 3] if i >= 3 else 0
                b = prev[i]
                c = prev[i - 3] if i >= 3 else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pred = a if pa <= pb and pa <= pc else (b if pb <= pc else c)
                row[i] = (row[i] + pred) & 255
        else:
            raise AssertionError(f"unknown filter {kind}")
        out[y] = row
    return width, height, out.reshape(height, width, 3).astype(np.uint8)
