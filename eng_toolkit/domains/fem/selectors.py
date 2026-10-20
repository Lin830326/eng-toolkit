"""The small, closed language that picks faces, points, supports and loads.  Nothing here is ever executed or passed on as text.

Every clause is parsed by a fixed grammar into numbers (millimetres, newtons, megapascals) and indices.  The text is first held to
a character whitelist (letters, digits, space and ``. , ; : = ( ) + - * / ^``: no shell metacharacter, quote, backslash, newline or
brace passes), then matched against the patterns below, then every quantity goes through the unit gatekeeper.  What reaches
Gmsh or CalculiX is only what the toolkit builds from these numbers.

Face selectors (all faces of the meshed solid lying in a plane, within a tolerance of 1e-6 of the part's diagonal)::

    x = 0 mm                    every boundary facet whose nodes all lie in the plane x = 0 (either side of the material)
    normal +z at z = 20 mm      the same, but only facets whose outward normal points to +z
    normal -x at x = 5 mm       axis x, y or z, sign + or -; the normal axis must be the plane's axis

Supports, ``;`` separated, each ``<target> [: <dofs>]``; no dofs means ``fixed``::

    x = 0 mm                    all three displacements zero on that face
    x = 0 mm : ux               only ux is zero (a roller)
    z = 0 mm : uz, uy           several, separated by commas or spaces; ``fixed`` = ux uy uz
    point(0 mm, 5 mm, 0 mm) : uy    the mesh node nearest that point (it must be within one element size)

Loads, ``;`` separated::

    force (0 N, 0 N, -1 kN) on x = 100 mm       total force vector, spread over the face by area (a uniform traction)
    pressure 5 MPa on normal +z at z = 20 mm    normal pressure, positive pushes into the material, negative pulls out
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

import numpy as np

from eng_toolkit.errors import ToolFailure
from eng_toolkit.units import UnitsError, parse_quantity

MAX_TEXT = 2000
MAX_CLAUSES = 20
MAX_LENGTH_MM = 1e7
MAX_FORCE_N = 1e12
MAX_PRESSURE_MPA = 1e7
PLANE_TOLERANCE = 1e-6  # of the part's diagonal
_ALLOWED = re.compile(r"[A-Za-z0-9 .,;:=()+\-*/^]*")
_NUMBER = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
_QUANTITY = rf"{_NUMBER}\s*[A-Za-z]*(?:\s*[/*^]\s*[A-Za-z0-9.]+)*"
_PLANE = re.compile(rf"^(?:normal\s*(?P<sign>[+-])\s*(?P<naxis>[xyz])\s+at\s+)?(?P<axis>[xyz])\s*=\s*(?P<offset>{_QUANTITY})$", re.IGNORECASE)
_POINT = re.compile(rf"^point\s*\(\s*(?P<x>{_QUANTITY})\s*,\s*(?P<y>{_QUANTITY})\s*,\s*(?P<z>{_QUANTITY})\s*\)$", re.IGNORECASE)
_FORCE = re.compile(rf"^force\s*\(\s*(?P<fx>{_QUANTITY})\s*,\s*(?P<fy>{_QUANTITY})\s*,\s*(?P<fz>{_QUANTITY})\s*\)\s+on\s+(?P<target>.+)$", re.IGNORECASE)
_PRESSURE = re.compile(rf"^pressure\s+(?P<p>{_QUANTITY})\s+on\s+(?P<target>.+)$", re.IGNORECASE)
_DOFS = re.compile(r"^(?:fixed|u[xyz](?:[ ,]+u[xyz])*)$", re.IGNORECASE)
AXES = "xyz"


@dataclass(frozen=True)
class PlaneSelector:
    axis: int  # 0, 1, 2
    offset_mm: float
    sign: int  # +1, -1, or 0 for either side
    text: str


@dataclass(frozen=True)
class PointSelector:
    xyz_mm: tuple[float, float, float]
    text: str


@dataclass(frozen=True)
class Support:
    target: PlaneSelector | PointSelector
    dofs: tuple[bool, bool, bool]
    text: str


@dataclass(frozen=True)
class Load:
    kind: str  # "force" or "pressure"
    target: PlaneSelector
    force_n: tuple[float, float, float] | None  # kind == "force"
    pressure_mpa: float | None  # kind == "pressure"
    text: str


def _bad(param: str, clause: str, why: str) -> UnitsError:
    return UnitsError("input.invalid", f"{param}: {why} in {clause!r}", {"param": param, "clause": clause[:200]})


def _check_text(text: str, param: str) -> None:
    if not isinstance(text, str):
        raise UnitsError("input.invalid", f"{param} must be text", {"param": param})
    if len(text) > MAX_TEXT:
        raise UnitsError("input.invalid", f"{param} is longer than {MAX_TEXT} characters", {"param": param})
    if not _ALLOWED.fullmatch(text):
        odd = sorted({c for c in text if not _ALLOWED.fullmatch(c)})
        shown = ", ".join(repr(c) for c in odd[:6])
        raise UnitsError(
            "input.invalid",
            f"{param} holds characters the selector language does not allow ({shown}); it takes letters, digits, spaces and . , ; : = ( ) + - * / ^",
            {"param": param, "characters": [repr(c) for c in odd[:6]]},
        )


def _clauses(text: str, param: str) -> list[str]:
    _check_text(text, param)
    parts = [p.strip() for p in text.split(";")]
    parts = [p for p in parts if p]
    if not parts:
        raise UnitsError("input.invalid", f"{param} is empty: give at least one clause", {"param": param})
    if len(parts) > MAX_CLAUSES:
        raise UnitsError("input.invalid", f"{param} has {len(parts)} clauses; at most {MAX_CLAUSES}", {"param": param})
    return parts


def _quantity(raw: str, *, dimension: str, unit: str, limit: float, param: str, clause: str) -> float:
    qty = parse_quantity(raw.strip(), name=f"{param} ({clause})", dimension=dimension)
    try:
        value = float(qty.to(unit).magnitude)
    except Exception as exc:  # noqa: BLE001 - pint raises several types for an overflow or an offset unit
        raise _bad(param, clause, f"cannot convert {raw!r} to {unit} ({exc})") from exc
    if not math.isfinite(value) or abs(value) > limit:
        raise UnitsError(
            "input.out_of_range",
            f"{param}: {raw.strip()!r} in {clause!r} is outside the supported range (at most {limit:g} {unit})",
            {"param": param, "clause": clause[:200], "max": limit, "unit": unit},
        )
    return value


def parse_plane(text: str, param: str, clause: str | None = None) -> PlaneSelector:
    clause = clause if clause is not None else text
    match = _PLANE.match(text.strip())
    if not match:
        raise _bad(
            param,
            clause,
            "not a face selector; expected '<x|y|z> = <length>' or 'normal <+|-><axis> at <axis> = <length>', e.g. 'x = 0 mm' or 'normal +z at z = 20 mm'",
        )
    axis = AXES.index(match["axis"].lower())
    sign = 0
    if match["sign"]:
        if match["naxis"].lower() != match["axis"].lower():
            raise _bad(param, clause, "the normal's axis must be the axis of the plane (a facet lying in z = 20 can only face +z or -z)")
        sign = 1 if match["sign"] == "+" else -1
    offset = _quantity(match["offset"], dimension="length", unit="millimeter", limit=MAX_LENGTH_MM, param=param, clause=clause)
    return PlaneSelector(axis=axis, offset_mm=offset, sign=sign, text=" ".join(text.split()))


def parse_point(text: str, param: str, clause: str) -> PointSelector:
    match = _POINT.match(text.strip())
    if not match:
        raise _bad(param, clause, "not a point; expected 'point(<length>, <length>, <length>)', e.g. 'point(0 mm, 5 mm, 0 mm)'")
    xyz = tuple(
        _quantity(match[k], dimension="length", unit="millimeter", limit=MAX_LENGTH_MM, param=param, clause=clause) for k in ("x", "y", "z")
    )
    return PointSelector(xyz_mm=xyz, text=" ".join(text.split()))  # type: ignore[arg-type]


def parse_supports(text: str) -> list[Support]:
    out: list[Support] = []
    for clause in _clauses(text, "supports"):
        target_text, _, dof_text = clause.partition(":")
        dof_text = dof_text.strip()
        if ":" in dof_text:
            raise _bad("supports", clause, "more than one ':'")
        if dof_text and not _DOFS.match(dof_text):
            raise _bad("supports", clause, "the part after ':' must be 'fixed' or some of ux, uy, uz")
        words = {w.lower() for w in re.split(r"[ ,]+", dof_text) if w}
        if not words or "fixed" in words:
            dofs = (True, True, True)
        else:
            dofs = tuple(f"u{a}" in words for a in AXES)
        target: PlaneSelector | PointSelector
        if target_text.strip().lower().startswith("point"):
            target = parse_point(target_text, "supports", clause)
        else:
            target = parse_plane(target_text, "supports", clause)
        out.append(Support(target=target, dofs=dofs, text=" ".join(clause.split())))  # type: ignore[arg-type]
    return out


def parse_loads(text: str) -> list[Load]:
    out: list[Load] = []
    for clause in _clauses(text, "loads"):
        force = _FORCE.match(clause)
        pressure = _PRESSURE.match(clause)
        if force:
            components = tuple(
                _quantity(force[k], dimension="force", unit="newton", limit=MAX_FORCE_N, param="loads", clause=clause) for k in ("fx", "fy", "fz")
            )
            if math.sqrt(sum(c * c for c in components)) > MAX_FORCE_N:
                raise UnitsError("input.out_of_range", f"loads: the force in {clause!r} exceeds {MAX_FORCE_N:g} N", {"param": "loads", "max": MAX_FORCE_N, "unit": "newton"})
            if not any(components):
                raise _bad("loads", clause, "the force is zero")
            out.append(Load("force", parse_plane(force["target"], "loads", clause), components, None, " ".join(clause.split())))  # type: ignore[arg-type]
        elif pressure:
            value = _quantity(pressure["p"], dimension="pressure", unit="megapascal", limit=MAX_PRESSURE_MPA, param="loads", clause=clause)
            if value == 0.0:
                raise _bad("loads", clause, "the pressure is zero")
            out.append(Load("pressure", parse_plane(pressure["target"], "loads", clause), None, value, " ".join(clause.split())))
        else:
            raise _bad(
                "loads",
                clause,
                "not a load; expected 'force (<F>, <F>, <F>) on <face>' or 'pressure <P> on <face>', e.g. 'force (0 N, 0 N, -1 kN) on x = 100 mm'",
            )
    return out


# --------------------------------------------------------------------------- applying a selector to a mesh


def select_facets(selector: PlaneSelector, nodes: np.ndarray, corners: np.ndarray, mids: np.ndarray, normals: np.ndarray, diagonal_mm: float) -> np.ndarray:
    """Indices of the boundary facets that lie in the plane (all six nodes within the tolerance) and, with a sign, face that way."""
    tol = max(PLANE_TOLERANCE * diagonal_mm, 1e-9)
    offsets = np.abs(nodes[np.concatenate([corners, mids], axis=1)][:, :, selector.axis] - selector.offset_mm)
    inside = np.all(offsets <= tol, axis=1)
    if selector.sign:
        inside &= normals[:, selector.axis] * selector.sign > 0.5
    return np.nonzero(inside)[0]


def empty_selection(text: str, what: str, diagonal_mm: float) -> ToolFailure:
    return ToolFailure(
        "fem.empty_selection",
        f"the selector {text!r} ({what}) matches no face of the model: no boundary face lies in that plane"
        + (" with that normal" if "normal" in text else "")
        + f" (tolerance {max(PLANE_TOLERANCE * diagonal_mm, 1e-9):.3g} mm). Check the coordinate, its unit and the axis",
        {"selector": text, "tolerance_mm": max(PLANE_TOLERANCE * diagonal_mm, 1e-9)},
    )
