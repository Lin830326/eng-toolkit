"""Hand-written PDF files for the reader tests: objects assembled as bytes with a computed cross-reference table, no PDF library.

Known strings at known places, known line lengths, and the hostile constructs the reader must survive.
"""

from __future__ import annotations

import zlib

A4 = (595.2756, 841.8898)  # points
A3_LANDSCAPE = (1190.5512, 841.8898)


def assemble(objects: list[bytes], root: int = 1, trailer_extra: bytes = b"", version: bytes = b"1.4") -> bytes:
    """A PDF from numbered object bodies (object i+1 is objects[i]); the xref table and trailer are computed."""
    out = bytearray(b"%PDF-" + version + b"\n")
    offsets = []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode() + b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root {root} 0 R ".encode() + trailer_extra + f">>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


def stream(content: bytes, extra: bytes = b"") -> bytes:
    return b"<< /Length %d " % len(content) + extra + b">>\nstream\n" + content + b"\nendstream"


def page_objects(content: bytes, size=A4, rotate: int = 0, first: int = 3) -> list[bytes]:
    """The page object and its content stream; ``first`` is the page's own object number."""
    rot = f"/Rotate {rotate} ".encode() if rotate else b""
    page = f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {size[0]} {size[1]}] ".encode() + rot + f"/Contents {first + 1} 0 R /Resources << /Font << /F1 << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> >> >> >>".encode()
    return [page, stream(content)]


def document(contents: list[tuple[bytes, tuple[float, float], int]], extra_catalog: bytes = b"") -> bytes:
    """A PDF with one page per (content stream, size, rotation)."""
    objects: list[bytes] = [b"", b""]
    kids = []
    for index, (content, size, rotate) in enumerate(contents):
        number = 3 + index * 2
        kids.append(f"{number} 0 R")
        objects += page_objects(content, size, rotate, first=number)
    objects[0] = b"<< /Type /Catalog /Pages 2 0 R " + extra_catalog + b">>"
    objects[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(contents)} >>".encode()
    return assemble(objects)


# the content of the main test page, with the numbers worked out in the tests
MAIN = (
    b"BT /F1 12 Tf 72 700 Td (HELLO FL-001) Tj ET\n"
    b"BT /F1 10 Tf 72 650 Td (MATERIAL AL6061) Tj ET\n"
    b"0.5 w 100 100 m 300 100 l S\n"  # 200 pt, solid, 0.5 pt wide
    b"[3 2] 0 d 100 120 m 200 120 l S [] 0 d\n"  # 100 pt, dashed
    b"1 w 50 200 100 20 re S\n"  # a rectangle: perimeter 240 pt
    b"0.25 w 200 400 m 250 450 300 450 350 400 c S\n"  # one cubic Bezier
    b"400 400 m 410 400 l 410 410 l h f\n"  # a small filled triangle
)


def active_content(kind: str) -> bytes:
    """A one-page PDF carrying active content of the named kind (never executed by the reader, only reported)."""
    page = document([(b"0 0 m 10 10 l S\n", A4, 0)])
    if kind == "javascript":
        return page.replace(b"/Type /Catalog", b"/Type /Catalog /OpenAction << /S /JavaScript /JS (app.alert\\(1\\)) >>")
    if kind == "obfuscated-javascript":  # the name /JavaScript written with a hex escape
        return page.replace(b"/Type /Catalog", b"/Type /Catalog /OpenAction << /S /J#61vaScript /JS (app.alert\\(1\\)) >>")
    if kind == "uri":
        link = b"<< /Type /Annot /Subtype /Link /Rect [0 0 10 10] /A << /S /URI /URI (http://evil.example/x) >> >>"
        objects = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>"]
        objects += [page_objects(b"0 0 m 10 10 l S\n")[0].replace(b"/Contents", b"/Annots [5 0 R] /Contents"), stream(b"0 0 m 10 10 l S\n"), link]
        return assemble(objects)
    if kind == "launch":
        return page.replace(b"/Type /Catalog", b"/Type /Catalog /OpenAction << /S /Launch /F (calc.exe) >>")
    if kind == "embedded":
        return page.replace(b"/Type /Catalog", b"/Type /Catalog /Names << /EmbeddedFiles << /Names [(a.exe) 9 0 R] >> >>")
    raise ValueError(kind)


def hidden_in_object_stream() -> bytes:
    """A PDF 1.5 file whose JavaScript action lives inside a compressed object stream, found only by parsing, not by scanning the bytes."""
    inner = [b"<< /S /JavaScript /JS (app.alert\\(1\\)) >>"]
    header = b""
    body = b""
    for number, obj in zip([6], inner):
        header += f"{number} {len(body)} ".encode()
        body += obj + b"\n"
    payload = zlib.compress(header + body)
    objects = {
        1: b"<< /Type /Catalog /Pages 2 0 R /OpenAction 6 0 R >>",
        2: b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        3: page_objects(b"0 0 m 10 10 l S\n")[0],
        4: stream(b"0 0 m 10 10 l S\n"),
        5: b"<< /Type /ObjStm /N 1 /First %d /Length %d /Filter /FlateDecode >>\nstream\n" % (len(header), len(payload)) + payload + b"\nendstream",
    }
    out = bytearray(b"%PDF-1.5\n")
    offsets = {}
    for number, body_ in objects.items():
        offsets[number] = len(out)
        out += f"{number} 0 obj\n".encode() + body_ + b"\nendobj\n"
    xref_offset = len(out)
    rows = bytearray()
    entries = [(0, 0, 255)] + [(1, offsets[n], 0) for n in (1, 2, 3, 4, 5)] + [(2, 5, 0)] + [(1, xref_offset, 0)]
    for kind, a, b in entries:
        rows += bytes([kind]) + a.to_bytes(3, "big") + bytes([b])
    packed = zlib.compress(bytes(rows))
    out += b"7 0 obj\n<< /Type /XRef /Size 8 /W [1 3 1] /Root 1 0 R /Length %d /Filter /FlateDecode >>\nstream\n" % len(packed) + packed + b"\nendstream\nendobj\n"
    out += f"startxref\n{xref_offset}\n%%EOF\n".encode()
    return bytes(out)


def flate_bomb(megabytes: int) -> bytes:
    """A one-page PDF whose content stream is Flate data that expands to ``megabytes`` MB of spaces."""
    compressor = zlib.compressobj(9)
    chunks = []
    block = b" " * (1 << 20)
    for _ in range(megabytes):
        chunks.append(compressor.compress(block))
    chunks.append(compressor.flush())
    data = b"".join(chunks)
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>", page_objects(b"")[0], stream(data, b"/Filter /FlateDecode ")]
    return assemble(objects)


def cyclic_pages() -> bytes:
    """A page tree whose node lists itself as a kid."""
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [2 0 R 3 0 R] /Count 2 >>", *page_objects(b"0 0 m 10 10 l S\n")]
    return assemble(objects)


def self_referencing_form() -> bytes:
    """A form XObject that draws itself."""
    form = b"<< /Type /XObject /Subtype /Form /BBox [0 0 100 100] /Resources << /XObject << /Fm0 5 0 R >> >> /Length 8 >>\nstream\n/Fm0 Do\n\nendstream"
    page = b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Contents 4 0 R /Resources << /XObject << /Fm0 5 0 R >> >> >>"
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>", page, stream(b"/Fm0 Do\n"), form]
    return assemble(objects)
