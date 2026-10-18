from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass(frozen=True, eq=False)
class Solid:
    """A B-rep in millimetres.  ``_handle`` is the OCP ``TopoDS_Shape``; it never leaves this package."""

    _handle: object = field(repr=False)
    solids: int
    faces: int
    roots_in_file: int = 1  # root entities the STEP file offers for transfer
    roots_transferred: int = 1  # roots that produced at least one face


@dataclass(frozen=True, eq=False)
class Mesh:
    """Per-face triangles; the nodes of neighbouring faces are not shared (not welded)."""

    vertices: np.ndarray  # (n, 3) float64, millimetres
    triangles: np.ndarray  # (m, 3) int32, counter-clockwise seen from outside
    deflection_mm: float  # chord error bound the tessellation was asked for
