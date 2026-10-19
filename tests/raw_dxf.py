"""Hand-written DXF text for the reader tests: group code / value pairs assembled as strings, no DXF library.

The readers are tested on files like these, not on files ezdxf wrote, so a mistake that ezdxf would make on both sides
(writing and reading) cannot hide.  Every expected number in the tests comes from the coordinates written here.
"""

from __future__ import annotations

V12, V2000, V2004, V2007, V2018 = "AC1009", "AC1015", "AC1018", "AC1021", "AC1032"


def tags(*pairs) -> str:
    """Flat (code, value, code, value, ...) or an iterable of (code, value) tuples to DXF lines."""
    flat = list(pairs)
    if flat and isinstance(flat[0], tuple):
        flat = [x for pair in flat for x in pair]
    return "".join(f"{c}\n{v}\n" for c, v in zip(flat[0::2], flat[1::2]))


def entity(kind: str, *pairs, version: str = V2000, subclass: str = "", handle: int | None = None) -> str:
    """One entity.  From R2000 on an entity has a handle and AcDb subclass markers; R12 has neither."""
    head = tags(0, kind)
    if version != V12:
        head += tags(5, f"{handle if handle is not None else _next_handle():X}", 100, "AcDbEntity")
    return head + _layer_first(pairs, version, subclass)


_counter = [0x100]


def _next_handle() -> int:
    _counter[0] += 1
    return _counter[0]


def _layer_first(pairs, version: str, subclass: str) -> str:
    pairs = list(pairs)
    body = tags(*pairs)
    if version != V12 and subclass:
        # layer (8) belongs to AcDbEntity; everything after the subclass marker belongs to the entity's own class
        layer = [(pairs[i], pairs[i + 1]) for i in range(0, len(pairs), 2) if pairs[i] in (8, 6, 62, 370, 67)]
        rest = [(pairs[i], pairs[i + 1]) for i in range(0, len(pairs), 2) if pairs[i] not in (8, 6, 62, 370, 67)]
        body = tags(*[x for p in layer for x in p]) + tags(100, subclass) + tags(*[x for p in rest for x in p])
    return body


def line(p1, p2, layer="0", version=V2000) -> str:
    return entity("LINE", 8, layer, 10, p1[0], 20, p1[1], 30, 0, 11, p2[0], 21, p2[1], 31, 0, version=version, subclass="AcDbLine")


def circle(c, r, layer="0", version=V2000) -> str:
    return entity("CIRCLE", 8, layer, 10, c[0], 20, c[1], 30, 0, 40, r, version=version, subclass="AcDbCircle")


def arc(c, r, start, end, layer="0", version=V2000) -> str:
    body = tags(8, layer, 10, c[0], 20, c[1], 30, 0, 40, r)
    head = tags(0, "ARC") + (tags(5, f"{_next_handle():X}", 100, "AcDbEntity") if version != V12 else "")
    rest = tags(50, start, 51, end)
    if version == V12:
        return head + body + rest
    return head + tags(8, layer) + tags(100, "AcDbCircle", 10, c[0], 20, c[1], 30, 0, 40, r, 100, "AcDbArc") + rest


def text(s, at, height=2.5, layer="0", version=V2000, encoded: bytes | None = None) -> str:
    return entity("TEXT", 8, layer, 10, at[0], 20, at[1], 30, 0, 40, height, 1, s, version=version, subclass="AcDbText")


def lwpolyline(points, layer="0", closed=False, bulges=None) -> str:
    out = tags(0, "LWPOLYLINE", 5, f"{_next_handle():X}", 100, "AcDbEntity", 8, layer, 100, "AcDbPolyline", 90, len(points), 70, 1 if closed else 0)
    for i, (x, y) in enumerate(points):
        out += tags(10, x, 20, y)
        if bulges and bulges[i]:
            out += tags(42, bulges[i])
    return out


