"""``mech_bolt_check``: tensile stress area, proof load, torque-preload relation and utilization of a bolt in tension.

Formulas (textbook, the usual forms of ISO 898-1 / VDI 2230 / Shigley practice; no table of a standard is reproduced)
* tensile stress area ``A_s = pi/4 * (d - 0.9382 P)^2`` (mean of pitch and minor diameter, ISO 898-1 / ISO 724 thread)
* property class ``a.b``: nominal tensile strength ``Rm = 100 a`` MPa, nominal yield / 0.2 % proof ``Rp = 10 a b`` MPa
  (that is what the designation means, ISO 898-1 clause on designation)
* proof strength ``Sp``: given, else estimated as ``0.9 Rp`` (see docs/calc-mech/design.md for the accuracy of that estimate)
* torque-preload (short form) ``T = K F d``
* bolt load under an external axial load ``P`` carried in part by the bolt: ``F_b = F_i + C P``
"""

from __future__ import annotations

import math
import re
from typing import Annotated

from pydantic import Field

from eng_toolkit import __version__
from eng_toolkit.contract import QuantityOut, ToolResult
from eng_toolkit.domains.mech._common import (
    QuantityIn,
    UnitSystemIn,
    bounded,
    is_blank,
    out,
    plain_number,
    positive_quantity,
)
from eng_toolkit.domains.mech._common import unit_system as units_for
from eng_toolkit.domains.mech._server import ENGINE, server
from eng_toolkit.runner import Computed, JobHandle, run_tool
from eng_toolkit.units import UnitsError, parse_quantity, ureg

# ISO metric coarse-thread pitches (ISO 261 / ISO 262 selected sizes), mm.  Standard geometric facts, 20 numbers; see
# docs/calc-mech/metric-coarse-pitch-data.md.
COARSE_PITCH_MM: dict[float, float] = {
    1.6: 0.35, 2.0: 0.4, 2.5: 0.45, 3.0: 0.5, 4.0: 0.7, 5.0: 0.8, 6.0: 1.0, 8.0: 1.25, 10.0: 1.5, 12.0: 1.75,
    14.0: 2.0, 16.0: 2.0, 20.0: 2.5, 24.0: 3.0, 30.0: 3.5, 36.0: 4.0, 42.0: 4.5, 48.0: 5.0, 56.0: 5.5, 64.0: 6.0,
}  # fmt: skip
CLASSES = ("4.6", "4.8", "5.6", "5.8", "6.8", "8.8", "9.8", "10.9", "12.9")
_CLASS_RE = re.compile(r"^(\d{1,2})\.(\d)$")
PROOF_FRACTION_OF_YIELD = 0.9


def tensile_stress_area_mm2(d_mm: float, pitch_mm: float) -> float:
    return math.pi / 4.0 * (d_mm - 0.9382 * pitch_mm) ** 2


def class_strengths_mpa(prop_class: str) -> tuple[float, float]:
    """(Rm, Rp) in MPa from the designation ``a.b``: ``Rm = 100 a``, ``Rp = 10 a b``."""
    match = _CLASS_RE.match(prop_class.strip()) if isinstance(prop_class, str) else None
    if match is None or match.group(0) not in CLASSES:
        raise UnitsError(
            "input.invalid",
            f"'property_class' must be one of {list(CLASSES)}, got {prop_class!r}",
            {"param": "property_class", "received": str(prop_class), "allowed": list(CLASSES)},
        )
    a, b = int(match.group(1)), int(match.group(2))
    return 100.0 * a, 10.0 * a * b


