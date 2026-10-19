"""Sheet, scale, view placement, overall dimensions and title block: the layout of a drawing, as pure data.

No OpenCASCADE and no DXF library here.  The input is the hidden-line projections of ``eng_toolkit.brep.project``,
the output a :class:`Drawing` that ``dxf.py`` draws and ``tools.py`` reports.  Lengths are millimetres on the sheet.

Layout, with the numbers (``conventions.py`` holds them):

* Sheets are ISO 5457 landscape: A4 297 x 210, A3 420 x 297, A2 594 x 420, A1 841 x 594, A0 1189 x 841.  The frame
  is 20 mm from the left edge and 10 mm from the others.  The title block is 180 x 40 mm in the bottom right corner
  of the frame, with the notes (4.5 mm per line) just above it.
* Views sit in a grid around the front view.  Third angle: top above, bottom below, right to the right of it, left to
  its left, back to the right of the right view.  First angle mirrors both axes: top below, bottom above, right to the
  left, left to the right, back to the left of the right view.  The isometric view goes into the top row, above the
  last column.  Views of one column share their left edge and views of one row their bottom edge, which is what
  keeps projected views in line.
* The distance between two neighbouring views is 15 mm plus 8 mm for every dimension row on the side where the
  dimensions of the second one go: horizontal dimensions below a view, vertical ones to its left.
* Overall dimensions follow ISO 129 "dimension once": each model axis is dimensioned in the first requested view in
  which it lies in the view plane.  With front, top and right that is X and Z on the front view and Y on the top view.
  The value is the size of the bounding box of the projected visible geometry.
* The views must fit with 5 mm clear space either above the title block and notes (the full width of the frame) or
  to the left of them (the full height).
* ``scale="auto"`` and ``sheet="auto"`` take the largest standard scale first, then the smallest sheet.  The scales are
  tried in the order 1:1, 1:2, 1:5, 1:10, 1:20, 1:50, 1:100 and every sheet from A4 up to A0 at each.  A part whose
  views (with gaps and dimension rows) cover less than a quarter of the usable area of the sheet at 1:1 is enlarged
  to the largest of 10:1, 5:1 and 2:1 that still fits; the usable area is the area inside the frame, and the sheet is A4
  when the sheet is automatic.  A fixed sheet or a fixed scale narrows the search to the other one.
  If nothing fits, ``drawing.layout_failed`` names the scale or the sheet that would.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from eng_toolkit.brep import VIEW_FRAMES, Projection
from eng_toolkit.domains.drawing import conventions as C
from eng_toolkit.errors import ToolFailure

Vec2 = tuple[float, float]
Scale = tuple[int, int]  # numerator : denominator, 2:1 is (2, 1) and 1:2 is (1, 2)
Method = Literal["third_angle", "first_angle"]

VIEW_NAMES: tuple[str, ...] = tuple(VIEW_FRAMES)
SHEETS: dict[str, Vec2] = {"A4": (297.0, 210.0), "A3": (420.0, 297.0), "A2": (594.0, 420.0), "A1": (841.0, 594.0), "A0": (1189.0, 841.0)}
SCALES: tuple[Scale, ...] = ((1, 1), (1, 2), (1, 5), (1, 10), (1, 20), (1, 50), (1, 100), (2, 1), (5, 1), (10, 1))  # ISO 5455
REDUCTIONS: tuple[Scale, ...] = SCALES[:7]
ENLARGEMENTS: tuple[Scale, ...] = ((10, 1), (5, 1), (2, 1))
METHODS: tuple[str, ...] = ("third_angle", "first_angle")
DIMENSION_MODES: tuple[str, ...] = ("auto", "overall", "none")
ENLARGE_BELOW = 0.25
AXIS_TOLERANCE = 1e-6

LIMITS = {"title": 80, "part_number": 40, "material": 40, "revision": 8, "general_tolerance": 80, "notes": 6, "note": 100}


def _invalid(param: str, message: str, **details) -> ToolFailure:
    return ToolFailure("input.invalid", f"{param}: {message}", {"param": param, **details})


# --------------------------------------------------------------------------- parsing of the caller's strings


def parse_views(text: str) -> tuple[str, ...]:
    names = tuple(item.strip().lower() for item in text.split(",") if item.strip())
    if not names:
        raise _invalid("views", f"name at least one view of {list(VIEW_NAMES)}", allowed=list(VIEW_NAMES))
    unknown = [n for n in names if n not in VIEW_NAMES]
    if unknown:
        raise _invalid("views", f"{unknown} is not a view; use a comma list of {list(VIEW_NAMES)}", allowed=list(VIEW_NAMES))
    repeated = sorted({n for n in names if names.count(n) > 1})
    if repeated:
        raise _invalid("views", f"{repeated} appears more than once", repeated=repeated)
    return names


def parse_method(text: str) -> Method:
    value = text.strip().lower()
    if value not in METHODS:
        raise _invalid("projection", f"{text!r} is not a projection method; use one of {list(METHODS)}", allowed=list(METHODS))
    return value  # type: ignore[return-value]


def parse_sheet(text: str) -> str:
    """``auto`` or a sheet name in upper case."""
    value = text.strip().upper() if text.strip().lower() != "auto" else "auto"
    if value != "auto" and value not in SHEETS:
        raise _invalid("sheet", f"{text!r} is not a sheet; use 'auto' or one of {list(SHEETS)}", allowed=["auto", *SHEETS])
    return value


def scale_text(scale: Scale) -> str:
    return f"{scale[0]}:{scale[1]}"


def parse_scale(text: str) -> Scale | Literal["auto"]:
    value = text.strip().lower()
    if value == "auto":
        return "auto"
    for scale in SCALES:
        if scale_text(scale) == value.replace(" ", ""):
            return scale
    raise _invalid("scale", f"{text!r} is not an ISO 5455 scale; use 'auto' or one of {[scale_text(s) for s in SCALES]}", allowed=["auto", *map(scale_text, SCALES)])


def parse_dimensions(text: str) -> str:
    value = text.strip().lower()
    if value not in DIMENSION_MODES:
        raise _invalid("dimensions", f"{text!r} is not a dimension mode; use one of {list(DIMENSION_MODES)}", allowed=list(DIMENSION_MODES))
    return "overall" if value == "auto" else value


def fold_spaces(value: str) -> str:
    """Every space separator (Unicode category Zs: U+3000 ideographic, U+00A0 no-break, the en and em spaces, ...) as U+0020.

    CJK text uses U+3000 as a separator and pasted text carries U+00A0.  A viewer draws them only if its font has the glyph
    (many CAD fonts do not), and the PDF cannot know the advance of a glyph it lacks; U+0020 looks the same everywhere.
    """
    return "".join(" " if unicodedata.category(c) == "Zs" else c for c in value)


def clean_text(param: str, value: str, limit: int) -> str:
    """A free-text field: at most ``limit`` characters, printable (control characters, line breaks and tabs are not).

    Space separators other than U+0020 are accepted and become U+0020 (:func:`fold_spaces`).
    """
    if len(value) > limit:
        raise _invalid(param, f"at most {limit} characters, got {len(value)}", limit=limit, length=len(value))
    value = fold_spaces(value)
    if not value.isprintable():
        bad = sorted({f"U+{ord(c):04X}" for c in value if not c.isprintable()})
        raise _invalid(param, f"contains characters that cannot be printed ({', '.join(bad)}); no control characters, tabs or line breaks", characters=bad)
    return value.strip()


def parse_notes(text: str) -> tuple[str, ...]:
    """Notes separated by ';': at most 6 of at most 100 printable characters."""
    notes = tuple(clean_text("notes", item, LIMITS["note"]) for item in text.split(";") if item.strip())
    if len(notes) > LIMITS["notes"]:
        raise _invalid("notes", f"at most {LIMITS['notes']} notes separated by ';', got {len(notes)}", limit=LIMITS["notes"], count=len(notes))
    return notes


# --------------------------------------------------------------------------- the result

@dataclass(frozen=True)
class TitleBlock:
    title: str = ""
    part_number: str = ""
    material: str = ""
    revision: str = "A"
    general_tolerance: str = ""
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class SheetLine:
    a: Vec2
    b: Vec2
    layer: str


@dataclass(frozen=True)
class SheetText:
    text: str
    at: Vec2  # left end of the baseline
    height: float
    layer: str
    width_factor: float = 1.0


@dataclass(frozen=True)
class PlacedView:
    name: str
    projection: Projection
    origin: Vec2  # sheet position of the model origin as seen in this view: sheet = origin + scale * (u, v)
    scale: float
    extent: Vec2  # width and height of the visible geometry as drawn

    def to_sheet(self, point: Vec2) -> Vec2:
        return (round(self.origin[0] + self.scale * point[0], 6), round(self.origin[1] + self.scale * point[1], 6))

    @property
    def box(self) -> tuple[Vec2, Vec2]:
        lo = self.to_sheet(self.projection.bbox_min)
        return lo, (round(lo[0] + self.extent[0], 6), round(lo[1] + self.extent[1], 6))


@dataclass(frozen=True)
class PlacedDim:
    kind: Literal["linear"]
    view: str
    orientation: Literal["horizontal", "vertical"]
    p1: Vec2  # sheet mm, measured points
    p2: Vec2
    base: Vec2  # sheet mm, a point on the dimension line
    value_mm: float  # true size
    text: str
    source: str  # "bbox:x"


@dataclass(frozen=True)
class Drawing:
    sheet: str
    size_mm: Vec2
    scale: Scale
    method: Method
    views: tuple[PlacedView, ...]
    dims: tuple[PlacedDim, ...]
    lines: tuple[SheetLine, ...]  # frame and title block
    texts: tuple[SheetText, ...]  # title block and notes
    warnings: tuple[str, ...]

    @property
    def scale_value(self) -> float:
        return self.scale[0] / self.scale[1]

    @property
    def scale_label(self) -> str:
        return scale_text(self.scale)

    @property
    def dimlfac(self) -> float:
        """What the drawn length is multiplied by to give the true size."""
        return self.scale[1] / self.scale[0]


# --------------------------------------------------------------------------- dimensions


def format_mm(value: float) -> str:
    """A length the way the dimension style prints it: two decimals at most, no trailing zeros."""
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


@dataclass(frozen=True)
class _DimPlan:
    axis: str
    view: str
    orientation: Literal["horizontal", "vertical"]
    value: float


def plan_dimensions(projections: Sequence[Projection], mode: str) -> list[_DimPlan]:
    """One overall dimension per model axis, in the first view (in the given order) whose plane holds the axis."""
    if mode == "none":
        return []
    plans: list[_DimPlan] = []
    for index, axis in enumerate("xyz"):
        for p in projections:
            if abs(abs(p.x_axis[index]) - 1.0) < AXIS_TOLERANCE:
                orientation, value = "horizontal", p.bbox_max[0] - p.bbox_min[0]
            elif abs(abs(p.y_axis[index]) - 1.0) < AXIS_TOLERANCE:
                orientation, value = "vertical", p.bbox_max[1] - p.bbox_min[1]
            else:
                continue
            if value > AXIS_TOLERANCE:
                plans.append(_DimPlan(axis, p.view, orientation, round(value, 6)))
            break
    return plans


# --------------------------------------------------------------------------- placement

_GRID: dict[str, dict[str, tuple[int, int]]] = {
    "third_angle": {"front": (0, 0), "top": (0, 1), "bottom": (0, -1), "left": (-1, 0), "right": (1, 0), "back": (2, 0)},
    "first_angle": {"front": (0, 0), "top": (0, -1), "bottom": (0, 1), "left": (1, 0), "right": (-1, 0), "back": (-2, 0)},
}


def grid_cells(names: Sequence[str], method: str) -> dict[str, tuple[int, int]]:
    """Grid cell (column, row) of every view; rows grow upwards, the front view is (0, 0)."""
    cells = {n: _GRID[method][n] for n in names if n != "iso"}
    if "iso" in names:
        if cells:
            column, row = max(c for c, _ in cells.values()), max(1, max(r for _, r in cells.values()))
            while (column, row) in cells.values():
                column += 1
        else:
            column, row = 0, 0
        cells["iso"] = (column, row)
    return cells


@dataclass(frozen=True)
class _Block:
    """The views of a drawing at one scale: their bounding boxes' offsets from the lower left corner of the block."""

    width: float
    height: float
    left: dict[str, float]  # per view: offset of the left edge of the visible geometry
    bottom: dict[str, float]


