"""The artifacts and the result every plot tool returns: ``plot.svg``, ``plot_data.json``, the inline SVG when small."""

from __future__ import annotations

import hashlib
import json

from eng_toolkit.contract import QuantityOut
from eng_toolkit.domains.plot._common import INLINE_SVG_MAX, Computed, JobHandle, clean, replaced_text_warning, scrub


def finish(handle: JobHandle, svg: str, data: dict, warnings: list[str], values: dict[str, float], primary: str) -> Computed:
    # every string is cleaned once more here (the last guard), so nothing a caller sent can make the UTF-8 encoding fail
    data = clean(data)
    warnings = scrub(warnings) + replaced_text_warning()
    payload = svg.encode("utf-8", "replace")
    data = {**data, "warnings": warnings, "svg_bytes": len(payload), "svg_sha256": hashlib.sha256(payload).hexdigest()}
    svg_artifact = handle.write_bytes("plot.svg", payload, fmt="svg")
    text = json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n"
    json_artifact = handle.write_bytes("plot_data.json", text.encode("utf-8", "backslashreplace"), fmt="json")
    out = dict(data)
    out["files"] = {"svg": "plot.svg", "data": "plot_data.json"}
    if len(payload) <= INLINE_SVG_MAX:
        out["svg_inline"] = payload.decode("utf-8")
    else:
        out["svg_inline"] = None
        out["svg_inline_omitted"] = f"the SVG is {len(payload)} bytes, above the {INLINE_SVG_MAX} byte inline limit; read plot.svg from the artifacts"
    quantities = {name: QuantityOut(quantity=float(v), unit="dimensionless") for name, v in values.items()}
    return Computed(values=quantities, primary=primary, warnings=warnings, data=out, artifacts=[svg_artifact, json_artifact])


__all__ = ["finish"]