def dimension(kind: int, *, layer="DIM", style="STANDARD", override=None, block=None, pts: dict | None = None, rotation=None, xdata_dstyle: dict | None = None, version=V2000, extra: str = "") -> str:
    """A DIMENSION.  ``pts`` maps group code -> (x, y); ``kind`` is group 70 (0 linear, 1 aligned, 2 angular, 3 diameter, ...)."""
    pts = pts or {}
    out = tags(0, "DIMENSION")
    if version != V12:
        out += tags(5, f"{_next_handle():X}", 100, "AcDbEntity")
    out += tags(8, layer)
    if version != V12:
        out += tags(100, "AcDbDimension")
    if block:
        out += tags(2, block)
    d = pts.get(10, (0, 0))
    out += tags(10, d[0], 20, d[1], 30, 0)
    m = pts.get(11, (0, 0))
    out += tags(11, m[0], 21, m[1], 31, 0, 70, kind)
    if override is not None:
        out += tags(1, override)
    out += tags(3, style)
    sub = {0: "AcDbAlignedDimension", 1: "AcDbAlignedDimension", 2: "AcDb2LineAngularDimension", 3: "AcDbDiametricDimension", 4: "AcDbRadialDimension", 5: "AcDb3PointAngularDimension", 6: "AcDbOrdinateDimension"}[kind & 0x0F]
    if version != V12:
        out += tags(100, sub)
    for code in (13, 14, 15, 16):
        if code in pts:
            x, y = pts[code]
            out += tags(code, x, code + 10, y, code + 20, 0)
    if rotation is not None:
        out += tags(50, rotation)
    if version != V12 and (kind & 0x0F) == 0:
        out += tags(100, "AcDbRotatedDimension")
    out += extra
    if xdata_dstyle:
        out += tags(1001, "ACAD", 1000, "DSTYLE", 1002, "{")
        for code, value in xdata_dstyle.items():
            out += tags(1070, code, 1040 if isinstance(value, float) else 1070, value)
        out += tags(1002, "}")
    return out


def insert(block, at, layer="0", scale=(1, 1, 1), rotation=0.0, attribs=(), columns=1, rows=1, col_spacing=0.0, row_spacing=0.0, version=V2000) -> str:
    out = tags(0, "INSERT")
    if version != V12:
        out += tags(5, f"{_next_handle():X}", 100, "AcDbEntity")
    out += tags(8, layer)
    if version != V12:
        out += tags(100, "AcDbBlockReference")
    if attribs:
        out += tags(66, 1)
    out += tags(2, block, 10, at[0], 20, at[1], 30, 0, 41, scale[0], 42, scale[1], 43, scale[2], 50, rotation)
    if columns != 1 or rows != 1:
        out += tags(70, columns, 71, rows, 44, col_spacing, 45, row_spacing)
    for tag, value, pos in attribs:
        out += tags(0, "ATTRIB")
        if version != V12:
            out += tags(5, f"{_next_handle():X}", 100, "AcDbEntity")
        out += tags(8, layer)
        if version != V12:
            out += tags(100, "AcDbText")
        out += tags(10, pos[0], 20, pos[1], 30, 0, 40, 2.5, 1, value)
        if version != V12:
            out += tags(100, "AcDbAttribute")
        out += tags(2, tag, 70, 0)
    if attribs:
        out += tags(0, "SEQEND")
        if version != V12:
            out += tags(5, f"{_next_handle():X}", 100, "AcDbEntity")
        out += tags(8, layer)
    return out


def mtext(s, at, height=2.5, layer="0") -> str:
    return tags(0, "MTEXT", 5, f"{_next_handle():X}", 100, "AcDbEntity", 8, layer, 100, "AcDbMText", 10, at[0], 20, at[1], 30, 0, 40, height, 71, 1, 1, s)


def block(name, *entities, version=V2000, flags=0, xref_path=None, layer="0") -> str:
    out = tags(0, "BLOCK")
    if version != V12:
        out += tags(5, f"{_next_handle():X}", 100, "AcDbEntity")
    out += tags(8, layer)
    if version != V12:
        out += tags(100, "AcDbBlockBegin")
    out += tags(2, name, 70, flags, 10, 0, 20, 0, 30, 0, 3, name)
    if xref_path:
        out += tags(1, xref_path)
    out += "".join(entities)
    out += tags(0, "ENDBLK")
    if version != V12:
        out += tags(5, f"{_next_handle():X}", 100, "AcDbEntity")
    out += tags(8, layer)
    if version != V12:
        out += tags(100, "AcDbBlockEnd")
    return out


