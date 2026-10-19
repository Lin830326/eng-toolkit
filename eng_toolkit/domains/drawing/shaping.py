"""What of a reader's result goes into the model's context.  Pure, no engine.

``shape`` cuts the full result of ``dxf_read.read`` or ``pdf_read.read`` to a one-paragraph ``summary`` plus the facts a
question is likely to ask, every list limited to ``max_items`` and carrying its ``total``.  The full result goes to the
JSON artifact untouched.  ``detail="full"`` adds fields to the items (not items to the lists).
"""

from __future__ import annotations

import re
from typing import Any

from eng_toolkit.domains.drawing.reading import cut_text

DETAILS = ("summary", "full")
MAX_ITEMS = 500
DEFAULT_ITEMS = 25
TITLE_ORDER = ("title", "part_number", "revision", "material", "scale", "sheet", "projection", "general_tolerance", "date", "drawn_by", "checked_by", "approved_by", "company", "units")


# A text that reads like a hole callout: a diameter sign followed by a number, THRU, DEEP, or a thread such as M6x1.  Found by wording only.
HOLE_CALLOUT = re.compile(r"[\u00d8\u2300\u2205]\s*\d|\bTHRU\b|\bDEEP\b|(?:^|\s)M\d+(?:\.\d+)?\s*[x\u00d7X]", re.IGNORECASE)


def is_hole_callout(text: str) -> bool:
    return bool(HOLE_CALLOUT.search(text))


STRING_LIMIT = 500  # longest string in the answer; the full text is in the JSON file
STRING_LIMIT_FULL = 2000
SUMMARY_LIMIT = 3000


def shape(full: dict[str, Any], *, detail: str, max_items: int) -> dict[str, Any]:
    rich = detail == "full"
    data = _shape_dxf(full, rich, max_items) if full["kind"] == "dxf" else _shape_pdf(full, rich, max_items)
    summary = data.pop("summary")
    data = _bound(data, STRING_LIMIT_FULL if rich else STRING_LIMIT)
    return {"summary": _cut(summary, SUMMARY_LIMIT), **data}


def _cut(text: str, limit: int) -> str:
    return cut_text(text, limit)


def _bound(value: Any, limit: int) -> Any:
    if isinstance(value, str):
        return _cut(value, limit)
    if isinstance(value, dict):
        return {_cut(k, limit) if isinstance(k, str) else k: _bound(v, limit) for k, v in value.items()}
    if isinstance(value, list):
        return [_bound(v, limit) for v in value]
    return value


def _drop_none(row: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in row.items() if v is not None}


def _listed(items: list[Any], total: int, n: int) -> dict[str, Any]:
    shown = items[:n]
    return {"total": total, "shown": len(shown), "items": shown}


def _num(value: float | None) -> str:
    return "?" if value is None else f"{value:.6g}"


def _n(count: int, singular: str, plural: str | None = None) -> str:
    return f"{count} {singular if count == 1 else plural or singular + 's'}"


def _plural_unit(name: str) -> str:
    return {"foot": "feet", "inch": "inches"}.get(name, name + "s")


# --------------------------------------------------------------------------- DXF


def _dim_text(d: dict[str, Any], mm_per_unit: float | None, unit_name: str) -> str:
    shown = _cut(d["text"], 40) if d["text"] not in (None, "") else _num(d["value"])
    if d["measured_unit"] == "angle":
        return f"{d['kind']} {shown}"
    suffix = " mm" if mm_per_unit == 1.0 else f" {unit_name}" if mm_per_unit else " (unit unknown)"
    drawn = f", drawn {_num(d['measured'])} x DIMLFAC {_num(d['dimlfac'])}" if d["dimlfac"] != 1.0 and d["measured"] is not None else ""
    in_mm = f" = {_num(d['value_mm'])} mm" if d["value_mm"] is not None and mm_per_unit not in (None, 1.0) else ""
    return f"{d['kind']} {shown}{suffix}{in_mm}{drawn}"


HOLE_COUNT = re.compile(r"^\s*(\d+)\s*[x×X]\s*[Ø⌀∅]")
DIAMETER_SIGN = re.compile(r"[Ø⌀∅]\s*\d")


