"""The pure side of ``drawing_convert``: what the caller may ask for, the limits on a hostile file, the page and the scale.

No DXF library and no MCP stack in here, so the child process that converts the file (``dxf.convert_file``) and the tests can
import it.  The numbers are millimetres on the sheet unless a name says otherwise.

* **Scale and page.**  ``factor`` is millimetres on the sheet per drawing unit: ``mm_per_unit`` (from ``$INSUNITS``) times the
  scale ratio.  At 1:1 a 100 mm line is 100 mm on the page, 283.465 pt (100 / 25.4 * 72) in the PDF.  ``sheet="auto"``
  makes the page the extents of the drawing plus 10 mm on every side; a named ISO 5457 sheet is used landscape, or portrait
  when only that fits.  ``scale="auto"`` is true size (1:1) on an automatic sheet when the page then fits an A0 sheet, else the
  largest ISO 5455 scale that does; on a named sheet it is the largest ISO 5455 scale (never an enlargement) at which the
  extents fit inside the sheet minus the 10 mm margins.  A scale and a sheet that do not fit together are refused, never clipped.
* **Limits.**  A hostile DXF can be big in many ways: entities, nested and repeated block references (an INSERT array of a
  million columns), vertices, text, coordinates near 1e308.  :class:`ConvertLimits` bounds each; the work counter
  (:func:`weigh`) is entities plus vertices plus text characters, counted after the block references are expanded.
"""

from __future__ import annotations

import math
import os
import re
from dataclasses import asdict, dataclass
from fractions import Fraction

from eng_toolkit.domains.drawing import sheet as S
from eng_toolkit.errors import ToolFailure

OUTPUT_FORMATS: tuple[str, ...] = ("pdf", "svg", "png", "dxf")
DXF_VERSIONS: dict[str, str] = {  # release name -> $ACADVER; ezdxf writes R12 and R2000 or later (not R13 and R14)
    "R12": "AC1009",
    "R2000": "AC1015",
    "R2004": "AC1018",
    "R2007": "AC1021",
    "R2010": "AC1024",
    "R2013": "AC1027",
    "R2018": "AC1032",
}
DEFAULT_DXF_VERSION = "R2018"
# ISO 5455, largest first: enlargements 50:1 ... 2:1, true size, reductions 1:2 ... 1:10000
ISO_SCALES: tuple[tuple[int, int], ...] = (
    (50, 1), (20, 1), (10, 1), (5, 1), (2, 1), (1, 1),
    (1, 2), (1, 5), (1, 10), (1, 20), (1, 50), (1, 100), (1, 200), (1, 500), (1, 1000), (1, 2000), (1, 5000), (1, 10000),
)  # fmt: skip
MARGIN_MM = 10.0
A0_LANDSCAPE = S.SHEETS["A0"]

ENV_TIMEOUT, ENV_MEMORY, ENV_MAX_MB = "ENG_TOOLKIT_CONVERT_TIMEOUT_S", "ENG_TOOLKIT_CONVERT_MEMORY_MB", "ENG_TOOLKIT_CONVERT_MAX_MB"


@dataclass(frozen=True)
class ConvertLimits:
    """Caps on a file that converts.  Measured with ezdxf 1.4.5: about 50 000 lines or 30 000 circles per second reach the recording."""

    max_file_bytes: int = 50_000_000
    max_entities: int = 200_000  # every entity in the file, blocks included
    max_work: int = 1_000_000  # entities + vertices + 5 per text character, after block references are expanded
    max_nesting: int = 16  # block inside block
    max_text_chars: int = 20_000  # one TEXT or MTEXT; longer ones are not drawn and are listed
    max_page_mm: float = 5080.0  # 200 inch, the largest page PDF 1.4 viewers are specified for
    max_coordinate_mm: float = 1e12  # any coordinate, length or factor of an entity, in millimetres
    max_output_bytes: int = 200_000_000

    def as_dict(self) -> dict[str, float]:
        return asdict(self)


LIMITS = ConvertLimits()


