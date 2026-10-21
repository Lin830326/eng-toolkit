"""``dsp_window``: window functions and their numerical properties against the known closed values.

Coherent gain ``sum(w)/N``; equivalent noise bandwidth ``N sum(w^2)/sum(w)^2`` bins; scalloping loss ``-20 log10(|W(0.5 bin)|/|W(0)|)``
(worst-case amplitude error of an off-bin tone); highest side-lobe level (the largest local maximum of ``|W|/|W(0)|`` beyond the first
null); 3 dB bandwidth (full width at ``|W| = |W(0)|/sqrt(2)``).  The spectrum is a zero-padded FFT (at least 8 points per bin), refined by
evaluating the DTFT at arbitrary frequencies, so no result depends on the grid.
"""

from __future__ import annotations

import math
from typing import Annotated, Any

import numpy as np
from pydantic import Field

from eng_toolkit.contract import ToolResult
from eng_toolkit.domains.dsp import _windows as win
from eng_toolkit.domains.dsp._common import Computed, JobHandle, QuantityOut, choice, clean, csv_text, plain, plain_number, qty, run, whole_number
from eng_toolkit.domains.dsp._server import server
from eng_toolkit.units import UnitsError

MAX_LENGTH = 65536


@server.tool(
    name="window",
    description=(
        "Window function and its properties: coherent gain, equivalent noise bandwidth (bins), scalloping loss, highest side-lobe level "
        "and 3 dB bandwidth, computed numerically (zero-padded FFT refined by the exact DTFT) and compared with the textbook closed values "
        "for rectangular, hann, hamming, blackman and flattop. Also kaiser (give kaiser_beta). Returns the window samples and its "
        "magnitude response near the main lobe as CSV. periodic=true is the form used in spectrum analysis, false the symmetric form for filter design."
    ),
)
def window(
    window: Annotated[str, Field(description="rectangular, hann, hamming, blackman, flattop or kaiser")],
    length: Annotated[int, Field(description="Number of samples, 8..65536")] = 1024,
    periodic: Annotated[bool, Field(description="Periodic (DFT-even, spectrum analysis; default true) or symmetric (filter design) form")] = True,
    kaiser_beta: Annotated[float, Field(description="Kaiser only: beta, 0..50")] = 0.0,
) -> ToolResult:
    inputs = {"window": window, "length": length, "periodic": periodic, "kaiser_beta": kaiser_beta}

    def compute(handle: JobHandle) -> Computed:
        name = choice(window, name="window", allowed=win.ALL_WINDOWS)
        n = whole_number(length, name="length", lo=8, hi=MAX_LENGTH)
        if not isinstance(periodic, bool):
            raise UnitsError("input.invalid", "'periodic' must be true or false", {"param": "periodic"})
        beta = plain_number(kaiser_beta, name="kaiser_beta", lo=0.0, hi=50.0)
        if name != "kaiser" and beta:
            raise UnitsError("input.invalid", "'kaiser_beta' applies to the kaiser window only", {"param": "kaiser_beta"})
        w = win.window_values(name, n, periodic=periodic, beta=beta)
        props = win.properties(w)
        warnings: list[str] = []
        values: dict[str, QuantityOut] = {
            "coherent_gain": plain(props["coherent_gain"], label="coherent_gain"), "enbw_bins": plain(props["enbw_bins"], label="enbw_bins"),
            "scalloping_loss_db": qty(props["scalloping_loss_db"], "decibel", label="scalloping_loss_db"),
            "bandwidth_3db_bins": plain(props["bandwidth_3db_bins"], label="bandwidth_3db_bins"),
        }
        if "highest_sidelobe_db" in props:
            values["highest_sidelobe_db"] = qty(props["highest_sidelobe_db"], "decibel", label="highest_sidelobe_db")
        data: dict[str, Any] = {
            "window": name, "length": n, "form": "periodic" if periodic else "symmetric", "kaiser_beta": beta if name == "kaiser" else None,
            "properties": props, "amplitude_correction_factor": 1.0 / props["coherent_gain"], "processing_gain_db_vs_rectangular": -10.0 * math.log10(props["enbw_bins"]),
            "definitions": {
                "coherent_gain": "sum(w)/N: the amplitude a unit on-bin sine reads before correction",
                "enbw_bins": "N sum(w^2)/sum(w)^2: width in bins of the rectangle with the same noise power",
                "scalloping_loss_db": "-20 log10(|W(0.5 bin)|/|W(0)|): worst amplitude error of a tone between two bins",
                "highest_sidelobe_db": "largest local maximum of 20 log10(|W|/|W(0)|) beyond the first null",
                "bandwidth_3db_bins": "full width where |W| = |W(0)|/sqrt(2)",
            },
        }
        if name in win.COSINE_SUM:
            ref = win.limit_properties(name)
            rows = {}
            agree = True
            for key, expected in ref.items():
                if key not in props:
                    continue
                diff = props[key] - expected
                ok = abs(diff) <= win.REFERENCE_TOLERANCE[key]
                agree = agree and ok
                rows[key] = {"computed": props[key], "closed_form_limit": expected, "difference": diff, "tolerance": win.REFERENCE_TOLERANCE[key], "agrees": bool(ok)}
            data["reference_comparison"] = rows
            data["agrees_with_textbook"] = bool(agree)
            data["reference_note"] = "reference = the closed form for a window of unlimited length: a0 sinc(d) + sum (a_k/2)(sinc(d-k) + sinc(d+k)) (the textbook values: Hann ENBW 1.5 bins, scalloping loss 1.4236 dB, ...); a finite or symmetric window differs by O(1/N)"
            if not agree and n >= 256 and periodic:
                warnings.append("a property differs from its textbook value by more than the stated tolerance")
        else:
            data["reference_comparison"] = "no closed textbook values for the kaiser window (they depend on beta)"
        if n < 64:
            warnings.append("a short window: properties differ noticeably from the long-window textbook values")
        # response table: offsets 0..min(16, N/2) bins
        top = min(16.0, n / 2.0)
        points = int(top * 32) + 1
        offsets = np.linspace(0.0, top, points)
        mag = np.abs(win.dtft(w, offsets))
        mag_db = 20.0 * np.log10(np.maximum(mag / mag[0], 1e-15))
        t1 = handle.write_text("window.csv", csv_text(["index", "value"], [np.arange(n, dtype=float), w]), fmt="csv")
        t2 = handle.write_text("window_response.csv", csv_text(["offset_bins", "magnitude_db_rel_peak"], [offsets, mag_db]), fmt="csv")
        data["tables"] = {"window.csv": {"rows": n, "columns": ["index", "value"]}, "window_response.csv": {"rows": points, "columns": ["offset_bins", "magnitude_db_rel_peak"], "floor_db": -300}}
        return Computed(values=values, primary="enbw_bins", warnings=warnings, data=clean(data), artifacts=[t1, t2])

    return run("dsp_window", inputs, compute)


__all__ = ["window"]