def _callout_phrase(text: str) -> str:
    """The callout and, when it has a diameter, how many holes it names: ``4x dia 6.6 THRU`` is 4, ``dia 10 THRU`` without a count is 1."""
    shown = _cut(text, 60)
    if not DIAMETER_SIGN.search(text):
        return shown
    count = int(m.group(1)) if (m := HOLE_COUNT.match(text)) else 1
    return f"{shown} ({count} hole{'s' if count != 1 else ''})"


def _ordinate_sentence(ordinates: list[dict[str, Any]]) -> str:
    """``x 10, 90; y 10, 25, 40``: the shown values of the ordinate dimensions per axis, in the order of the file, repeats dropped."""
    out = []
    for axis in ("x", "y", None):
        values: list[str] = []
        for d in ordinates:
            if d.get("ordinate_axis") == axis:
                shown = _num(d["value"]) if d["value"] is not None else _cut(d["text"] or "?", 20)  # the value, not the style's decimals ("10.0000")
                if shown not in values:
                    values.append(shown)
        if values:
            out.append(f"{axis or 'axis unknown'} {', '.join(values)}")
    return "; ".join(out)


def _title_sentence(tb: dict[str, Any]) -> str:
    if not tb["fields"]:
        return "Title block: none found."
    ordered = sorted(tb["fields"], key=lambda k: TITLE_ORDER.index(k) if k in TITLE_ORDER else 99)
    values = ", ".join(f"{k}={_cut(tb['fields'][k]['value'], 80)!r}" for k in ordered)
    inferred = [f["confidence"] for f in tb["fields"].values() if f["inferred"]]
    low, high = (min(inferred), max(inferred)) if inferred else (1.0, 1.0)
    how = "exact attribute tags" if not inferred else f"inferred from text positions, confidence {low:.2f}" + (f" to {high:.2f}" if high != low else "")
    return f"Title block ({tb['method']}; {how}): {values}."


def dxf_summary(full: dict[str, Any]) -> str:
    units, layout = full["units"], full["entities"]
    parts = [f"DXF {full['dxf']['release']} ({full['dxf']['version_code']}), text encoding {full['dxf']['encoding']}."]
    if units["mm_per_unit"]:
        parts.append(f"Units: {units['name']} (from {units['source']}); 1 drawing unit = {units['mm_per_unit']:g} mm.")
    else:
        parts.append(f"Units: UNKNOWN ({units['name']}: the file does not say), so lengths are in unknown drawing units and there are no millimetre values.")
    used = sum(1 for layer in full["layers"] if layer["entities"])
    parts.append(f"Model space has {_n(layout['model'], 'entity', 'entities')}" + (f" and paper space {layout['paper']}" if layout["paper"] else "") + f" on {_n(used, 'layer')}.")
    model = full["extents"].get("model")
    if model:
        unit_text = _plural_unit(units["name"]) if units["mm_per_unit"] else "drawing units"
        if model["geometry"]:
            w, h = model["geometry"]["size"]
            text = f"Model geometry extents {_num(w)} x {_num(h)} {unit_text}"
            if model["geometry"]["size_mm"] and units["mm_per_unit"] != 1.0:
                text += f" = {_num(model['geometry']['size_mm'][0])} x {_num(model['geometry']['size_mm'][1])} mm"
            if model["with_annotations"]["size"] != model["geometry"]["size"]:
                aw, ah = model["with_annotations"]["size"]
                text += f" ({_num(aw)} x {_num(ah)} with dimensions and text)"
        else:
            aw, ah = model["with_annotations"]["size"]
            text = f"Model space holds no geometry, only dimensions and text spanning {_num(aw)} x {_num(ah)} {unit_text}"
        parts.append(text + ".")
    if full["recognised_as"]:
        parts.append(f"Made by {full['recognised_as']}.")
    parts.append(_title_sentence(full["title_block"]))
    dims = full["dimensions"]
    ordinates: list[dict[str, Any]] = []
    if dims:
        ordinates = [d for d in dims if d["kind"] == "ordinate"]
        others = [d for d in dims if d["kind"] != "ordinate"]
        listed = "; ".join(_dim_text(d, units["mm_per_unit"], units["name"]) for d in others[:12]) or "ordinates only"
        other_total = full["dimensions_total"] - len(ordinates)
        more = f"; and {other_total - 12} more" if other_total > 12 else ""
        parts.append(f"Dimensions ({full['dimensions_total']}): {listed}{more}.")
        if ordinates:
            parts.append(f"Ordinates ({len(ordinates)}): {_ordinate_sentence(ordinates)} (coordinates from the datum corner of the view; 0 is the datum).")
    else:
        parts.append("Dimensions: none.")
    callouts = [t for t in full["texts"] if is_hole_callout(t["text"])]
    if callouts:
        shown = "; ".join(_callout_phrase(t["text"]) for t in callouts[:8]) + (f"; and {len(callouts) - 8} more" if len(callouts) > 8 else "")
        parts.append(f"Hole callouts ({len(callouts)}, found by their wording in the texts): {shown}.")
        if ordinates:
            parts.append("The ordinates probably position these holes: their non-zero values are the hole coordinates.")
    views = full["views"]
    if views["count"] is not None:
        parts.append(f"Views: {_n(views['count'], 'group')} of geometry (a guess from positions, confidence {views['confidence']}).")
    parts.append(f"Texts: {full['texts_total']}. {_n(full['blocks']['definitions'], 'block definition')}, {_n(full['blocks']['inserts'], 'block reference')}.")
    if full["warnings"]:
        parts.append(f"{len(full['warnings'])} warnings: read them.")
    return " ".join(parts)