def _env_number(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    try:
        value = float(raw) if raw else default
    except ValueError:
        return default
    return value if math.isfinite(value) and value > 0 else default


def runtime_limits(default_timeout: float, default_memory: int) -> tuple[ConvertLimits, float, int]:
    """The limits for one call; the operator can change the file size, the time and the memory with environment variables."""
    limits = ConvertLimits(max_file_bytes=int(_env_number(ENV_MAX_MB, LIMITS.max_file_bytes / 1e6) * 1e6))
    return limits, _env_number(ENV_TIMEOUT, 60.0 if default_timeout < 60.0 else default_timeout), int(_env_number(ENV_MEMORY, default_memory))


# --------------------------------------------------------------------------- the strings


def _invalid(param: str, message: str, **details) -> ToolFailure:
    return ToolFailure("input.invalid", f"{param}: {message}", {"param": param, **details})


def parse_format(text: str) -> str:
    value = text.strip().lower().lstrip(".")
    if value not in OUTPUT_FORMATS:
        raise _invalid("output_format", f"{text!r} is not an output format; use one of {list(OUTPUT_FORMATS)}", allowed=list(OUTPUT_FORMATS))
    return value


def parse_dxf_version(text: str) -> str:
    value = text.strip().upper()
    if value in ("R13", "R14", "R15", "R2005", "R2006", "R2008", "R2009", "R2011", "R2012", "R2014", "R2015", "R2016", "R2017", "R2019"):
        raise _invalid("dxf_version", f"{text!r} cannot be written; use one of {list(DXF_VERSIONS)} (R13 and R14 are not written by the DXF library)", allowed=list(DXF_VERSIONS))
    if value not in DXF_VERSIONS:
        raise _invalid("dxf_version", f"{text!r} is not a DXF version; use one of {list(DXF_VERSIONS)}", allowed=list(DXF_VERSIONS))
    return value


_RATIO = re.compile(r"^(\d{1,6}):(\d{1,6})$")


def parse_scale(text: str) -> tuple[int, int] | None:
    """``None`` for auto, else an ISO 5455 ratio as (numerator, denominator)."""
    value = text.strip().lower().replace(" ", "")
    if value == "auto":
        return None
    found = _RATIO.match(value)
    if found:
        ratio = (int(found.group(1)), int(found.group(2)))
        if ratio in ISO_SCALES:
            return ratio
    allowed = ["auto", *(f"{n}:{d}" for n, d in ISO_SCALES)]
    raise _invalid("scale", f"{text!r} is not an ISO 5455 scale; use one of {allowed}", allowed=allowed)


def scale_text(ratio: tuple[int, int]) -> str:
    return f"{ratio[0]}:{ratio[1]}"


# --------------------------------------------------------------------------- the page


@dataclass(frozen=True)
class Page:
    sheet: str  # "A3" ... or "auto" (extents plus margins)
    width_mm: float
    height_mm: float
    ratio: tuple[int, int]
    orientation: str  # landscape, portrait or custom

    @property
    def scale(self) -> float:
        return self.ratio[0] / self.ratio[1]


def _too_big(what: str, value: float, limit: float, hint: str) -> ToolFailure:
    return ToolFailure(
        "input.out_of_range",
        f"{what} is {value:.6g} mm, over the {limit:g} mm the converter allows; {hint}",
        {"value_mm": value, "limit_mm": limit},
    )


def _fits(width: float, height: float, sheet: tuple[float, float]) -> str | None:
    """The orientation (landscape preferred) in which content of this size fits inside the sheet minus the margins, or None."""
    long_, short = max(sheet), min(sheet)
    if width <= long_ - 2 * MARGIN_MM and height <= short - 2 * MARGIN_MM:
        return "landscape"
    if width <= short - 2 * MARGIN_MM and height <= long_ - 2 * MARGIN_MM:
        return "portrait"
    return None


def choose_page(width_mm: float, height_mm: float, sheet: str, ratio: tuple[int, int] | None, limits: ConvertLimits = LIMITS) -> Page:
    """The page for content of ``width_mm`` x ``height_mm`` at true size: see the module docstring.

    Raises ``input.out_of_range`` when the page would pass ``limits.max_page_mm`` or the content does not fit the sheet, and
    ``drawing.layout_failed`` when a fixed sheet holds the extents at no ISO 5455 scale.
    """
    if not (math.isfinite(width_mm) and math.isfinite(height_mm)) or width_mm < 0 or height_mm < 0:
        raise ToolFailure("input.out_of_range", "the extents of the drawing are not finite", {})
    if sheet != "auto":
        paper = S.SHEETS[sheet]
        candidates = [ratio] if ratio else [c for c in ISO_SCALES if c[0] <= c[1]]  # auto never enlarges
        for candidate in candidates:
            s = candidate[0] / candidate[1]
            orientation = _fits(width_mm * s, height_mm * s, paper)
            if orientation:
                w, h = (max(paper), min(paper)) if orientation == "landscape" else (min(paper), max(paper))
                return Page(sheet, w, h, candidate, orientation)
        if ratio:
            s = ratio[0] / ratio[1]
            raise ToolFailure(
                "drawing.layout_failed",
                f"the drawing is {width_mm * s:.6g} x {height_mm * s:.6g} mm at {scale_text(ratio)}, more than fits a {sheet} sheet inside its "
                f"{MARGIN_MM:g} mm margins ({paper[0] - 2 * MARGIN_MM:g} x {paper[1] - 2 * MARGIN_MM:g} mm); use a larger sheet, a smaller scale or sheet='auto'",
                {"param": "scale", "drawing_mm": [width_mm * s, height_mm * s], "sheet": sheet},
            )
        raise ToolFailure(
            "drawing.layout_failed",
            f"the drawing is {width_mm:.6g} x {height_mm:.6g} mm, more than a {sheet} sheet holds even at 1:10000; use a larger sheet or sheet='auto'",
            {"param": "sheet", "drawing_mm": [width_mm, height_mm], "sheet": sheet},
        )
    if ratio is None:  # true size when the page is no larger than A0, else the largest ISO scale that gives such a page
        ratio = next((c for c in ISO_SCALES if c[0] <= c[1] and _fits(width_mm * c[0] / c[1], height_mm * c[0] / c[1], A0_LANDSCAPE)), ISO_SCALES[-1])
    s = ratio[0] / ratio[1]
    width, height = width_mm * s + 2 * MARGIN_MM, height_mm * s + 2 * MARGIN_MM
    for side in (width, height):
        if side > limits.max_page_mm:
            hint = (
                f"use a smaller scale than {scale_text(ratio)} (for example 1:100)"
                if ratio != ISO_SCALES[-1]
                else "the drawing is too large for any ISO 5455 scale: check $INSUNITS and stray entities far from the drawing"
            )
            raise _too_big("the page", side, limits.max_page_mm, hint)
    return Page("auto", width, height, ratio, "custom")


def factor(mm_per_unit: float, page: Page) -> float:
    """Millimetres on the sheet per drawing unit."""
    return mm_per_unit * page.scale


def exact_scale(ratio: tuple[int, int]) -> Fraction:
    return Fraction(ratio[0], ratio[1])


# --------------------------------------------------------------------------- the work


def weigh(entities: int, vertices: int, text_chars: int) -> int:
    """The work of drawing something: one per entity, one per vertex or control point, five per text character."""
    return entities + vertices + 5 * text_chars


def too_large(what: str, value: int, limit: int) -> ToolFailure:
    return ToolFailure(
        "drawing.too_large",
        f"{what}: {value} is over the limit of {limit}; split the drawing or remove what is not needed",
        {"what": what, "value": value, "limit": limit},
    )


# --------------------------------------------------------------------------- losses


def loss(code: str, message: str, count: int | None = None, **extra) -> dict:
    """One row of ``data.losses.items``: a stable ``code``, a sentence, and how many things it concerns."""
    row: dict = {"code": code, "message": message}
    if count is not None:
        row["count"] = count
    row.update(extra)
    return row


# what each older DXF release cannot hold beyond the entity types it lacks (those are counted by writing the file and reading it back)
OLD_RELEASE_NOTES: dict[str, tuple[tuple[str, str], ...]] = {
    "R12": (
        ("lineweight", "R12 has no lineweights: the layer and entity line weights are not stored"),
        ("true_colour", "R12 has only the 255 indexed colours: true colours are not stored"),
        ("transparency", "R12 has no transparency"),
        ("units", "R12 has no $INSUNITS: the drawing units are not stored in the file"),
        ("unicode", "R12 text is in a code page: characters outside it are stored as \\U+XXXX escapes"),
    ),
    "R2000": (
        ("true_colour", "R2000 has only the 255 indexed colours: true colours are not stored"),
        ("transparency", "R2000 has no transparency"),
        ("unicode", "R2000 text is in a code page: characters outside it are stored as \\U+XXXX escapes"),
    ),
    "R2004": (("unicode", "R2004 text is in a code page: characters outside it are stored as \\U+XXXX escapes"),),
}