def _block(names: Sequence[str], cells: dict[str, tuple[int, int]], projections: dict[str, Projection], rows: dict[str, tuple[int, int]], scale: Scale) -> _Block:
    s = scale[0] / scale[1]
    size = {n: ((projections[n].bbox_max[0] - projections[n].bbox_min[0]) * s, (projections[n].bbox_max[1] - projections[n].bbox_min[1]) * s) for n in names}
    columns = sorted({cells[n][0] for n in names})
    lines = sorted({cells[n][1] for n in names})
    margin_left = {c: max(C.DIM_ROW_MM * rows[n][1] for n in names if cells[n][0] == c) for c in columns}
    margin_bottom = {r: max(C.DIM_ROW_MM * rows[n][0] for n in names if cells[n][1] == r) for r in lines}
    width_of = {c: max(size[n][0] for n in names if cells[n][0] == c) for c in columns}
    height_of = {r: max(size[n][1] for n in names if cells[n][1] == r) for r in lines}
    x, column_left = 0.0, {}
    for c in columns:
        x += margin_left[c]
        column_left[c] = x
        x += width_of[c] + C.VIEW_GAP_MM
    y, row_bottom = 0.0, {}
    for r in lines:
        y += margin_bottom[r]
        row_bottom[r] = y
        y += height_of[r] + C.VIEW_GAP_MM
    return _Block(
        x - C.VIEW_GAP_MM,
        y - C.VIEW_GAP_MM,
        {n: column_left[cells[n][0]] for n in names},
        {n: row_bottom[cells[n][1]] for n in names},
    )