def _hole_callouts(texts: list[dict[str, Any]], n: int) -> dict[str, Any]:
    found = [{"text": t["text"], "at": t["at"]} for t in texts if is_hole_callout(t["text"])]
    return {"inferred": True, "total": len(found), "items": found[:n]}


def _shape_dxf(full: dict[str, Any], rich: bool, n: int) -> dict[str, Any]:
    units = full["units"]
    layers = sorted(full["layers"], key=lambda layer: (-layer["entities"], layer["name"]))
    layer_items = []
    for layer in layers[:n]:
        row: dict[str, Any] = {"name": layer["name"], "entities": layer["entities"], "length": layer["length"], "length_mm": layer["length_mm"]}
        flags = [k for k, v in (("off", layer["on"] is False), ("frozen", bool(layer["frozen"])), ("locked", bool(layer["locked"])), ("undeclared", layer["declared"] is False)) if v]
        if flags:
            row["flags"] = flags
        layer_items.append({**layer, **row} if rich else row)
    dims = []
    for d in full["dimensions"][:n]:
        if rich:
            dims.append(_drop_none(d))
            continue
        keys = ("kind", "ordinate_axis", "layer", "measured", "dimlfac", "dimlfac_source", "value", "value_mm", "text", "text_override")
        row = _drop_none({k: d[k] for k in keys})
        if d["space"] == "paper":
            row["space"] = "paper"
        dims.append(row)
    texts = [_drop_none(t) if rich else {"text": t["text"], "layer": t["layer"], "at": t["at"]} for t in full["texts"][:n]]
    tb = full["title_block"]
    title: dict[str, Any] = {
        "method": tb["method"],
        "block": tb["block"],
        "fields": {k: {"value": f["value"], "inferred": f["inferred"], "confidence": f["confidence"], **({"source": f["source"]} if rich else {})} for k, f in tb["fields"].items()},
        "status": tb["status"],
        "conflicts": tb["conflicts"],
    }
    if rich:
        title["unmatched_attributes"] = tb["unmatched_attributes"]
        title["empty_labels"] = tb["empty_labels"]
    extents: dict[str, Any] = {}
    for space, e in full["extents"].items():
        extents[space] = None if e is None else {key: e[key] for key in ("geometry", "with_annotations")} | ({} if e["complete"] else {"complete": False})
    blocks = full["blocks"]
    by_type = dict(sorted(full["entities"]["by_type"].items(), key=lambda kv: -kv[1])[:n])
    data: dict[str, Any] = {
        "summary": dxf_summary(full),
        "format": "dxf",
        "file": full["file"],
        "dxf": {k: full["dxf"][k] for k in ("version_code", "release", "encoding", "recovered")},
        "units": units,
        "extents": extents,
        "entities": {"total": full["entities"]["total"], "model": full["entities"]["model"], "paper": full["entities"]["paper"], "by_type": by_type},
        "layers": _listed(layer_items, len(layers), n),
        "title_block": title,
        "dimensions": _listed(dims, full["dimensions_total"], n),
        "texts": _listed(texts, full["texts_total"], n),
        "hole_callouts": _hole_callouts(full["texts"], n),
        "views": {"count": full["views"]["count"], "inferred": True, "confidence": full["views"]["confidence"], "items": full["views"]["items"][:n]},
        "blocks": {
            "definitions": blocks["definitions"],
            "inserts": blocks["inserts"],
            "max_nesting": blocks["max_nesting"],
            "xrefs": blocks["xrefs"][:n],
            "cycles": blocks["cycles"][:n],
            "with_attributes": [{**w, "attribs": dict(list(w["attribs"].items())[:n])} for w in blocks["with_attributes"][:n]],
        },
        "recognised_as": full["recognised_as"],
    }
    if full["images"]:
        data["images"] = full["images"][:n]
    if rich:
        data["layouts"] = full["layouts"]
        data["extents_note"] = next((e["method"] for e in full["extents"].values() if e), None)
        data["views"]["method"] = full["views"]["method"]
        data["dxf"]["encoding_source"] = full["dxf"]["encoding_source"]
        data["length_counts"] = full["entities"]["length_counts"]
    return data


