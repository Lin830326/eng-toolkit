"""The artifacts and the result every plot tool returns: ``plot.svg`` and/or ``plot.png``, ``plot_data.json``, the inline SVG when small."""

from __future__ import annotations

import hashlib
import json

from eng_toolkit.contract import QuantityOut
from eng_toolkit.domains.plot import _png
from eng_toolkit.domains.plot._common import INLINE_SVG_MAX, Computed, JobHandle, clean, replaced_text_warning, scrub
from eng_toolkit.errors import EngineUnavailable

FORMATS = ("svg", "png", "both")
DEFAULT_FORMAT = "both"
FORMAT_TEXT = "both (default: plot.svg + plot.png 1600 px wide; PNG needs the png extra), svg (no PNG) or png (no SVG)"


def wants_png(raw: object) -> bool:
    """Whether the raw ``format`` argument asks for a PNG (used before validation, to name the engine in the provenance)."""
    return isinstance(raw, str) and raw.strip().lower() in ("png", "both")


def finish(handle: JobHandle, svg: str, data: dict, warnings: list[str], values: dict[str, float], primary: str, fmt: str = DEFAULT_FORMAT) -> Computed:
    # every string is cleaned once more here (the last guard), so nothing a caller sent can make the UTF-8 encoding fail
    data = clean(data)
    warnings = scrub(warnings) + replaced_text_warning()
    payload = svg.encode("utf-8", "replace")
    data = {**data, "svg_bytes": len(payload), "svg_sha256": hashlib.sha256(payload).hexdigest()}
    picture = None
    png_error = None
    if fmt in ("png", "both"):
        try:
            picture = _png.render(svg)
        except EngineUnavailable as exc:
            png_error = {"code": exc.family, "message": exc.message, "install": exc.details.get("install", ""), "module": exc.details.get("module", "")}
            warnings.append(
                f"NO PNG WAS WRITTEN although format={fmt!r} was asked for: {exc.message.rstrip('. ')}. "
                "The chart itself is complete: plot.svg is returned instead (data.png_path is null)."
            )
    write_svg = fmt in ("svg", "both") or picture is None
    artifacts = []
    files = {}
    out_extra: dict = {}
    if picture is not None:
        digest = hashlib.sha256(picture.png).hexdigest()
        if picture.substituted:
            warnings.append(
                f"{picture.substituted} character(s) have no glyph in the PNG's fonts (Latin, Greek and common symbols; CJK uses a system font) and are drawn as '?'; "
                "the SVG keeps them"
            )
        if picture.cjk:
            warnings.append("CJK text in the PNG is drawn with a CJK font of the machine that rendered it; if that machine has none the characters are blank")
        data["png"] = {"width_px": picture.width, "height_px": picture.height, "pixels_per_svg_px": round(picture.scale, 4), "bytes": len(picture.png), "sha256": digest,
                       "from": "plot.svg redrawn as a PDF page and rasterised by PDFium; the light theme's colours for theme=auto"}
        out_extra = {"png_width": picture.width, "png_height": picture.height}
    elif png_error is not None:
        data["png"] = {"written": False, "error": png_error}
        out_extra = {"png_path": None}
    data = {**data, "format": fmt if png_error is None else "svg", "warnings": warnings}
    if write_svg:
        artifacts.append(handle.write_bytes("plot.svg", payload, fmt="svg"))
        files["svg"] = "plot.svg"
    if picture is not None:
        png_artifact = handle.write_bytes("plot.png", picture.png, fmt="png")
        artifacts.append(png_artifact)
        files["png"] = "plot.png"
        out_extra["png_path"] = png_artifact.path
    text = json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n"
    json_artifact = handle.write_bytes("plot_data.json", text.encode("utf-8", "backslashreplace"), fmt="json")
    artifacts.append(json_artifact)
    files["data"] = "plot_data.json"
    out = dict(data)
    if picture is not None:
        out["png"] = {**data["png"], "path": out_extra["png_path"]}  # the path holds the job id: not in plot_data.json, which must be reproducible
    out["files"] = files
    out.update(out_extra)
    if write_svg and len(payload) <= INLINE_SVG_MAX:
        out["svg_inline"] = payload.decode("utf-8")
    elif write_svg:
        out["svg_inline"] = None
        out["svg_inline_omitted"] = f"the SVG is {len(payload)} bytes, above the {INLINE_SVG_MAX} byte inline limit; read plot.svg from the artifacts"
    else:
        out["svg_inline"] = None
        out["svg_inline_omitted"] = "format is png: no SVG was written; ask for format=both to get the SVG too"
    quantities = {name: QuantityOut(quantity=float(v), unit="dimensionless") for name, v in values.items()}
    return Computed(values=quantities, primary=primary, warnings=warnings, data=out, artifacts=artifacts)


__all__ = ["DEFAULT_FORMAT", "FORMATS", "FORMAT_TEXT", "finish", "wants_png"]