def _notes_height(count: int) -> float:
    return 0.0 if count == 0 else count * C.NOTE_PITCH_MM + C.NOTE_PAD_MM


def _frame(sheet: str) -> tuple[float, float, float, float]:
    width, height = SHEETS[sheet]
    return C.FRAME_MARGIN_LEFT_MM, C.FRAME_MARGIN_OTHER_MM, width - C.FRAME_MARGIN_OTHER_MM, height - C.FRAME_MARGIN_OTHER_MM


def usable_area(sheet: str) -> float:
    x0, y0, x1, y1 = _frame(sheet)
    return (x1 - x0) * (y1 - y0)


def _regions(sheet: str, notes: int) -> list[tuple[float, float, float, float]]:
    """Where the views may go: above the title block and notes, or to the left of them, less the clear space."""
    x0, y0, x1, y1 = _frame(sheet)
    band = C.TITLE_BLOCK_HEIGHT_MM + _notes_height(notes)
    m = C.PLACEMENT_MARGIN_MM
    return [(x0 + m, y0 + band + m, x1 - m, y1 - m), (x0 + m, y0 + m, x1 - C.TITLE_BLOCK_WIDTH_MM - m, y1 - m)]


def _place(block: _Block, sheet: str, notes: int) -> Vec2 | None:
    """Lower left corner of the block, centred in the first region it fits, or ``None``."""
    for x0, y0, x1, y1 in _regions(sheet, notes):
        if x1 - x0 >= block.width and y1 - y0 >= block.height:
            return round((x0 + x1 - block.width) / 2, 6), round((y0 + y1 - block.height) / 2, 6)
    return None