# --------------------------------------------------------------------------- PDF


def pdf_summary(full: dict[str, Any]) -> str:
    info, text, vector = full["pdf"], full["text"], full["vector"]
    reading = f", the first {info['pages_read']} read" if info["pages_read"] < info["pages_total"] else ""
    parts = [f"PDF {info['version'] or '?'}, {_n(info['pages_total'], 'page')}{reading}."]
    sizes = ", ".join(f"Page {p['number']} is {p['width_mm']:.1f} x {p['height_mm']:.1f} mm" for p in full["pages"][:5])
    parts.append(sizes + "." if sizes else "No page could be read.")
    if text["has_text_layer"]:
        first = [line["text"] for p in full["pages"] for line in p["text"]["lines"]][:8]
        parts.append(f"Text: {text['chars']} characters; first lines: " + " | ".join(repr(_cut(t, 80)) for t in first) + ".")
    else:
        parts.append("Readable text: NO. This PDF has no text layer, so no text can be read from it (words may be drawn as outlines); nothing is invented.")
    if vector["paths"]:
        box = vector["bbox_mm"]
        extent = f", extent {box[2] - box[0]:.1f} x {box[3] - box[1]:.1f} mm" if box else ""
        dashed = sum(s["length_mm"] for s in vector["by_style"] if s["dashed"])
        total = vector["straight_length_mm"] + vector["curve_length_mm"]
        parts.append(f"Vector: {vector['paths']} paths ({vector['stroked']} stroked, {vector['filled']} filled), stroked length {total:.1f} mm of which dashed {dashed:.1f} mm{extent}.")
    else:
        parts.append("Vector: no paths.")
    active = info["active_content"]
    parts.append("Active content: " + (", ".join(f"{k} ({v})" for k, v in active.items()) + " (reported, not executed)." if active else "none found."))
    return " ".join(parts)


def _shape_pdf(full: dict[str, Any], rich: bool, n: int) -> dict[str, Any]:
    budget = 2 * n
    pages = []
    for p in full["pages"][:n]:
        lines = p["text"]["lines"][: max(budget, 0)]
        budget -= len(lines)
        vector = {k: v for k, v in p["vector"].items() if k != "method" and (rich or k != "by_style")}
        pages.append(
            {
                "number": p["number"],
                "width_mm": p["width_mm"],
                "height_mm": p["height_mm"],
                "rotation": p["rotation"],
                "images": p["images"],
                "text": {"line_total": p["text"]["line_total"], "chars": p["text"]["chars"], "lines": lines},
                "vector": vector,
            }
        )
    info = full["pdf"]
    pdf = {k: info[k] for k in ("version", "pages_total", "pages_read", "encrypted", "active_content")}
    if info["metadata"]:
        pdf["metadata"] = info["metadata"]
    return {
        "summary": pdf_summary(full),
        "format": "pdf",
        "file": full["file"],
        "pdf": pdf,
        "origin": full["origin"],
        "pages": _listed(pages, info["pages_read"], n),
        "text": full["text"],
        "vector": full["vector"],
    }