def attdef(tag, default, at, layer="0", version=V2000) -> str:
    out = tags(0, "ATTDEF")
    if version != V12:
        out += tags(5, f"{_next_handle():X}", 100, "AcDbEntity")
    out += tags(8, layer)
    if version != V12:
        out += tags(100, "AcDbText")
    out += tags(10, at[0], 20, at[1], 30, 0, 40, 2.5, 1, default)
    if version != V12:
        out += tags(100, "AcDbAttributeDefinition")
    out += tags(3, tag, 2, tag, 70, 0)
    return out


def layer_row(name, color=7, linetype="CONTINUOUS", flags=0, version=V2000) -> str:
    out = tags(0, "LAYER")
    if version != V12:
        out += tags(5, f"{_next_handle():X}", 100, "AcDbSymbolTableRecord", 100, "AcDbLayerTableRecord")
    return out + tags(2, name, 70, flags, 62, color, 6, linetype)


def dimstyle_row(name, version=V2000, **values) -> str:
    """values: DXF group code -> value, e.g. {144: 2.0} for DIMLFAC."""
    out = tags(0, "DIMSTYLE")
    if version != V12:
        out += tags(105, f"{_next_handle():X}", 100, "AcDbSymbolTableRecord", 100, "AcDbDimStyleTableRecord")
    out += tags(2, name, 70, 0)
    for code, value in values.items():
        out += tags(int(code), value)
    return out


def table(name, *rows, version=V2000) -> str:
    out = tags(0, "TABLE", 2, name)
    if version != V12:
        out += tags(5, f"{_next_handle():X}", 100, "AcDbSymbolTable")
    out += tags(70, len(rows))
    return out + "".join(rows) + tags(0, "ENDTAB")


def section(name, *items) -> str:
    return tags(0, "SECTION", 2, name) + "".join(items) + tags(0, "ENDSEC")


def document(
    version=V2000,
    *,
    insunits: int | None = None,
    measurement: int | None = None,
    codepage: str | None = None,
    header_extra: str = "",
    layers: tuple[str, ...] = (),
    dimstyles: tuple[str, ...] = (),
    blocks: tuple[str, ...] = (),
    entities: tuple[str, ...] = (),
    eof: bool = True,
) -> str:
    """A whole file.  ``layers`` and ``dimstyles`` are rows made by :func:`layer_row` and :func:`dimstyle_row`."""
    head = tags(9, "$ACADVER", 1, version)
    if codepage:
        head += tags(9, "$DWGCODEPAGE", 3, codepage)
    if measurement is not None:
        head += tags(9, "$MEASUREMENT", 70, measurement)
    if insunits is not None:
        head += tags(9, "$INSUNITS", 70, insunits)
    head += header_extra
    parts = [section("HEADER", head)]
    tables = []
    if version != V12 or layers:
        tables.append(table("LTYPE", tags(0, "LTYPE") + (tags(5, "14", 100, "AcDbSymbolTableRecord", 100, "AcDbLinetypeTableRecord") if version != V12 else "") + tags(2, "CONTINUOUS", 70, 0, 3, "Solid line", 72, 65, 73, 0, 40, 0.0), version=version))
    if layers:
        tables.append(table("LAYER", *layers, version=version))
    if dimstyles:
        tables.append(table("DIMSTYLE", *dimstyles, version=version))
    if tables:
        parts.append(section("TABLES", *tables))
    if blocks:
        parts.append(section("BLOCKS", *blocks))
    parts.append(section("ENTITIES", *entities))
    text = "".join(parts)
    return text + (tags(0, "EOF") if eof else "")


def write(path, text: str, encoding: str = "utf-8") -> str:
    """Write with the encoding named (a code page for the old files).  Newlines are written as given, one per line."""
    with open(path, "w", encoding=encoding, newline="\n") as handle:
        handle.write(text)
    return str(path)