# --------------------------------------------------------------------------- text fitting


def text_em(text: str) -> float:
    """Estimated width of a text in text heights: full-width characters count 1.0, the rest 0.62."""
    return sum(C.WIDE_EM if unicodedata.east_asian_width(c) in ("W", "F") else C.LATIN_EM for c in text)


def fit_text(text: str, max_width: float, height: float) -> tuple[float, float, bool]:
    """Height and width factor that bring ``text`` into ``max_width``, and whether it fits at all.

    The height shrinks first, down to 2 mm; below that the characters are narrowed, down to 0.4 of their width.
    """
    em = text_em(text)
    if em * height <= max_width:
        return height, 1.0, True
    shrunk = max(C.MIN_TEXT_HEIGHT_MM, min(height, max_width / em))
    if em * shrunk <= max_width:
        return round(shrunk, 3), 1.0, True
    factor = max_width / (em * shrunk)
    return round(shrunk, 3), round(max(factor, C.MIN_WIDTH_FACTOR), 3), factor >= C.MIN_WIDTH_FACTOR


# --------------------------------------------------------------------------- sheet furniture


def _rect(x0: float, y0: float, x1: float, y1: float, layer: str) -> list[SheetLine]:
    return [SheetLine((x0, y0), (x1, y0), layer), SheetLine((x1, y0), (x1, y1), layer), SheetLine((x1, y1), (x0, y1), layer), SheetLine((x0, y1), (x0, y0), layer)]


