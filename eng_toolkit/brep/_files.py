"""Reading a STEP, IGES or BREP file into a ``Solid`` and tessellating it into a ``Mesh``."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from eng_toolkit.brep import _occ
from eng_toolkit.brep._types import Mesh, Solid
from eng_toolkit.errors import ToolFailure

ANGULAR_DEFLECTION_RAD = 0.5
SOLID_EXTENSIONS: tuple[str, ...] = (".step", ".stp", ".iges", ".igs", ".brep")


def _count(shape, kind) -> int:
    from OCP.TopExp import TopExp_Explorer

    explorer = TopExp_Explorer(shape, kind)
    found = 0
    while explorer.More():
        found += 1
        explorer.Next()
    return found


def _has_topology_header(path: Path) -> bool:
    with path.open("rb") as fh:
        head = fh.read(4096)
    return any(line.rstrip(b"\r").startswith(b"CASCADE Topology V") for line in head.split(b"\n"))


def _read_brep(path: Path, unreadable):
    from OCP.BRep import BRep_Builder
    from OCP.BRepTools import BRepTools
    from OCP.TopoDS import TopoDS_Shape

    # BRepTools.Read prints to std::cout, which the message printers do not cover, when the header line is
    # missing; stdout is the MCP channel, so a file without it never reaches OCCT.
    try:
        has_header = _has_topology_header(path)
    except OSError as exc:
        raise unreadable(f"cannot read BREP ({exc})") from exc
    if not has_header:
        raise unreadable("not a readable BREP file")
    shape = TopoDS_Shape()
    try:
        done = BRepTools.Read_s(shape, str(path), BRep_Builder())
    except Exception as exc:  # noqa: BLE001 - OCCT raises its own exception types
        raise unreadable(f"cannot read BREP ({exc})") from exc
    if not done or shape.IsNull():
        raise unreadable("not a readable BREP file")
    return shape


def read_solid(path: Path, *, param: str | None = None) -> Solid:
    """Read a STEP, IGES or BREP file (STEP and IGES in millimetres; BREP numbers are taken as millimetres).

    The format follows the extension: ``.iges``/``.igs`` and ``.brep`` as named, anything else is read as STEP.
    ``param`` names the caller's parameter in error messages.
    """
    _occ.load()
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.IGESControl import IGESControl_Reader
    from OCP.STEPControl import STEPControl_Reader
    from OCP.TopAbs import TopAbs_FACE, TopAbs_SOLID

    def unreadable(why: str) -> ToolFailure:
        prefix = f"{param}: " if param else ""
        return ToolFailure("input.invalid", f"{prefix}{why} in {str(path)!r}", {"path": str(path)})

    suffix = path.suffix.lower()
    if suffix == ".brep":
        shape = _read_brep(path, unreadable)
        roots_in_file = 1
        roots_transferred = 1 if _count(shape, TopAbs_FACE) > 0 else 0
    else:
        label, reader = ("IGES", IGESControl_Reader()) if suffix in (".iges", ".igs") else ("STEP", STEPControl_Reader())
        try:
            status = reader.ReadFile(str(path))
        except Exception as exc:  # noqa: BLE001 - OCCT raises its own exception types
            raise unreadable(f"cannot read {label} ({exc})") from exc
        if status != IFSelect_RetDone:
            raise unreadable(f"not a readable {label} file")
        roots_in_file = int(reader.NbRootsForTransfer())
        if reader.TransferRoots() == 0:
            raise unreadable("no shape to transfer")
        shape = reader.OneShape()
        if shape.IsNull():
            raise unreadable("no shape")
        # TransferRoots counts a root whose geometry failed to convert as transferred and hands back an empty shape for it.
        roots_transferred = sum(1 for i in range(1, reader.NbShapes() + 1) if _count(reader.Shape(i), TopAbs_FACE) > 0)
    return Solid(
        _handle=shape,
        solids=_count(shape, TopAbs_SOLID),
        faces=_count(shape, TopAbs_FACE),
        roots_in_file=roots_in_file,
        roots_transferred=roots_transferred,
    )


def triangulate(solid: Solid, deflection_mm: float) -> Mesh:
    """Tessellate every face with the given linear deflection; each face keeps its own nodes."""
    _occ.load()
    from eng_toolkit.brep._mesh_process import needs_isolation

    if needs_isolation(solid._handle):
        from eng_toolkit.brep._view import estimate_triangles, tessellate_faces
        from eng_toolkit.brep._mesh_process import MESH_TRIANGLES

        estimate = estimate_triangles(solid, deflection_mm).total
        if estimate > MESH_TRIANGLES:
            raise ToolFailure("input.out_of_range", "freeform mesh estimate exceeds the triangle budget; use a coarser mesh_deflection",
                              {"param": "/mesh_deflection", "estimated": True, "triangles": estimate, "limit": MESH_TRIANGLES})

        mesh = tessellate_faces(solid, deflection_mm, with_edges=False)
        return Mesh(vertices=mesh.vertices, triangles=mesh.triangles, deflection_mm=deflection_mm)
    from OCP.BRep import BRep_Tool
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    from OCP.TopAbs import TopAbs_FACE, TopAbs_REVERSED
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopLoc import TopLoc_Location
    from OCP.TopoDS import TopoDS

    shape = solid._handle
    BRepMesh_IncrementalMesh(shape, deflection_mm, False, ANGULAR_DEFLECTION_RAD, True)
    vertices: list[tuple[float, float, float]] = []
    triangles: list[tuple[int, int, int]] = []
    explorer = TopExp_Explorer(shape, TopAbs_FACE)
    while explorer.More():
        face = TopoDS.Face(explorer.Current())
        location = TopLoc_Location()
        triangulation = BRep_Tool.Triangulation_s(face, location)
        if triangulation is not None:
            transform = location.Transformation()
            base = len(vertices)
            for i in range(1, triangulation.NbNodes() + 1):
                p = triangulation.Node(i).Transformed(transform)
                vertices.append((p.X(), p.Y(), p.Z()))
            reversed_face = face.Orientation() == TopAbs_REVERSED
            for i in range(1, triangulation.NbTriangles() + 1):
                a, b, c = triangulation.Triangle(i).Get()
                if reversed_face:
                    b, c = c, b
                triangles.append((base + a - 1, base + b - 1, base + c - 1))
        explorer.Next()
    return Mesh(
        vertices=np.asarray(vertices, dtype=np.float64).reshape(-1, 3),
        triangles=np.asarray(triangles, dtype=np.int32).reshape(-1, 3),
        deflection_mm=deflection_mm,
    )