@server.tool(
    name="bolt_check",
    description=(
        "Check a metric bolt in tension: tensile stress area, proof load, the torque <-> preload relation T = K*F*d, "
        "and utilization against the proof load (optionally with an external axial load). Give EXACTLY ONE of "
        "'torque' (tightening torque -> preload) or 'preload' (-> required torque). Tension only: shear, bending, "
        "fatigue and thread stripping are not checked."
    ),
)
def bolt_check(
    diameter: Annotated[QuantityIn, Field(description="Nominal (major) thread diameter with unit, e.g. '10 mm' for M10")],
    torque: Annotated[
        str, Field(description="Tightening torque, e.g. '40 N*m' or '30 lbf*ft'. Give this OR preload, not both.")
    ] = "",
    preload: Annotated[
        str, Field(description="Target bolt preload (axial force), e.g. '20 kN'. Give this OR torque, not both.")
    ] = "",
    property_class: Annotated[
        str, Field(description="ISO 898-1 property class: 4.6, 4.8, 5.6, 5.8, 6.8, 8.8, 9.8, 10.9 or 12.9")
    ] = "8.8",
    pitch: Annotated[
        str,
        Field(
            description="Thread pitch with unit, e.g. '1.5 mm' (or '1.25 mm' for fine). Empty = the ISO coarse pitch "
            "of a standard size M1.6..M64."
        ),
    ] = "",
    nut_factor: Annotated[
        float,
        Field(description="Nut (torque) factor K in T = K*F*d, 0.05 to 1.0. About 0.2 for plain dry steel; lubricated is lower."),
    ] = 0.2,
    external_load: Annotated[
        str, Field(description="Optional external axial tension on the joint, e.g. '8 kN'. Empty = none.")
    ] = "",
    load_share: Annotated[
        float,
        Field(description="Fraction C (0 to 1) of the external load that the bolt carries; 1.0 = all of it (conservative)."),
    ] = 1.0,
    proof_strength: Annotated[
        str,
        Field(
            description="Proof stress Sp of the bolt material, e.g. '580 MPa' (from the standard or the supplier). "
            "Empty = estimated as 0.9 x the nominal yield strength of the class."
        ),
    ] = "",
    unit_system: UnitSystemIn = "metric",
) -> ToolResult:
    inputs = {
        "diameter": diameter,
        "torque": torque,
        "preload": preload,
        "property_class": property_class,
        "pitch": pitch,
        "nut_factor": nut_factor,
        "external_load": external_load,
        "load_share": load_share,
        "proof_strength": proof_strength,
        "unit_system": unit_system,
    }

    def compute(_: JobHandle) -> Computed:
        units = units_for(unit_system)
        have_t, have_f = not is_blank(torque), not is_blank(preload)
        if have_t == have_f:
            raise UnitsError(
                "input.missing" if not have_t else "input.invalid",
                "give exactly one of 'torque' (to compute the preload) or 'preload' (to compute the torque)",
                {"param": "torque|preload", "torque": torque, "preload": preload},
            )
        d = positive_quantity(diameter, name="diameter", dimension="length", unit="mm", lo=1.6, hi=100.0)
        d_mm = float(d.to("mm").magnitude)
        if is_blank(pitch):
            match = next((p for size, p in COARSE_PITCH_MM.items() if math.isclose(size, d_mm, rel_tol=1e-6)), None)
            if match is None:
                raise UnitsError(
                    "input.invalid",
                    f"no standard coarse pitch is tabulated for d = {d:~P}; give 'pitch' explicitly "
                    f"(standard sizes: {sorted(COARSE_PITCH_MM)} mm)",
                    {"param": "pitch", "diameter": str(d)},
                )
            p_mm, pitch_source = match, "ISO coarse series"
        else:
            p_q = positive_quantity(pitch, name="pitch", dimension="length", unit="mm", lo=0.2, hi=12.0)
            p_mm, pitch_source = float(p_q.to("mm").magnitude), "given"
            if p_mm >= d_mm / 2.0:
                raise UnitsError(
                    "input.invalid",
                    f"pitch {p_q:~P} is not plausible for a thread of diameter {d:~P} (pitch must be < d/2)",
                    {"param": "pitch", "pitch": str(p_q), "diameter": str(d)},
                )
        k = plain_number(nut_factor, name="nut_factor", lo=0.05, hi=1.0)
        share = plain_number(load_share, name="load_share", lo=0.0, hi=1.0)
        rm, rp = class_strengths_mpa(property_class)
        if is_blank(proof_strength):
            sp = PROOF_FRACTION_OF_YIELD * rp
            sp_source = f"estimated {PROOF_FRACTION_OF_YIELD} x nominal yield of class {property_class}"
        else:
            sp_q = positive_quantity(proof_strength, name="proof_strength", dimension="pressure", unit="MPa", lo=50.0, hi=3000.0)
            sp, sp_source = float(sp_q.to("MPa").magnitude), "given"

        a_s = tensile_stress_area_mm2(d_mm, p_mm)  # mm^2
        f_proof = sp * a_s  # N  (MPa * mm^2)
        d_m = d_mm / 1000.0
        if have_t:
            t_q = positive_quantity(torque, name="torque", dimension="torque", unit="N*m", lo=1e-3, hi=1e5)
            t_nm = float(t_q.to("N*m").magnitude)
            f_i = t_nm / (k * d_m)
        else:
            f_q = positive_quantity(preload, name="preload", dimension="force", unit="N", lo=1.0, hi=1e8)
            f_i = float(f_q.to("N").magnitude)
            t_nm = k * f_i * d_m
        ext = 0.0
        if not is_blank(external_load):
            e_q = parse_quantity(external_load, name="external_load", dimension="force")
            if float(e_q.magnitude) < 0:
                raise UnitsError("input.invalid", f"'external_load' must be >= 0 (tension), got {e_q:~P}", {"param": "external_load"})
            bounded(e_q, name="external_load", unit="N", lo=0.0, hi=1e8)
            ext = float(e_q.to("N").magnitude)
        f_b = f_i + share * ext
        util_preload = f_i / f_proof
        util = f_b / f_proof

        def F(newtons: float) -> QuantityOut:
            return out(ureg.Quantity(newtons, "N"), units["force"])

        values = {
            "utilization": QuantityOut(quantity=util, unit="dimensionless"),
            "preload": F(f_i),
            "tightening_torque": out(ureg.Quantity(t_nm, "N*m"), units["torque"]),
            "tensile_stress_area": out(ureg.Quantity(a_s, "mm**2"), units["area"]),
            "proof_load": F(f_proof),
            "bolt_load": F(f_b),
            "bolt_stress": out(ureg.Quantity(f_b / a_s, "MPa"), units["stress"]),
            "preload_utilization": QuantityOut(quantity=util_preload, unit="dimensionless"),
            "proof_safety_factor": QuantityOut(quantity=f_proof / f_b, unit="dimensionless"),
        }
        warnings: list[str] = []
        if util > 1.0:
            warnings.append(
                f"bolt load is {util:.2f} x the proof load: the bolt would take a permanent elongation; "
                "reduce the preload or use a stronger class / larger bolt"
            )
        if util_preload > 0.9:
            warnings.append(
                f"preload is {util_preload:.0%} of the proof load; textbook practice keeps preload at or below about "
                "90 % for permanent and 75 % for re-used joints"
            )
        elif util_preload < 0.25:
            warnings.append(f"preload is only {util_preload:.0%} of the proof load: the joint may loosen or the bolt see a large load swing")
        if not 0.1 <= k <= 0.3:
            warnings.append(
                f"nut factor K = {k:g} is outside the usual 0.1-0.3 (lubrication, plating and thread finish change K by "
                "tens of percent); use a measured value for a critical joint"
            )
        if is_blank(proof_strength):
            warnings.append(f"proof stress Sp = {sp:g} MPa is {sp_source}; give 'proof_strength' from the standard or the supplier for a design value")
        if not any(math.isclose(size, d_mm, rel_tol=1e-6) for size in COARSE_PITCH_MM):
            warnings.append(
                f"d = {d_mm:g} mm is not one of the tabulated ISO coarse sizes: the stress area uses the ISO metric thread "
                "formula; for an inch (UN) thread the true area is about 1 % smaller, so check the thread table"
            )
        if ext > 0 and share >= 1.0:
            warnings.append("load_share = 1: the bolt is assumed to carry the whole external load (no joint-stiffness credit)")
        return Computed(
            values=values,
            primary="utilization",
            warnings=warnings,
            data={
                "formula": "A_s = pi/4 (d - 0.9382 P)^2 ; F_p = Sp A_s ; T = K F d ; F_b = F_i + C P_ext ; utilization = F_b / F_p",
                "pitch_mm": p_mm,
                "pitch_source": pitch_source,
                "property_class": property_class,
                "tensile_strength_mpa": rm,
                "yield_strength_mpa": rp,
                "proof_strength_mpa": sp,
                "proof_strength_source": sp_source,
                "nut_factor": k,
                "solved_for": "preload" if have_t else "tightening_torque",
            },
        )

    return run_tool(
        tool_name="mech_bolt_check",
        engine=ENGINE,
        version=__version__,
        inputs=inputs,
        compute=compute,
        engines=("pint",),
        standing_warnings=(
            "Axial tension only. The torsion locked in by tightening, shear and bending, fatigue, embedment, joint "
            "stiffness and thread stripping are not checked. Torque-to-preload scatter of +/-25 % or more is normal.",
        ),
    )


__all__ = ["CLASSES", "COARSE_PITCH_MM", "bolt_check", "class_strengths_mpa", "tensile_stress_area_mm2"]