def _furniture(sheet: str, scale: Scale, method: Method, title: TitleBlock) -> tuple[list[SheetLine], list[SheetText], list[str]]:
    x0, y0, x1, y1 = _frame(sheet)
    lines = _rect(x0, y0, x1, y1, C.FRAME.name)
    tb_x = x1 - C.TITLE_BLOCK_WIDTH_MM
    lines += _rect(tb_x, y0, x1, y0 + C.TITLE_BLOCK_HEIGHT_MM, C.TITLE.name)
    values = {
        "title": title.title,
        "part_number": title.part_number,
        "material": title.material,
        "revision": title.revision,
        "scale": scale_text(scale),
        "sheet": sheet,
        "projection": C.PROJECTION_LABELS[method],
        "general_tolerance": title.general_tolerance,
        "status": C.STATUS_LINE,
    }
    texts: list[SheetText] = []
    warnings: list[str] = []
    top = y0 + C.TITLE_BLOCK_HEIGHT_MM
    for band, band_height in zip(C.TITLE_BANDS, C.TITLE_BAND_HEIGHTS_MM):
        bottom = top - band_height
        if bottom > y0 + 1e-9:
            lines.append(SheetLine((tb_x, bottom), (x1, bottom), C.TITLE.name))
        for key, label, offset, width in band:
            if offset > 0:
                lines.append(SheetLine((tb_x + offset, bottom), (tb_x + offset, top), C.TITLE.name))
            if label:
                texts.append(SheetText(label, (tb_x + offset + C.CELL_PAD_MM, top - 0.8 - C.LABEL_HEIGHT_MM), C.LABEL_HEIGHT_MM, C.TITLE.name))
            value = values[key]
            if not value:
                continue
            base = C.TITLE_VALUE_HEIGHT_MM if key == "title" else C.STATUS_HEIGHT_MM if key == "status" else C.VALUE_HEIGHT_MM
            height, factor, fits = fit_text(value, width - 2 * C.CELL_PAD_MM, base)
            if factor < 1.0 or not fits:
                warnings.append(f"{key.replace('_', ' ')} text is {'too long for its cell and runs over' if not fits else 'compressed to'} {factor:g} of its width")
            y = bottom + (band_height - height) / 2 if key == "status" else bottom + C.CELL_PAD_MM
            texts.append(SheetText(value, (tb_x + offset + C.CELL_PAD_MM, round(y, 6)), height, C.TITLE.name, factor))
        top = bottom
    for index, note in enumerate(title.notes):
        text = f"{index + 1}. {note}"
        height, factor, fits = fit_text(text, C.TITLE_BLOCK_WIDTH_MM - 2 * C.CELL_PAD_MM, C.NOTE_TEXT_HEIGHT_MM)
        if factor < 1.0 or not fits:
            warnings.append(f"note {index + 1} is {'too long for the notes area and runs over' if not fits else 'compressed to'} {factor:g} of its width")
        y = y0 + C.TITLE_BLOCK_HEIGHT_MM + C.NOTE_PAD_MM + (len(title.notes) - 1 - index) * C.NOTE_PITCH_MM
        texts.append(SheetText(text, (tb_x + C.CELL_PAD_MM, round(y, 6)), height, C.TEXT.name, factor))
    return lines, texts, warnings


# --------------------------------------------------------------------------- choosing sheet and scale


def _layout_failed(message: str, **details) -> ToolFailure:
    return ToolFailure("drawing.layout_failed", message, details)


