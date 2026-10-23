"""Linear static analysis with CalculiX: the input deck the toolkit writes itself, the run, and the results read back.

The deck is built from arrays only: node coordinates, element connectivity, two material numbers, constrained degrees of freedom
and nodal forces.  Every name in it (``NALL``, ``EALL``, ``NSUP``, ``MAT``) is a constant of this module and every number is a
finite float or an integer, so no text from a caller, a file or a parameter can reach it, and the
keywords ``*INCLUDE`` and ``*INPUT`` (which would read another file) are never written.  Units are N, mm, MPa (N/mm2).

CalculiX prints with seven significant digits to ``.dat`` and six to ``.frd``.  Displacements and reactions are read from ``.dat``
(seven digits, the equilibrium check depends on it); nodal stresses only exist in ``.frd`` and carry six digits (and the
extrapolation CalculiX does from the integration points, see ``docs/fem/design.md``).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from eng_toolkit import external as ext
from eng_toolkit.domains.fem.mesh import TetMesh
from eng_toolkit.errors import ToolFailure
from eng_toolkit.external_catalog import CCX

CCX_TIMEOUT_S = 240.0
CCX_MEMORY_MB = 6144
CCX_MAX_OUTPUT_BYTES = 150_000_000
JOB = "job"  # the CalculiX job name: job.inp in, job.dat / job.frd out
RIGID_TOLERANCE = 1e-6  # smallest singular value of the constraint/rigid-motion matrix, relative to its largest
_MOTIONS = ("translation along x", "translation along y", "translation along z", "rotation about x", "rotation about y", "rotation about z")
MAX_DISPLACEMENT_MM = 1e9
MAX_STRESS_MPA = 1e9
MAX_REACTION_N = 1e24


# --------------------------------------------------------------------------- rigid body motion


def free_rigid_motions(nodes: np.ndarray, fixed: np.ndarray) -> list[str]:
    """The rigid motions the constraints leave free; empty when the body is held.

    The six rigid motions (three translations, three rotations about axes through the centroid, lengths scaled by half the
    diagonal so that all six columns are comparable) are tabulated at every constrained degree of freedom: row (node, direction d)
    is the displacement in direction d each motion produces at that node.  The body is held exactly when this matrix has rank 6.
    Its 6 x 6 Gram matrix is decomposed; an eigenvector whose eigenvalue is below ``RIGID_TOLERANCE**2`` of the largest is a motion
    nothing prevents, and is named by its components above 0.25.
    """
    centre = nodes.mean(axis=0)
    scale = max(float(np.linalg.norm(nodes.max(axis=0) - nodes.min(axis=0))) / 2.0, 1e-12)
    rows: list[np.ndarray] = []
    node_index, direction = np.nonzero(fixed)
    r = (nodes[node_index] - centre) / scale
    eye = np.eye(3)
    for k in range(len(node_index)):
        d = int(direction[k])
        rows.append(np.concatenate([eye[d], [np.cross(eye[a], r[k])[d] for a in range(3)]]))
    if not rows:
        matrix = np.zeros((1, 6))
    else:
        matrix = np.array(rows)
    values, vectors = np.linalg.eigh(matrix.T @ matrix)
    top = max(float(values.max()), 1e-300)
    free: list[str] = []
    for value, vector in zip(values, vectors.T):
        if value / top < RIGID_TOLERANCE**2:
            parts = [_MOTIONS[i] for i in np.argsort(-np.abs(vector)) if abs(vector[i]) > 0.25]
            free.append(" combined with ".join(parts) if parts else "an undetermined motion")
    return free


# --------------------------------------------------------------------------- the deck


def _wrap(values: list[int], per_line: int = 16) -> list[str]:
    return [", ".join(str(v) for v in values[i : i + per_line]) for i in range(0, len(values), per_line)]


def build_deck(mesh: TetMesh, young_mpa: float, poisson: float, fixed: np.ndarray, force_n: np.ndarray) -> str:
    """The ``.inp`` text: one static step, C3D10 elements, one isotropic material, boundary conditions and concentrated loads."""
    for name, number in (("youngs_modulus", young_mpa), ("poisson_ratio", poisson)):
        if not math.isfinite(number):
            raise ToolFailure("input.invalid", "a material constant is not finite", {"param": f"/{name}"})
    if young_mpa <= 0 or not -1 < poisson < .5:
        raise ToolFailure("input.invalid", "the material constants must give positive finite bulk and shear moduli", {"param": "/poisson_ratio"})
    if not (np.all(np.isfinite(mesh.nodes)) and np.all(np.isfinite(force_n))):
        raise ToolFailure("engine.failure", "a coordinate or a load is not finite; no deck was written", {})
    # CalculiX reads numeric fields in 20 columns. Shortest decimals preserve the
    # endpoints' neighbours; long exponents of tiny ratios can use 13 digits.
    poisson_text = repr(poisson)
    if len(poisson_text) > 20:
        poisson_text = f"{poisson:.12e}"
    if not -1 < float(poisson_text) < .5:
        raise ToolFailure("input.out_of_range", "the serialized Poisson ratio would give singular material constants", {"param": "/poisson_ratio"})
    lines = ["*HEADING", "eng-toolkit fem_static", "*NODE, NSET=NALL"]
    lines += [f"{i + 1}, {x:.12e}, {y:.12e}, {z:.12e}" for i, (x, y, z) in enumerate(mesh.nodes)]
    lines.append("*ELEMENT, TYPE=C3D10, ELSET=EALL")
    lines += [f"{i + 1}, " + ", ".join(str(int(v) + 1) for v in row) for i, row in enumerate(mesh.tets)]
    lines += ["*MATERIAL, NAME=MAT", "*ELASTIC", f"{young_mpa:.12e}, {poisson_text}", "*SOLID SECTION, ELSET=EALL, MATERIAL=MAT"]
    constrained = np.nonzero(fixed.any(axis=1))[0]
    lines.append("*NSET, NSET=NSUP")
    lines += _wrap([int(n) + 1 for n in constrained])
    lines.append("*BOUNDARY")
    for n in constrained:
        for d in range(3):
            if fixed[n, d]:
                lines.append(f"{int(n) + 1}, {d + 1}, {d + 1}, 0.")
    lines += ["*STEP", "*STATIC", "*CLOAD"]
    for n in np.nonzero(np.any(force_n != 0.0, axis=1))[0]:
        for d in range(3):
            if force_n[n, d] != 0.0:
                lines.append(f"{int(n) + 1}, {d + 1}, {force_n[n, d]:.12e}")
    lines += [
        "*NODE FILE",
        "U",
        "*EL FILE",
        "S",
        "*NODE PRINT, NSET=NALL",
        "U",
        "*NODE PRINT, NSET=NSUP, TOTALS=YES",
        "RF",
        "*END STEP",
    ]
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- reading the results


def _number(token: str) -> float:
    """A Fortran-printed real: ``1.0E+02`` or, when the exponent has three digits, ``1.000000-100``."""
    try:
        return float(token)
    except ValueError:
        match = re.fullmatch(r"([-+]?\d*\.?\d+)([-+]\d{3})", token)
        if not match:
            raise
        return float(f"{match[1]}e{match[2]}")


@dataclass(frozen=True)
class DatResults:
    displacement: np.ndarray  # (n, 3) mm
    reaction_nodes: np.ndarray  # 0-based node indices of the constrained nodes
    reaction: np.ndarray  # (k, 3) N, as CalculiX prints them (seven digits)
    reaction_total: np.ndarray  # (3,) N, summed by CalculiX before printing


def read_dat(path: Path, n_nodes: int) -> DatResults:
    text = path.read_text(encoding="ascii", errors="replace").splitlines()
    disp: dict[int, tuple[float, float, float]] = {}
    rf: dict[int, tuple[float, float, float]] = {}
    total: list[float] | None = None
    mode = None
    try:
        for line in text:
            low = line.strip().lower()
            if low.startswith("displacements (vx,vy,vz) for set nall"):
                mode = "disp"
            elif low.startswith("forces (fx,fy,fz) for set nsup"):
                mode = "rf"
            elif low.startswith("total force (fx,fy,fz) for set nsup"):
                mode = "total"
            elif not low:
                continue
            elif mode == "disp":
                p = line.split()
                disp[int(p[0])] = (_number(p[1]), _number(p[2]), _number(p[3]))
            elif mode == "rf":
                p = line.split()
                rf[int(p[0])] = (_number(p[1]), _number(p[2]), _number(p[3]))
            elif mode == "total":
                total = [_number(t) for t in line.split()[:3]]
                mode = None
    except (ValueError, IndexError) as exc:
        raise ToolFailure("engine.failure", f"CalculiX's .dat file could not be read ({exc})", {}) from exc
    if len(disp) != n_nodes or total is None or len(total) != 3:
        raise ToolFailure(
            "engine.failure",
            f"CalculiX's .dat file holds displacements of {len(disp)} of {n_nodes} nodes" + ("" if total else " and no reaction total"),
            {},
        )
    u = np.array([disp[i + 1] for i in range(n_nodes)])
    ids = np.array(sorted(rf), dtype=np.int64)
    return DatResults(u, ids - 1, np.array([rf[int(i)] for i in ids]).reshape(-1, 3), np.array(total))


def read_frd_stress(path: Path, n_nodes: int) -> np.ndarray:
    """Nodal stress ``(n, 6)`` (sxx, syy, szz, sxy, syz, szx), MPa, from the ``STRESS`` block of the ``.frd`` (fixed columns, E12.5)."""
    stress = np.full((n_nodes, 6), np.nan)
    inside = False
    seen = 0
    try:
        with path.open("r", encoding="ascii", errors="replace") as fh:
            for line in fh:
                if line.startswith(" -4  STRESS"):
                    inside = True
                    continue
                if not inside:
                    continue
                if line.startswith(" -1"):
                    node = int(line[3:13])
                    stress[node - 1] = [_number(line[13 + 12 * k : 25 + 12 * k]) for k in range(6)]
                    seen += 1
                elif line.startswith(" -3"):
                    break
    except (ValueError, IndexError) as exc:
        raise ToolFailure("engine.failure", f"CalculiX's .frd file could not be read ({exc})", {}) from exc
    if seen != n_nodes:
        raise ToolFailure("engine.failure", f"CalculiX's .frd file holds stresses at {seen} of {n_nodes} nodes", {})
    return stress


def von_mises(stress: np.ndarray) -> np.ndarray:
    sxx, syy, szz, sxy, syz, szx = (stress[:, k] for k in range(6))
    return np.hypot.reduce(np.stack([
        (sxx - syy) / math.sqrt(2), (syy - szz) / math.sqrt(2), (szz - sxx) / math.sqrt(2),
        math.sqrt(3) * sxy, math.sqrt(3) * syz, math.sqrt(3) * szx,
    ]), axis=0)


def check_result(name: str, array: np.ndarray, limit: float) -> None:
    """Check solver components and derived fields before they can reach artifacts."""
    if not np.all(np.isfinite(array)):
        raise ToolFailure("engine.failure", f"CalculiX returned a non-finite {name}; the result is discarded", {"param": "/loads"})
    if np.any(np.abs(array) > limit):
        raise ToolFailure("input.out_of_range", f"the computed {name} exceeds the supported magnitude {limit:g}; the result is discarded", {"param": "/loads", "max": limit})


# --------------------------------------------------------------------------- running it


@dataclass(frozen=True)
class SolveResult:
    displacement: np.ndarray
    stress: np.ndarray
    dat: DatResults
    record: ext.Record


_SINGULAR = re.compile(r"zero pivot|singular|not enough|rigid body|negative jacobian|nonpositive jacobian", re.IGNORECASE)


def solve(mesh: TetMesh, young_mpa: float, poisson: float, fixed: np.ndarray, force_n: np.ndarray, workdir: Path) -> SolveResult:
    """Write ``job.inp`` in ``workdir``, run CalculiX once and read ``job.dat`` and ``job.frd``."""
    (workdir / f"{JOB}.inp").write_text(build_deck(mesh, young_mpa, poisson, fixed, force_n), encoding="ascii", newline="\n")
    record = ext.run(CCX, ["-i", JOB], workdir=workdir, timeout_s=CCX_TIMEOUT_S, memory_mb=CCX_MEMORY_MB, max_output_bytes=CCX_MAX_OUTPUT_BYTES, check=False)
    said = f"{record.stdout_tail[-400:]} {record.stderr_tail[-400:]}"
    dat, frd = workdir / f"{JOB}.dat", workdir / f"{JOB}.frd"
    if record.returncode != 0 or not dat.is_file() or not frd.is_file():
        family = "fem.not_constrained" if _SINGULAR.search(said) else "engine.failure"
        tail = " ".join(part for part in (record.stdout_tail[-300:], record.stderr_tail[-200:]) if part)
        raise ToolFailure(
            family,
            f"CalculiX exited with code {record.returncode}" + (" and wrote no result" if not frd.is_file() else "") + (f": {tail}" if tail else ""),
            {"returncode": record.returncode, "stdout_tail": record.stdout_tail, "stderr_tail": record.stderr_tail},
        )
    results = read_dat(dat, len(mesh.nodes))
    stress = read_frd_stress(frd, len(mesh.nodes))
    for name, array, limit in (
        ("displacement (mm)", results.displacement, MAX_DISPLACEMENT_MM),
        ("stress (MPa)", stress, MAX_STRESS_MPA),
        ("nodal reaction (N)", results.reaction, MAX_REACTION_N),
        ("total reaction (N)", results.reaction_total, MAX_REACTION_N),
    ):
        check_result(name, array, limit)
    return SolveResult(results.displacement, stress, results, record)
