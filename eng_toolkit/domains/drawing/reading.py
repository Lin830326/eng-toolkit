"""What the drawing readers share: limits, the drawing-unit table and helpers for the result.  Pure, no engine.

The readers (``dxf_read``, ``pdf_read``) run in a child process and hand back one plain dict of the full content;
:func:`shape` cuts that to what goes into the model's context.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

# $INSUNITS (DXF reference): code -> (name, millimetres per unit)
INSUNITS: dict[int, tuple[str, float]] = {
    1: ("inch", 25.4),
    2: ("foot", 304.8),
    3: ("mile", 1609344.0),
    4: ("millimeter", 1.0),
    5: ("centimeter", 10.0),
    6: ("meter", 1000.0),
    7: ("kilometer", 1_000_000.0),
    8: ("microinch", 25.4e-6),
    9: ("mil", 0.0254),
    10: ("yard", 914.4),
    11: ("angstrom", 1e-7),
    12: ("nanometer", 1e-6),
    13: ("micrometer", 1e-3),
    14: ("decimeter", 100.0),
    15: ("decameter", 10_000.0),
    16: ("hectometer", 100_000.0),
    17: ("gigameter", 1e12),
    18: ("astronomical unit", 149_597_870_700_000.0),
    19: ("light year", 9.4607304725808e18),
    20: ("parsec", 3.0856775814914e19),
    21: ("US survey foot", 304.8006096),
    22: ("US survey inch", 25.4000508),
    23: ("US survey yard", 914.4018288),
    24: ("US survey mile", 1609347.2187),
}
MM_PER_PT = 25.4 / 72.0


@dataclass(frozen=True)
class Limits:
    """Caps on untrusted input.  The defaults were chosen from a measurement: ezdxf loads about 4 MB of DXF per second."""

    max_file_bytes: int = 50_000_000
    max_entities: int = 500_000  # DXF: every entity in the file, blocks included
    max_pages: int = 200  # PDF
    max_pdf_objects: int = 200_000  # PDF: objects examined for active content
    max_list_items: int = 20_000  # per list in the full JSON artifact
    max_nesting: int = 16  # block inside block
    max_transformed_points: int = 5_000_000  # work allowed for the extents of nested blocks

    def as_dict(self) -> dict[str, int]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> "Limits":
        return cls(**raw) if raw else cls()


def units_info(insunits: int | None, measurement: int | None) -> tuple[dict[str, Any], list[str]]:
    """The drawing units from ``$INSUNITS``.  Never assumes: with no unit there is no millimetre factor.

    ``$MEASUREMENT`` (0 English, 1 metric) is a hint about the hatch and linetype files, not a unit; it is reported and
    never used to convert anything.
    """
    hint = {0: "english", 1: "metric"}.get(measurement) if measurement is not None else None
    warnings: list[str] = []
    if insunits in INSUNITS:
        name, per_unit = INSUNITS[insunits]
        return {"insunits": insunits, "name": name, "mm_per_unit": per_unit, "source": "$INSUNITS", "measurement_hint": hint}, warnings
    if insunits is None or insunits == 0:
        why = "$INSUNITS is missing or not an integer (R12 and older files have none)" if insunits is None else "$INSUNITS is 0 (unitless)"
        extra = f"; $MEASUREMENT says {hint}, which is not a unit" if hint else ""
        warnings.append(
            f"Drawing units are not stated in the file: {why}{extra}. Not assuming millimetres or inches: lengths are in "
            "unknown drawing units and no millimetre value is given. Ask whoever made the drawing."
        )
        return {"insunits": insunits, "name": "unitless", "mm_per_unit": None, "source": "none", "measurement_hint": hint}, warnings
    warnings.append(f"$INSUNITS is {insunits}, which is not a unit code (1 to 24) the DXF reference defines; no millimetre values are given.")
    return {"insunits": insunits, "name": f"unknown({insunits})", "mm_per_unit": None, "source": "$INSUNITS", "measurement_hint": hint}, warnings


def mm(value: float | None, mm_per_unit: float | None, digits: int = 6) -> float | None:
    """A length in millimetres, or None when the unit is not known."""
    if value is None or mm_per_unit is None:
        return None
    return round(value * mm_per_unit, digits)


def round_float(value: float, digits: int = 6) -> float:
    return round(float(value), digits) + 0.0