def compose(
    projections: Sequence[Projection],
    *,
    method: Method,
    sheet: str = "auto",
    scale: Scale | Literal["auto"] = "auto",
    dimensions: str = "overall",
    title: TitleBlock = TitleBlock(),
) -> Drawing:
    """Place the views on a sheet, plan their dimensions and write the title block.

    ``projections`` in the order the caller asked for them; ``sheet`` and ``scale`` as returned by
    :func:`parse_sheet` and :func:`parse_scale`.  ``drawing.layout_failed`` when no sheet and scale fit.
    """
    names = [p.view for p in projections]
    by_name = {p.view: p for p in projections}
    cells = grid_cells(names, method)
    plans = plan_dimensions(projections, dimensions)
    rows = {n: (sum(d.orientation == "horizontal" for d in plans if d.view == n), sum(d.orientation == "vertical" for d in plans if d.view == n)) for n in names}
    notes = len(title.notes)

    def block_at(s: Scale) -> _Block:
        return _block(names, cells, by_name, rows, s)

    def fit(sh: str, s: Scale) -> Vec2 | None:
        return _place(block_at(s), sh, notes)

    sheets = list(SHEETS) if sheet == "auto" else [sheet]
    fixed_scale = scale if scale != "auto" else None
    chosen = _choose(sheets, fixed_scale, block_at, fit)
    if chosen is None:
        raise _no_fit(sheet, fixed_scale, fit)
    sheet_name, scale_chosen, corner = chosen

    block = block_at(scale_chosen)
    s = scale_chosen[0] / scale_chosen[1]
    placed: list[PlacedView] = []
    for n in names:
        p = by_name[n]
        left, bottom = corner[0] + block.left[n], corner[1] + block.bottom[n]
        origin = (round(left - p.bbox_min[0] * s, 6), round(bottom - p.bbox_min[1] * s, 6))
        extent = (round((p.bbox_max[0] - p.bbox_min[0]) * s, 6), round((p.bbox_max[1] - p.bbox_min[1]) * s, 6))
        placed.append(PlacedView(n, p, origin, s, extent))
    view_by_name = {v.name: v for v in placed}

    dims: list[PlacedDim] = []
    used = {n: [0, 0] for n in names}
    for plan in plans:
        (x0, y0), (x1, y1) = view_by_name[plan.view].box
        if plan.orientation == "horizontal":
            used[plan.view][0] += 1
            base = (x0, round(y0 - C.DIM_ROW_MM * used[plan.view][0], 6))
            p1, p2 = (x0, y0), (x1, y0)
        else:
            used[plan.view][1] += 1
            base = (round(x0 - C.DIM_ROW_MM * used[plan.view][1], 6), y0)
            p1, p2 = (x0, y0), (x0, y1)
        dims.append(PlacedDim("linear", plan.view, plan.orientation, p1, p2, base, plan.value, format_mm(plan.value), f"bbox:{plan.axis}"))

    lines, texts, warnings = _furniture(sheet_name, scale_chosen, method, title)
    return Drawing(sheet_name, SHEETS[sheet_name], scale_chosen, method, tuple(placed), tuple(dims), tuple(lines), tuple(texts), tuple(warnings))


def _choose(sheets: list[str], fixed_scale: Scale | None, block_at, fit) -> tuple[str, Scale, Vec2] | None:
    """The first (sheet, scale, corner) that fits: see the module docstring for the order."""
    if fixed_scale is not None:
        candidates = [(sh, fixed_scale) for sh in sheets]
    else:
        first = block_at((1, 1))
        small = first.width * first.height < ENLARGE_BELOW * usable_area(sheets[0])
        candidates = ([(sheets[0], s) for s in ENLARGEMENTS] if small else []) + [(sh, s) for s in REDUCTIONS for sh in sheets]
    for sh, s in candidates:
        corner = fit(sh, s)
        if corner is not None:
            return sh, s, corner
    return None


def _largest_scale(sheet: str, fit) -> Scale | None:
    for s in REDUCTIONS:
        if fit(sheet, s) is not None:
            return s
    return None


def _no_fit(sheet: str, fixed_scale: Scale | None, fit) -> ToolFailure:
    """The ``drawing.layout_failed`` error, naming the scale or the sheet that would fit."""
    if fixed_scale is None:
        where = "sheet A0" if sheet == "auto" else f"sheet {sheet}"
        return _layout_failed(
            f"the views do not fit {where} even at scale {scale_text(REDUCTIONS[-1])}: split the part, drop views or dimensions",
            sheet=sheet, scale=scale_text(REDUCTIONS[-1]), fits_at_scale=None, fits_on_sheet=None,
        )
    widest = "A0" if sheet == "auto" else sheet
    best = _largest_scale(widest, fit)
    bigger = next((sh for sh in SHEETS if fit(sh, fixed_scale) is not None), None)
    hints = [f"scale {scale_text(best)} fits sheet {widest}"] if best else []
    if bigger and sheet != "auto":
        hints.append(f"sheet {bigger} fits at scale {scale_text(fixed_scale)}")
    where = "any sheet up to A0" if sheet == "auto" else f"sheet {sheet}"
    return _layout_failed(
        f"the views do not fit {where} at scale {scale_text(fixed_scale)}" + (f"; {'; '.join(hints)}" if hints else "; no standard scale or sheet fits"),
        sheet=sheet, scale=scale_text(fixed_scale), fits_at_scale=scale_text(best) if best else None, fits_on_sheet=bigger,
    )


__all__ = [
    "SCALES",
    "SHEETS",
    "Drawing",
    "PlacedDim",
    "PlacedView",
    "SheetLine",
    "SheetText",
    "TitleBlock",
    "clean_text",
    "compose",
    "fit_text",
    "format_mm",
    "grid_cells",
    "parse_dimensions",
    "parse_method",
    "parse_notes",
    "parse_scale",
    "parse_sheet",
    "parse_views",
    "plan_dimensions",
    "scale_text",
    "text_em",
    "usable_area",
]
