"""A tessellation that keeps face and edge identity, for coloured viewer files (``geometry_view``).

Faces are numbered as :func:`eng_toolkit.brep.inventory` numbers them (1-based ``TopExp_Explorer`` order) and edges as
the DFM facts number them (1-based index in ``TopExp.MapShapesAndAncestors``), so a face id or an edge id here names the
same entity as in ``geometry_measure`` and ``geometry_check_dfm`` for the same file bytes and OCCT version.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from eng_toolkit.brep import _occ
from eng_toolkit.brep._files import ANGULAR_DEFLECTION_RAD
from eng_toolkit.brep._types import Solid
from eng_toolkit.brep._query import _bbox, _integrated_properties

CURVATURE_SAMPLES = 5  # per parameter direction, for surfaces and curves whose curvature is not constant
# Calibrated against BRepMesh (OCCT 7.x, angular deflection 0.5 rad) on spheres, tori, cones, lofts and filleted boxes at
# 1, 0.1, 0.01 and 0.001 mm; see docs/geometry-view/design.md for the table.
CURVED_FACTOR = 6.6  # triangles per (h1 h2) of area on a sphere or torus: the mesher's triangles are ~3.3x smaller than h1 h2 / 2
CONE_FACTOR = 1.2  # the mesher treats a cone isotropically: about one triangle per h^2
FREEFORM_FACTOR = 5.0  # B-spline and other surfaces: isotropic like the cone, with smaller triangles
REVOLUTION_FACTOR = 2.5  # a surface of revolution (an ellipse revolved): isotropic


@dataclass(frozen=True, eq=False)
class EdgePolyline:
    id: int  # E<n>: same numbering as the DFM edge ids
    points_mm: np.ndarray  # (k, 3) float64, the nodes the face tessellation put on the edge, in order along it


@dataclass(frozen=True, eq=False)
class FaceMesh:
    """Per-face tessellation: face ``i`` (id ``i + 1``) owns vertices ``vertex_ranges[i]`` and triangles ``triangle_ranges[i]``.

    The nodes of neighbouring faces are not shared, so every face keeps its own vertices (and normals).  Triangles are
    counter-clockwise seen from outside.  ``seam_edges`` and ``degenerate_edges`` count edges that are not drawn: a seam
    closes a periodic face on itself (the line along a cylinder) and a degenerate edge has no length (a sphere's pole).
    """

    vertices: np.ndarray  # (n, 3) float64, millimetres
    triangles: np.ndarray  # (m, 3) int64, global vertex indices
    vertex_ranges: tuple[tuple[int, int], ...]  # (first, count) per face
    triangle_ranges: tuple[tuple[int, int], ...]
    edges: tuple[EdgePolyline, ...]
    seam_edges: int
    degenerate_edges: int
    edges_without_polygon: int  # edges with no tessellation polygon on any of their faces (not drawn)
    deflection_mm: float


@dataclass(frozen=True)
class Closure:
    """Whether a shape is made of closed solids only (see :func:`closure`)."""

    solids: int
    shells: int  # shells inside solids
    open_shells: int  # shells of solids with at least one free edge
    free_edges: int  # edges of a solid's shell used by only one face of that shell (seams and degenerate edges excluded)
    faces_outside_solids: int  # faces of the shape that belong to no solid (loose faces, free shells)
    loose_face_ids: tuple[int, ...] = ()  # F<n> (inventory numbering) of the faces that belong to no solid, ascending

    @property
    def closed(self) -> bool:
        """At least one solid, every shell of every solid closed, and no face outside the solids."""
        return self.solids > 0 and self.open_shells == 0 and self.faces_outside_solids == 0

    def problems(self) -> list[str]:
        """What keeps the shape from being closed solids only, in words (empty when :attr:`closed`, or with no solid)."""
        found = []
        if self.open_shells:
            found.append(f"{self.open_shells} of {self.shells} shells of its solids are open ({self.free_edges} free edges)")
        if self.solids and self.faces_outside_solids:
            found.append(f"{self.faces_outside_solids} faces lie outside any solid (loose faces or shells)")
        return found

    def summary(self, listed: int | None = None) -> dict[str, object]:
        """The closure as plain data for a tool result; ``loose_face_ids`` holds the first ``listed`` ids (all if None)."""
        return {
            "closed_solids_only": self.closed,
            "solids": self.solids,
            "shells": self.shells,
            "open_shells": self.open_shells,
            "free_edges": self.free_edges,
            "faces_outside_solids": self.faces_outside_solids,
            "loose_face_ids": list(self.loose_face_ids if listed is None else self.loose_face_ids[: max(0, listed)]),
        }


def closure(solid: Solid) -> Closure:
    """Every shell of every solid must be closed (no free edge) and every face must lie in a solid.

    A free edge is an edge that only one face of its shell uses; a seam (used twice by the same periodic face) and a
    degenerate edge (a sphere's pole) are not free.  ``mass_properties`` alone accepts a solid made of an open shell (it
    still integrates a volume) and a compound of a solid with loose faces beside it; this check does not.  This is the
    one closure check of the package: ``geometry_view`` and ``geometry_check_dfm`` refuse what it finds, ``geometry_measure``
    reports it.  ``loose_face_ids`` numbers faces as :func:`eng_toolkit.brep.inventory` does.
    """
    _occ.load()
    from OCP.BRep import BRep_Tool
    from OCP.collections import (
        IndexedDataMap_TopoDS_Shape_List_TopoDS_Shape_TopTools_ShapeMapHasher as EdgeToFaces,
    )
    from OCP.collections import IndexedMap_TopoDS_Shape_TopTools_ShapeMapHasher as ShapeMap
    from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_SHELL, TopAbs_SOLID
    from OCP.TopExp import TopExp, TopExp_Explorer
    from OCP.TopoDS import TopoDS

    shape = solid._handle
    every_face = ShapeMap()
    TopExp.MapShapes_s(shape, TopAbs_FACE, every_face)
    in_solids = ShapeMap()
    solids = shells = open_shells = free_edges = 0
    solid_explorer = TopExp_Explorer(shape, TopAbs_SOLID)
    while solid_explorer.More():
        solids += 1
        body = solid_explorer.Current()
        TopExp.MapShapes_s(body, TopAbs_FACE, in_solids)
        shell_explorer = TopExp_Explorer(body, TopAbs_SHELL)
        while shell_explorer.More():
            shells += 1
            edge_to_faces = EdgeToFaces()
            TopExp.MapShapesAndAncestors_s(shell_explorer.Current(), TopAbs_EDGE, TopAbs_FACE, edge_to_faces)
            free = 0
            for index in range(1, edge_to_faces.Extent() + 1):
                if BRep_Tool.Degenerated_s(TopoDS.Edge(edge_to_faces.FindKey(index))):
                    continue
                if edge_to_faces.FindFromIndex(index).Size() == 1:
                    free += 1
            free_edges += free
            open_shells += 1 if free else 0
            shell_explorer.Next()
        solid_explorer.Next()
    loose: list[int] = []
    face_explorer = TopExp_Explorer(shape, TopAbs_FACE)  # the inventory's order, so the ids are its F<n>
    face_id = 0
    while face_explorer.More():
        face_id += 1
        if not in_solids.Contains(face_explorer.Current()):
            loose.append(face_id)
        face_explorer.Next()
    return Closure(
        solids=solids,
        shells=shells,
        open_shells=open_shells,
        free_edges=free_edges,
        faces_outside_solids=int(every_face.Extent() - in_solids.Extent()),
        loose_face_ids=tuple(loose),
    )


@dataclass(frozen=True)
class TriangleEstimate:
    total: int
    per_face: tuple[int, ...]  # face id - 1 -> estimated triangles


def _angle_step(curvature: float, deflection_mm: float) -> float:
    """Angle one chord may span on a curve of this curvature: the chord error ``d`` gives ``2 acos(1 - d k)``, and the
    mesher's angular limit caps it at ``ANGULAR_DEFLECTION_RAD``."""
    x = 1.0 - deflection_mm * curvature
    return min(ANGULAR_DEFLECTION_RAD, 2.0 * math.acos(x) if x > -1.0 else math.pi)


def _curve_segments(edge, deflection_mm: float) -> int:
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.BRepLProp import BRepLProp_CLProps
    from OCP.GCPnts import GCPnts_AbscissaPoint
    from OCP.GeomAbs import GeomAbs_Circle, GeomAbs_Line

    curve = BRepAdaptor_Curve(edge)
    kind = curve.GetType()
    if kind == GeomAbs_Line:
        return 1
    length = float(GCPnts_AbscissaPoint.Length_s(curve))
    if not math.isfinite(length) or length <= 0.0:
        return 1
    if kind == GeomAbs_Circle:
        curvature = 1.0 / curve.Circle().Radius()
    else:
        first, last = curve.FirstParameter(), curve.LastParameter()
        curvature = 0.0
        for k in range(CURVATURE_SAMPLES):
            props = BRepLProp_CLProps(curve, first + (last - first) * (k + 0.5) / CURVATURE_SAMPLES, 2, 1e-9)
            if props.IsTangentDefined():
                curvature = max(curvature, abs(props.Curvature()))
    if curvature <= 0.0:
        return 1
    return max(1, math.ceil(length * curvature / _angle_step(curvature, deflection_mm)))


def _interior(face, surface, area: float, deflection_mm: float, *, isotropic: bool) -> float:
    """Sum over a grid of UV cells of (the cell's share of the face area) / (h1 h2), where h = chord length in each
    principal direction (``_angle_step(k) / k``).  A flat principal direction counts as ``sqrt(area)`` long, unless
    ``isotropic`` (both directions take the larger curvature, which is how the mesher treats a cone)."""
    from OCP.BRepLProp import BRepLProp_SLProps
    from OCP.BRepTools import BRepTools

    u0, u1, v0, v1 = BRepTools.UVBounds_s(face)
    weights: list[float] = []
    inverse_cells: list[float] = []
    flat = math.sqrt(area) if area > 0.0 else 1.0
    for i in range(CURVATURE_SAMPLES):
        for j in range(CURVATURE_SAMPLES):
            u = u0 + (u1 - u0) * (i + 0.5) / CURVATURE_SAMPLES
            v = v0 + (v1 - v0) * (j + 0.5) / CURVATURE_SAMPLES
            props = BRepLProp_SLProps(surface, u, v, 2, 1e-9)
            if not props.IsCurvatureDefined():
                continue
            density = props.D1U().Crossed(props.D1V()).Magnitude()
            if not (math.isfinite(density) and density > 0.0):
                continue
            k1, k2 = sorted((abs(props.MaxCurvature()), abs(props.MinCurvature())), reverse=True)
            if isotropic:
                k2 = k1
            h1 = _angle_step(k1, deflection_mm) / k1 if k1 > 1e-12 else flat
            h2 = _angle_step(k2, deflection_mm) / k2 if k2 > 1e-12 else flat
            weights.append(density)
            inverse_cells.append(1.0 / (min(h1, flat) * min(h2, flat)))
    if not weights:
        return 0.0
    total = math.fsum(weights)
    return area * math.fsum(w * c for w, c in zip(weights, inverse_cells)) / total


def estimate_triangles(solid: Solid, deflection_mm: float, *, deadline: Callable[[str], None] | None = None) -> TriangleEstimate:
    """How many triangles :func:`tessellate_faces` will make, estimated from the exact geometry before any meshing.

    Per face: one triangle per boundary segment (an edge of curvature ``k`` is cut into chords spanning
    ``_angle_step(k, d)``, a straight edge into one), plus, for a curved face, the interior: a cylinder or cone (curved in
    one direction only) gets two triangles per circumferential step over its angular span; any other curved face gets
    ``2 A / (h1 h2)``, where ``h = step / k`` is the chord length in each principal direction (the flat direction of a
    singly curved free-form face is taken as ``sqrt(A)`` long). Spline faces also reserve work for every knot span,
    polynomial degree and rational weight ratio. This is a conservative preflight estimate, not a mathematical bound
    on OCCT's triangulator. Freeform meshes also run in a process with time and memory limits.
    """
    _occ.load()
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import (
        GeomAbs_BezierSurface,
        GeomAbs_BSplineSurface,
        GeomAbs_Cone,
        GeomAbs_Cylinder,
        GeomAbs_Plane,
        GeomAbs_Sphere,
        GeomAbs_SurfaceOfRevolution,
        GeomAbs_Torus,
    )
    from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    per_face: list[int] = []
    explorer = TopExp_Explorer(solid._handle, TopAbs_FACE)
    while explorer.More():
        if deadline is not None:
            deadline("estimate")
        face = TopoDS.Face(explorer.Current())
        boundary = 0
        edges = TopExp_Explorer(face, TopAbs_EDGE)
        while edges.More():
            boundary += _curve_segments(TopoDS.Edge(edges.Current()), deflection_mm)
            edges.Next()
        surface = BRepAdaptor_Surface(face)
        kind = surface.GetType()
        if kind == GeomAbs_Plane:
            estimate = boundary - 2.0  # a polygon of n nodes is n - 2 triangles (holes add a few)
        elif kind == GeomAbs_Cylinder:
            curvature = 1.0 / surface.Cylinder().Radius()
            span = surface.LastUParameter() - surface.FirstUParameter()
            estimate = boundary + 2.0 * math.ceil(span / _angle_step(curvature, deflection_mm))  # one strip along the axis
        else:
            props = _integrated_properties(face)
            area = abs(float(props.Mass()))
            if kind in (GeomAbs_Sphere, GeomAbs_Torus):
                estimate = boundary + CURVED_FACTOR * _interior(face, surface, area, deflection_mm, isotropic=False)
            elif kind == GeomAbs_Cone:
                estimate = boundary + CONE_FACTOR * _interior(face, surface, area, deflection_mm, isotropic=True)
            elif kind == GeomAbs_SurfaceOfRevolution:
                estimate = boundary + REVOLUTION_FACTOR * _interior(face, surface, area, deflection_mm, isotropic=True)
            else:  # B-spline, Bezier, revolution, extrusion, offset: meshed isotropically
                estimate = boundary + FREEFORM_FACTOR * _interior(face, surface, area, deflection_mm, isotropic=True)
                if kind in (GeomAbs_BSplineSurface, GeomAbs_BezierSurface):
                    spline = surface.BSpline() if kind == GeomAbs_BSplineSurface else surface.Bezier()
                    spans = (spline.NbUKnots() - 1) * (spline.NbVKnots() - 1) if kind == GeomAbs_BSplineSurface else 1
                    weights = [spline.Weight(i, j) for i in range(1, spline.NbUPoles() + 1) for j in range(1, spline.NbVPoles() + 1)]
                    weight_ratio = max(weights) / min(weights)
                    size = math.dist(*_bbox(face))
                    complexity = spans * (spline.UDegree() + 1) * (spline.VDegree() + 1) * weight_ratio
                    estimate = max(estimate, boundary + complexity * max(1.0, size / (16 * deflection_mm)))
        per_face.append(int(max(2.0, min(estimate, 1e15))))
        explorer.Next()
    return TriangleEstimate(total=int(sum(per_face)), per_face=tuple(per_face))


def tessellate_faces(
    solid: Solid,
    deflection_mm: float,
    *,
    with_edges: bool = True,
    deadline: Callable[[str], None] | None = None,
    after_face: Callable[[int], None] | None = None,
) -> FaceMesh:
    """Tessellate every face anew (any triangulation already on the shape is removed first, so the result depends only on
    ``deflection_mm``) and, with ``with_edges``, collect each edge's polyline from the tessellation of one of its faces.

    The faces are meshed one at a time (serially, so the result is deterministic): ``deadline("mesh")`` is called before
    each face and ``after_face(triangles so far)`` after it, so a time or size budget stops the work within one face's
    meshing; ``deadline("edges")`` is called between edges.  A single OpenCASCADE call cannot be interrupted.  Edges
    shared by two faces are discretised once and reused, so the faces still meet without cracks.
    """
    _occ.load()
    from OCP.BRep import BRep_Tool
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    from OCP.BRepTools import BRepTools
    from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_REVERSED
    from OCP.TopExp import TopExp, TopExp_Explorer
    from OCP.TopLoc import TopLoc_Location
    from OCP.TopoDS import TopoDS

    shape = solid._handle
    from eng_toolkit.brep._mesh_process import needs_isolation, isolated_mesh

    isolated = needs_isolation(shape)
    if isolated:
        shape = isolated_mesh(shape, deflection_mm, deadline=deadline)
    else:
        BRepTools.Clean_s(shape)
    vertex_blocks: list[np.ndarray] = []
    triangle_blocks: list[np.ndarray] = []
    vertex_ranges: list[tuple[int, int]] = []
    triangle_ranges: list[tuple[int, int]] = []
    vertex_count = triangle_count = 0
    explorer = TopExp_Explorer(shape, TopAbs_FACE)
    while explorer.More():
        if deadline is not None:
            deadline("mesh")
        face = TopoDS.Face(explorer.Current())
        if not isolated:
            BRepMesh_IncrementalMesh(face, deflection_mm, False, ANGULAR_DEFLECTION_RAD, False)
        location = TopLoc_Location()
        triangulation = BRep_Tool.Triangulation_s(face, location)
        nodes = np.empty((0, 3))
        corners = np.empty((0, 3), dtype=np.int64)
        if triangulation is not None and triangulation.NbTriangles() > 0:
            transform = location.Transformation()
            points = [triangulation.Node(i).Transformed(transform) for i in range(1, triangulation.NbNodes() + 1)]
            nodes = np.array([(p.X(), p.Y(), p.Z()) for p in points], dtype=np.float64).reshape(-1, 3)
            corners = np.array([triangulation.Triangle(i).Get() for i in range(1, triangulation.NbTriangles() + 1)], dtype=np.int64) - 1
            if face.Orientation() == TopAbs_REVERSED:
                corners = corners[:, [0, 2, 1]]
        vertex_ranges.append((vertex_count, len(nodes)))
        triangle_ranges.append((triangle_count, len(corners)))
        vertex_blocks.append(nodes)
        triangle_blocks.append(corners + vertex_count)
        vertex_count += len(nodes)
        triangle_count += len(corners)
        if after_face is not None:
            after_face(triangle_count)
        explorer.Next()

    edges: list[EdgePolyline] = []
    seams = degenerate = missing = 0
    if with_edges:
        from OCP.collections import (
            IndexedDataMap_TopoDS_Shape_List_TopoDS_Shape_TopTools_ShapeMapHasher as EdgeToFaces,
        )

        edge_to_faces = EdgeToFaces()
        TopExp.MapShapesAndAncestors_s(shape, TopAbs_EDGE, TopAbs_FACE, edge_to_faces)
        for index in range(1, edge_to_faces.Extent() + 1):
            if deadline is not None:
                deadline("edges")
            edge = TopoDS.Edge(edge_to_faces.FindKey(index))
            if BRep_Tool.Degenerated_s(edge):
                degenerate += 1
                continue
            faces = [TopoDS.Face(f) for f in edge_to_faces.FindFromIndex(index)]
            if faces and all(BRep_Tool.IsClosed_s(edge, f) for f in faces):
                seams += 1
                continue
            points = None
            for face in faces:
                location = TopLoc_Location()
                triangulation = BRep_Tool.Triangulation_s(face, location)
                if triangulation is None:
                    continue
                polygon = BRep_Tool.PolygonOnTriangulation_s(edge, triangulation, location)
                if polygon is None or polygon.NbNodes() < 2:
                    continue
                transform = location.Transformation()
                picked = [triangulation.Node(polygon.Node(k)).Transformed(transform) for k in range(1, polygon.NbNodes() + 1)]
                points = np.array([(p.X(), p.Y(), p.Z()) for p in picked], dtype=np.float64)
                break
            if points is None:
                missing += 1
                continue
            edges.append(EdgePolyline(id=index, points_mm=points))

    return FaceMesh(
        vertices=np.concatenate(vertex_blocks) if vertex_blocks else np.empty((0, 3)),
        triangles=np.concatenate(triangle_blocks) if triangle_blocks else np.empty((0, 3), dtype=np.int64),
        vertex_ranges=tuple(vertex_ranges),
        triangle_ranges=tuple(triangle_ranges),
        edges=tuple(edges),
        seam_edges=seams,
        degenerate_edges=degenerate,
        edges_without_polygon=missing,
        deflection_mm=deflection_mm,
    )
