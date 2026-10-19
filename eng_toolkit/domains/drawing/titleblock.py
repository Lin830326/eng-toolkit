"""Title-block recognition from what a DXF holds: block attributes and label/value texts.  Pure, no engine.

Two sources, told apart in every field:

* **attributes** (an INSERT with ATTRIB entities, how most CAD templates store a title block): the tag is looked up in a
  fixed vocabulary.  A hit is exact: ``inferred`` is False, ``confidence`` 1.0.
* **label pairs** (plain texts, how drawing_make_views writes it): a text that is a known label owns the nearest text
  that is not itself a label, below it or to its right.  That is a guess from positions: ``inferred`` is True and
  ``confidence`` is below 1.

Texts and tags outside the vocabulary are never assigned to a field.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

# field -> the labels and tags that mean it (compared after upper-casing and dropping everything but letters, digits, CJK)
VOCABULARY: dict[str, tuple[str, ...]] = {
    "title": ("TITLE", "DRAWINGTITLE", "DWGTITLE", "PARTNAME", "DESCRIPTION", "DESC", "圖名", "品名", "名稱", "零件名稱", "圖紙名稱"),
    "part_number": ("PARTNO", "PARTNUMBER", "PARTNUM", "PN", "DWGNO", "DWGNUMBER", "DRAWINGNO", "DRAWINGNUMBER", "ITEMNO", "圖號", "料號", "件號", "品號", "零件編號", "零件號", "圖紙編號"),
    "revision": ("REV", "REVISION", "版次", "版本", "修訂", "改版"),
    "material": ("MATERIAL", "MATL", "MAT", "材質", "材料"),
    "scale": ("SCALE", "比例"),
    "sheet": ("SHEET", "SHEETNO", "SHEETSIZE", "SIZE", "PAGE", "張數", "圖幅"),
    "date": ("DATE", "DRAWNDATE", "日期"),
    "drawn_by": ("DRAWN", "DRAWNBY", "DRAWER", "DESIGNER", "DESIGNEDBY", "繪圖", "製圖", "設計", "繪製"),
    "checked_by": ("CHECKED", "CHECKEDBY", "CHK", "CHECKER", "校對", "審核", "檢查"),
    "approved_by": ("APPROVED", "APPROVEDBY", "APPR", "核准", "批准", "核可"),
    "projection": ("PROJECTION", "PROJ", "PROJECTIONMETHOD", "投影", "投影法"),
    "general_tolerance": ("GENERALTOLERANCE", "TOLERANCE", "TOL", "GENTOL", "一般公差", "未註公差", "公差"),
    "company": ("COMPANY", "公司"),
    "units": ("UNITS", "UNIT", "單位"),
}
_LOOKUP: dict[str, str] = {key: name for name, keys in VOCABULARY.items() for key in keys}
_KEEP = re.compile(r"[^A-Z0-9㐀-鿿]+")
_TITLE_BLOCK_NAME = re.compile(r"TITLE|TTL|\bTB\b|^TB|BLK|FRAME|標題|圖框|圖簽", re.I)
_COLON = re.compile(r"[:：]")
STATUS_PREFIX = "UNREVIEWED"

BASE_CONFIDENCE = 0.85
COLON_CONFIDENCE = 0.9
OTHER_LAYER_PENALTY = 0.25
FAR_PENALTY = 0.15
RIGHT_PENALTY = 0.1
MAX_GAP_HEIGHTS = 10.0  # a value below its label is at most this many label heights away
FAR_GAP_HEIGHTS = 6.0
MAX_RIGHT_HEIGHTS = 40.0
MIN_TITLE_TAGS = 2
MAX_LABELS = 200  # a title block has a few dozen labels; thousands of texts that look like labels are not one
MAX_CANDIDATES = 5000


@dataclass(frozen=True)
class TextRec:
    text: str
    x: float
    y: float
    height: float
    layer: str


@dataclass(frozen=True)
class AttribBlock:
    name: str
    layer: str
    attribs: list[tuple[str, str]]  # (tag, value) in file order


@dataclass(frozen=True)
class Field:
    value: str
    source: str
    inferred: bool
    confidence: float


def canonical_field(label: str) -> str | None:
    """The field a label or tag stands for, or None when it is not in the vocabulary."""
    key = _KEEP.sub("", unicodedata.normalize("NFKC", label).upper())
    return _LOOKUP.get(key)


# --------------------------------------------------------------------------- attributes


@dataclass
class AttribResult:
    block: str | None = None
    fields: dict[str, Field] = field(default_factory=dict)
    unmatched: list[tuple[str, str]] = field(default_factory=list)


def from_attributes(blocks: list[AttribBlock]) -> AttribResult:
    """The fields of the INSERT that looks most like a title block (most recognised tags; at least two, or one in a block named like one)."""
    best: tuple[int, int, AttribBlock] | None = None
    for order, block in enumerate(blocks):
        recognised = sum(1 for tag, value in block.attribs if value.strip() and canonical_field(tag))
        named = bool(_TITLE_BLOCK_NAME.search(block.name))
        if recognised >= MIN_TITLE_TAGS or (recognised >= 1 and named):
            if best is None or (recognised, -order) > (best[0], -best[1]):
                best = (recognised, order, block)
    if best is None:
        return AttribResult()
    block = best[2]
    result = AttribResult(block=block.name)
    for tag, value in block.attribs:
        value = value.strip()
        if not value:
            continue
        name = canonical_field(tag)
        if name is None:
            result.unmatched.append((tag, value))
        elif name not in result.fields:
            result.fields[name] = Field(value, f"attribute {tag} of block {block.name}", False, 1.0)
    return result


# --------------------------------------------------------------------------- label pairs


@dataclass
class PairResult:
    fields: dict[str, Field] = field(default_factory=dict)
    status: str | None = None
    empty_labels: list[str] = field(default_factory=list)
    limited: bool = False  # the search was cut to MAX_LABELS labels and MAX_CANDIDATES other texts


def _width(rec: TextRec) -> float:
    return rec.height * sum(1.0 if unicodedata.east_asian_width(c) in ("W", "F") else 0.62 for c in rec.text)


def _below(label: TextRec, cand: TextRec) -> tuple[float, float] | None:
    """(distance, confidence penalty) when ``cand`` sits below ``label`` and overlaps it horizontally."""
    gap = label.y - cand.y
    if gap < 0.3 * label.height or gap > MAX_GAP_HEIGHTS * label.height:
        return None
    dx = cand.x - label.x
    if dx < -2.0 * label.height or dx > max(_width(label), 4.0 * label.height):
        return None
    return gap + 0.5 * abs(dx), FAR_PENALTY if gap > FAR_GAP_HEIGHTS * label.height else 0.0


def _right(label: TextRec, cand: TextRec) -> tuple[float, float] | None:
    """(distance, confidence penalty) when ``cand`` is on the same line as ``label``, to its right."""
    tall = max(label.height, cand.height)
    if abs(cand.y - label.y) > 0.75 * tall:
        return None
    gap = cand.x - (label.x + _width(label))
    if gap < -0.5 * label.height or gap > MAX_RIGHT_HEIGHTS * label.height:
        return None
    return max(gap, 0.0) + 3.0 * abs(cand.y - label.y), RIGHT_PENALTY


def from_label_pairs(texts: list[TextRec]) -> PairResult:
    result = PairResult()
    labels: list[tuple[TextRec, str]] = []
    candidates: list[TextRec] = []
    for rec in texts:
        text = rec.text.strip()
        if not text:
            continue
        if text.upper().startswith(STATUS_PREFIX):
            result.status = result.status or text
            continue
        name = canonical_field(text)
        if name:
            labels.append((rec, name))
            continue
        parts = _COLON.split(text, maxsplit=1)
        if len(parts) == 2 and parts[1].strip() and canonical_field(parts[0]):
            name = canonical_field(parts[0])
            assert name is not None
            result.fields.setdefault(name, Field(parts[1].strip(), f'label "{parts[0].strip()}" with its value in one text (layer {rec.layer})', True, COLON_CONFIDENCE))
            continue
        candidates.append(rec)

    if len(labels) > MAX_LABELS or len(candidates) > MAX_CANDIDATES:
        result.limited = True
        labels, candidates = labels[:MAX_LABELS], candidates[:MAX_CANDIDATES]
    pairs: list[tuple[float, int, int, float, str]] = []
    for li, (label, _) in enumerate(labels):
        for ci, cand in enumerate(candidates):
            for direction, test in (("below", _below), ("right", _right)):
                scored = test(label, cand)
                if scored:
                    pairs.append((scored[0], li, ci, scored[1], direction))
    pairs.sort()
    used_labels: set[int] = set()
    used_values: set[int] = set()
    for _, li, ci, penalty, direction in pairs:
        if li in used_labels or ci in used_values:
            continue
        label, name = labels[li]
        cand = candidates[ci]
        used_labels.add(li)
        used_values.add(ci)
        if name in result.fields:
            continue
        confidence = BASE_CONFIDENCE - penalty - (OTHER_LAYER_PENALTY if cand.layer != label.layer else 0.0)
        where = "below it" if direction == "below" else "to its right"
        layer = cand.layer if cand.layer == label.layer else f"{cand.layer}, label on {label.layer}"
        result.fields[name] = Field(cand.text.strip(), f'label "{label.text.strip()}" with the nearest text {where} (layer {layer})', True, round(max(confidence, 0.1), 2))
    result.empty_labels = [label.text.strip() for li, (label, _) in enumerate(labels) if li not in used_labels]
    return result


# --------------------------------------------------------------------------- both


@dataclass
class TitleBlock:
    method: str  # "attributes", "label_pairs", "attributes+label_pairs" or "none"
    fields: dict[str, Field]
    block: str | None = None
    status: str | None = None
    conflicts: list[dict[str, str]] = field(default_factory=list)
    unmatched_attributes: list[tuple[str, str]] = field(default_factory=list)
    empty_labels: list[str] = field(default_factory=list)
    limited: bool = False


def build(blocks: list[AttribBlock], texts: list[TextRec]) -> TitleBlock:
    attributes = from_attributes(blocks)
    pairs = from_label_pairs(texts)
    fields = dict(attributes.fields)
    conflicts: list[dict[str, str]] = []
    for name, found in pairs.fields.items():
        if name not in fields:
            fields[name] = found
        elif fields[name].value.strip().casefold() != found.value.strip().casefold():
            conflicts.append({"field": name, "attribute": fields[name].value, "text": found.value})
    methods = [m for m, used in (("attributes", bool(attributes.fields)), ("label_pairs", bool(pairs.fields))) if used]
    return TitleBlock(
        method="+".join(methods) or "none",
        fields=fields,
        block=attributes.block,
        status=pairs.status,
        conflicts=conflicts,
        unmatched_attributes=attributes.unmatched,
        empty_labels=pairs.empty_labels,
        limited=pairs.limited,
    )
