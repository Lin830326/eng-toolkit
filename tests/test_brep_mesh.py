"""brep.weld and brep.mesh_topology: hand-calculated meshes, and parity with the Open3D path they replace."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np
import pytest

from eng_toolkit import brep

try:
    import OCP  # noqa: F401

    HAVE_OCP = True
except (ImportError, OSError):
    HAVE_OCP = False

try:
    import open3d  # noqa: F401

    HAVE_OPEN3D = True
except (ImportError, OSError):
    HAVE_OPEN3D = False

needs_ocp = pytest.mark.skipif(not HAVE_OCP, reason="the OCP engine is not installed; run: uv sync --extra cad")
needs_open3d = pytest.mark.skipif(not HAVE_OPEN3D, reason="Open3D is not installed; run: uv sync --extra scan")
needs_both = pytest.mark.skipif(
    not (HAVE_OCP and HAVE_OPEN3D), reason="OCP and Open3D are both needed; run: uv sync --extra scan"
)

OLD_MERGE_EPS_MM = 1e-6


def make_mesh(vertices, triangles, deflection_mm: float = 0.0) -> brep.Mesh:
    return brep.Mesh(
        vertices=np.asarray(vertices, dtype=np.float64).reshape(-1, 3),
        triangles=np.asarray(triangles, dtype=np.int32).reshape(-1, 3),
        deflection_mm=deflection_mm,
    )


def signed_volume(mesh: brep.Mesh) -> float:
    corners = mesh.vertices[mesh.triangles]
    return float(np.einsum("ij,ij->i", corners[:, 0], np.cross(corners[:, 1], corners[:, 2])).sum() / 6.0)


def unweld(mesh: brep.Mesh) -> brep.Mesh:
    """Give every triangle its own three vertices, as a face-by-face tessellation does."""
    corners = mesh.vertices[mesh.triangles].reshape(-1, 3)
    return make_mesh(corners, np.arange(len(corners)).reshape(-1, 3), mesh.deflection_mm)


def cube_sphere(n: int, radius: float = 1.0) -> brep.Mesh:
    """A sphere from six n-by-n grids, each face with its own nodes, outward winding: 12 n^2 triangles, 6 n^2 + 2 welded nodes."""
    axes = {  # normal, u axis, v axis, with u x v = normal
        "+x": ((1, 0, 0), (0, 1, 0), (0, 0, 1)),
        "-x": ((-1, 0, 0), (0, 0, 1), (0, 1, 0)),
        "+y": ((0, 1, 0), (0, 0, 1), (1, 0, 0)),
        "-y": ((0, -1, 0), (1, 0, 0), (0, 0, 1)),
        "+z": ((0, 0, 1), (1, 0, 0), (0, 1, 0)),
        "-z": ((0, 0, -1), (0, 1, 0), (1, 0, 0)),
    }
    grid = np.linspace(-1.0, 1.0, n + 1)
    u, v = np.meshgrid(grid, grid, indexing="ij")
    index = np.arange((n + 1) ** 2).reshape(n + 1, n + 1)
    p00, p10, p11, p01 = index[:-1, :-1], index[1:, :-1], index[1:, 1:], index[:-1, 1:]
    quad_triangles = np.concatenate(
        [np.stack([p00, p10, p11], -1).reshape(-1, 3), np.stack([p00, p11, p01], -1).reshape(-1, 3)]
    )
    vertices, triangles = [], []
    for normal, a, b in axes.values():
        cube = np.array(normal) + u[..., None] * np.array(a) + v[..., None] * np.array(b)
        sphere = radius * cube / np.linalg.norm(cube, axis=-1, keepdims=True)
        triangles.append(quad_triangles + sum(len(x) for x in vertices))
        vertices.append(sphere.reshape(-1, 3))
    return make_mesh(np.concatenate(vertices), np.concatenate(triangles))


# --------------------------------------------------------------------------- hand-built meshes (no engines)

TETRA_VERTICES = [(0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 1)]
TETRA_TRIANGLES = [(0, 2, 1), (0, 1, 3), (0, 3, 2), (1, 2, 3)]  # outward: normals -z, -y, -x, (1,1,1)


def tetrahedron() -> brep.Mesh:
    return make_mesh(TETRA_VERTICES, TETRA_TRIANGLES)


def octahedron_by_faces() -> brep.Mesh:
    """Unit octahedron, one triangle per octant with its own three nodes: 24 nodes that weld to 6."""
    corners, triangles = [], []
    for sx in (1, -1):
        for sy in (1, -1):
            for sz in (1, -1):
                x, y, z = (sx, 0, 0), (0, sy, 0), (0, 0, sz)
                triangle = (x, y, z) if sx * sy * sz > 0 else (x, z, y)
                triangles.append((len(corners), len(corners) + 1, len(corners) + 2))
                corners.extend(triangle)
    return make_mesh(corners, triangles)


def test_a_tetrahedron_is_closed_oriented_and_encloses_one_sixth():
    mesh = tetrahedron()
    assert signed_volume(mesh) == pytest.approx(1 / 6, abs=1e-15)
    welded = brep.weld(mesh)
    assert (len(welded.vertices), len(welded.triangles)) == (4, 4)
    topology = brep.mesh_topology(welded)
    assert (topology.edge_manifold_closed, topology.vertex_manifold, topology.watertight) == (True, True, True)


def test_flipping_one_triangle_breaks_the_orientation_not_the_vertex_fans():
    triangles = [list(t) for t in TETRA_TRIANGLES]
    triangles[3] = [1, 3, 2]
    topology = brep.mesh_topology(make_mesh(TETRA_VERTICES, triangles))
    assert topology.edge_manifold_closed is False
    assert topology.vertex_manifold is True
    assert topology.watertight is False


def test_an_octahedron_welds_from_24_nodes_to_6():
    mesh = octahedron_by_faces()
    assert (len(mesh.vertices), len(mesh.triangles)) == (24, 8)
    welded = brep.weld(mesh)
    assert (len(welded.vertices), len(welded.triangles)) == (6, 8)
    assert brep.mesh_topology(welded).watertight is True
    assert signed_volume(welded) == pytest.approx(4 / 3, abs=1e-12)
    assert brep.mesh_topology(mesh).edge_manifold_closed is False  # unwelded, every edge is a boundary


def test_welding_keeps_the_coordinates_of_the_first_vertex_of_each_position_and_their_order():
    mesh = make_mesh(
        [(5, 0, 0), (0, 0, 0), (0, 1, 0), (5 + 4e-7, 0, 0), (0, 0, 1)],
        [(0, 1, 2), (3, 2, 4)],
    )
    welded = brep.weld(mesh)
    np.testing.assert_array_equal(welded.vertices, [(5, 0, 0), (0, 0, 0), (0, 1, 0), (0, 0, 1)])
    np.testing.assert_array_equal(welded.triangles, [(0, 1, 2), (0, 2, 3)])
    assert welded.deflection_mm == mesh.deflection_mm


def test_two_tetrahedra_sharing_one_vertex_are_not_watertight():
    second = [(x + 1, y, z) for x, y, z in TETRA_VERTICES]  # its first corner lands on (1, 0, 0)
    vertices = [*TETRA_VERTICES, *second]
    triangles = [*TETRA_TRIANGLES, *[(a + 4, b + 4, c + 4) for a, b, c in TETRA_TRIANGLES]]
    welded = brep.weld(make_mesh(vertices, triangles))
    assert (len(welded.vertices), len(welded.triangles)) == (7, 8)
    topology = brep.mesh_topology(welded)
    assert topology.edge_manifold_closed is True
    assert topology.vertex_manifold is False
    assert topology.watertight is False


def test_two_disjoint_tetrahedra_are_watertight():
    second = [(x + 5, y, z) for x, y, z in TETRA_VERTICES]
    triangles = [*TETRA_TRIANGLES, *[(a + 4, b + 4, c + 4) for a, b, c in TETRA_TRIANGLES]]
    assert brep.mesh_topology(brep.weld(make_mesh([*TETRA_VERTICES, *second], triangles))).watertight is True


def _straddling_tetrahedron(gap: float) -> brep.Mesh:
    """Unit tetrahedron, one triangle per face, whose first corner sits at x = 0.4e-6 on two faces and ``gap`` further on the third.

    The weld grid has cell boundaries at odd multiples of half the tolerance, so 0.5e-6 lies between the two copies.
    """
    near, far = (0.4e-6, 0.0, 0.0), (0.4e-6 + gap, 0.0, 0.0)
    vertices = []
    for k, face in enumerate(TETRA_TRIANGLES):
        for index in face:
            vertices.append((near if k < 2 else far) if index == 0 else TETRA_VERTICES[index])
    return make_mesh(vertices, np.arange(12).reshape(4, 3))


def test_a_node_pair_across_a_grid_cell_boundary_still_welds():
    mesh = _straddling_tetrahedron(gap=0.4999999e-6)
    welded = brep.weld(mesh)
    assert len(welded.vertices) == 4
    assert brep.mesh_topology(welded).watertight is True


def test_a_node_pair_further_apart_than_the_tolerance_stays_two_nodes():
    welded = brep.weld(_straddling_tetrahedron(gap=2e-6))
    assert len(welded.vertices) == 5
    assert brep.mesh_topology(welded).edge_manifold_closed is False


def test_the_tolerance_is_a_parameter():
    mesh = _straddling_tetrahedron(gap=2e-6)
    assert len(brep.weld(mesh, eps_mm=1e-5).vertices) == 4


def test_degenerate_triangles_and_unreferenced_vertices_are_dropped():
    vertices = [*TETRA_VERTICES, (9, 9, 9), (1e-7, 0, 0)]  # vertex 4 is never used; vertex 5 welds onto vertex 0
    welded = brep.weld(make_mesh(vertices, [*TETRA_TRIANGLES, (0, 5, 0)]))
    assert (len(welded.vertices), len(welded.triangles)) == (4, 4)
    assert welded.triangles.max() == 3


def test_a_triangle_that_collapses_to_an_edge_after_welding_is_dropped():
    vertices = [*TETRA_VERTICES, (1 + 3e-7, 0, 0)]
    welded = brep.weld(make_mesh(vertices, [*TETRA_TRIANGLES, (1, 4, 2)]))
    assert (len(welded.vertices), len(welded.triangles)) == (4, 4)
    assert brep.mesh_topology(welded).watertight is True


def test_welding_twice_changes_nothing_and_repeats_exactly():
    once = brep.weld(octahedron_by_faces())
    again = brep.weld(octahedron_by_faces())
    twice = brep.weld(once)
    for other in (again, twice):
        np.testing.assert_array_equal(once.vertices, other.vertices)
        np.testing.assert_array_equal(once.triangles, other.triangles)
    assert once.triangles.dtype == np.int32
    assert once.vertices.dtype == np.float64


def test_an_empty_mesh_welds_to_an_empty_mesh_and_is_not_watertight():
    empty = make_mesh(np.empty((0, 3)), np.empty((0, 3)))
    welded = brep.weld(empty)
    assert (welded.vertices.shape, welded.triangles.shape) == ((0, 3), (0, 3))
    topology = brep.mesh_topology(welded)
    assert (topology.edge_manifold_closed, topology.vertex_manifold, topology.watertight) == (False, False, False)


def test_non_finite_coordinates_are_rejected():
    with pytest.raises(ValueError, match="finite"):
        brep.weld(make_mesh([(0, 0, 0), (1, 0, 0), (np.nan, 1, 0)], [(0, 1, 2)]))


def test_a_single_triangle_is_open_but_its_vertex_is_a_single_fan():
    topology = brep.mesh_topology(make_mesh([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [(0, 1, 2)]))
    assert (topology.edge_manifold_closed, topology.vertex_manifold) == (False, True)


def test_two_triangles_meeting_at_one_vertex_are_a_bowtie_not_a_fan():
    vertices = [(0, 0, 0), (1, 0, 0), (0, 1, 0), (-1, 0, 0), (0, -1, 0)]
    topology = brep.mesh_topology(make_mesh(vertices, [(0, 1, 2), (0, 3, 4)]))
    assert topology.vertex_manifold is False


def test_an_edge_shared_by_three_triangles_is_neither_closed_nor_vertex_manifold():
    vertices = [(0, 0, 0), (1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1)]
    topology = brep.mesh_topology(make_mesh(vertices, [(0, 1, 2), (1, 0, 3), (0, 1, 4)]))
    assert (topology.edge_manifold_closed, topology.vertex_manifold) == (False, False)


def test_the_same_triangle_twice_is_not_closed():
    mesh = make_mesh([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [(0, 1, 2), (0, 1, 2)])
    assert brep.mesh_topology(mesh).edge_manifold_closed is False


def test_a_triangle_with_a_repeated_index_is_not_closed():
    mesh = make_mesh(TETRA_VERTICES, [*TETRA_TRIANGLES, (0, 0, 1)])
    topology = brep.mesh_topology(mesh)
    assert (topology.edge_manifold_closed, topology.vertex_manifold) == (False, False)


@pytest.mark.parametrize("n", [3, 40])
def test_a_cube_sphere_welds_to_6n2_plus_2_nodes_and_is_watertight(n):
    mesh = cube_sphere(n)
    assert (len(mesh.vertices), len(mesh.triangles)) == (6 * (n + 1) ** 2, 12 * n * n)
    welded = brep.weld(mesh)
    assert (len(welded.vertices), len(welded.triangles)) == (6 * n * n + 2, 12 * n * n)
    assert brep.mesh_topology(welded).watertight is True
    exact = 4 / 3 * np.pi
    assert 0 < signed_volume(welded) < exact
    if n == 40:
        assert signed_volume(welded) > 0.99 * exact


def test_removing_one_triangle_of_a_welded_cube_sphere_opens_it():
    welded = brep.weld(cube_sphere(6))
    opened = make_mesh(welded.vertices, welded.triangles[1:])
    topology = brep.mesh_topology(opened)
    assert (topology.edge_manifold_closed, topology.watertight) == (False, False)


def test_a_welded_mesh_in_random_rigid_motion_keeps_its_topology():
    rng = np.random.default_rng(7)
    mesh = octahedron_by_faces()
    for _ in range(20):
        q, _r = np.linalg.qr(rng.normal(size=(3, 3)))
        if np.linalg.det(q) < 0:
            q[:, 0] *= -1
        moved = make_mesh(mesh.vertices @ q.T + rng.uniform(-5000, 5000, 3), mesh.triangles)
        welded = brep.weld(moved)
        assert (len(welded.vertices), len(welded.triangles)) == (6, 8)
        assert brep.mesh_topology(welded).watertight is True


# --------------------------------------------------------------------------- Open3D parity on the open-mesh flags


@needs_open3d
@pytest.mark.parametrize(
    "name",
    ["tetrahedron", "single_triangle", "bowtie", "two_tetrahedra_one_vertex", "open_cube_sphere", "flipped_tetrahedron"],
)
def test_vertex_manifold_flag_agrees_with_open3d_on_hand_built_meshes(name):
    import open3d as o3d

    second = [(x + 1, y, z) for x, y, z in TETRA_VERTICES]
    welded_cube_sphere = brep.weld(cube_sphere(5))
    meshes = {
        "tetrahedron": tetrahedron(),
        "single_triangle": make_mesh([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [(0, 1, 2)]),
        "bowtie": make_mesh([(0, 0, 0), (1, 0, 0), (0, 1, 0), (-1, 0, 0), (0, -1, 0)], [(0, 1, 2), (0, 3, 4)]),
        "two_tetrahedra_one_vertex": brep.weld(
            make_mesh(
                [*TETRA_VERTICES, *second],
                [*TETRA_TRIANGLES, *[(a + 4, b + 4, c + 4) for a, b, c in TETRA_TRIANGLES]],
            )
        ),
        "open_cube_sphere": make_mesh(welded_cube_sphere.vertices, welded_cube_sphere.triangles[3:]),
        "flipped_tetrahedron": make_mesh(TETRA_VERTICES, [*TETRA_TRIANGLES[:3], (1, 3, 2)]),
    }
    mesh = meshes[name]
    reference = o3d.geometry.TriangleMesh(o3d.utility.Vector3dVector(mesh.vertices), o3d.utility.Vector3iVector(mesh.triangles))
    assert brep.mesh_topology(mesh).vertex_manifold == reference.is_vertex_manifold()


# --------------------------------------------------------------------------- parity with the Open3D path (needs OCP and Open3D)


@dataclass(frozen=True)
class Measured:
    vertices: int
    triangles: int
    watertight: bool
    volume: float


def open3d_path(raw: brep.Mesh) -> Measured:
    """What ``scan.read_model_mesh`` did before this slice."""
    import open3d as o3d

    mesh = o3d.geometry.TriangleMesh(o3d.utility.Vector3dVector(raw.vertices), o3d.utility.Vector3iVector(raw.triangles))
    mesh = mesh.merge_close_vertices(OLD_MERGE_EPS_MM)
    mesh.remove_degenerate_triangles()
    mesh.remove_unreferenced_vertices()
    watertight = bool(mesh.is_edge_manifold(allow_boundary_edges=False) and mesh.is_vertex_manifold())
    vertices = np.asarray(mesh.vertices, dtype=np.float64)
    triangles = np.asarray(mesh.triangles, dtype=np.int32)
    return Measured(len(vertices), len(triangles), watertight, signed_volume(brep.Mesh(vertices, triangles, 0.0)))


def numpy_path(raw: brep.Mesh) -> Measured:
    welded = brep.weld(raw)
    return Measured(
        len(welded.vertices), len(welded.triangles), brep.mesh_topology(welded).watertight, signed_volume(welded)
    )


def _write_step(shape, path: Path) -> Path:
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer

    writer = STEPControl_Writer()
    writer.Transfer(shape, STEPControl_AsIs)
    assert writer.Write(str(path)) == IFSelect_RetDone
    return path


def _box(x=0.0, y=0.0, z=0.0, length=10.0, width=10.0, height=10.0):
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.gp import gp_Pnt

    return BRepPrimAPI_MakeBox(gp_Pnt(x, y, z), length, width, height).Shape()


def _compound(*shapes):
    from OCP.BRep import BRep_Builder
    from OCP.TopoDS import TopoDS_Compound

    builder = BRep_Builder()
    compound = TopoDS_Compound()
    builder.MakeCompound(compound)
    for shape in shapes:
        builder.Add(compound, shape)
    return compound


def _faces(shape) -> list:
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    explorer = TopExp_Explorer(shape, TopAbs_FACE)
    found = []
    while explorer.More():
        found.append(TopoDS.Face(explorer.Current()))
        explorer.Next()
    return found


def _cylinder(radius=10.0, height=30.0, x=0.0, y=0.0, z=0.0):
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
    from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

    return BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(x, y, z), gp_Dir(0, 0, 1)), radius, height).Shape()


def _cut(shape, tool):
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut

    return BRepAlgoAPI_Cut(shape, tool).Shape()


def _sphere():
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeSphere

    return BRepPrimAPI_MakeSphere(10.0).Shape()


def _torus():
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeTorus

    return BRepPrimAPI_MakeTorus(20.0, 5.0).Shape()


@dataclass(frozen=True)
class Case:
    build: Callable[[], object]
    deflection_mm: float
    watertight: bool
    counts: tuple[int, int] | None = None  # (vertices, triangles) worked out by hand where the tessellation is forced


CASES: dict[str, Case] = {
    "box": Case(lambda: _box(0, 0, 0, 100, 50, 20), 0.01, True, (8, 12)),
    "cylinder": Case(lambda: _cylinder(), 0.001, True),
    "sphere": Case(_sphere, 0.01, True),
    "torus": Case(_torus, 0.01, True),
    "box_through_hole": Case(lambda: _cut(_box(0, 0, 0, 100, 50, 20), _cylinder(5.0, 30.0, 50.0, 25.0, -5.0)), 0.01, True),
    "box_internal_void": Case(lambda: _cut(_box(0, 0, 0, 100, 50, 20), _box(20, 10, 5, 30, 20, 10)), 0.01, True, (16, 24)),
    "two_disjoint_boxes": Case(lambda: _compound(_box(), _box(30, 0, 0)), 0.01, True, (16, 24)),
    "two_boxes_touching_along_an_edge": Case(lambda: _compound(_box(), _box(10, 10, 0)), 0.01, False, (14, 24)),
    "two_boxes_touching_at_one_vertex": Case(lambda: _compound(_box(), _box(10, 10, 10)), 0.01, False, (15, 24)),
    "open_shell_single_face": Case(lambda: _compound(_faces(_box())[0]), 0.01, False, (4, 2)),
    "box_missing_one_face": Case(lambda: _compound(*_faces(_box())[1:]), 0.01, False, (8, 10)),
}

TRANSFORM_COUNT = 20


def _random_rigid_motions() -> list[tuple[tuple[float, float, float], float, tuple[float, float, float]]]:
    rng = np.random.default_rng(20241018)
    motions = []
    for i in range(TRANSFORM_COUNT):
        axis = rng.normal(size=3)
        axis /= np.linalg.norm(axis)
        angle = float(rng.uniform(0.0, 2.0 * np.pi))
        shift = (5000.0, -5000.0, 5000.0) if i == 0 else tuple(float(v) for v in rng.uniform(-5000.0, 5000.0, 3))
        motions.append((tuple(float(a) for a in axis), angle, shift))
    return motions


MOTIONS = _random_rigid_motions()


def _moved(shape, motion):
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
    from OCP.gp import gp_Ax1, gp_Dir, gp_Pnt, gp_Trsf, gp_Vec

    axis, angle, shift = motion
    rotation = gp_Trsf()
    rotation.SetRotation(gp_Ax1(gp_Pnt(0, 0, 0), gp_Dir(*axis)), angle)
    translation = gp_Trsf()
    translation.SetTranslation(gp_Vec(*shift))
    return BRepBuilderAPI_Transform(shape, translation.Multiplied(rotation), True).Shape()


def raw_mesh(shape, deflection_mm: float, directory: Path) -> brep.Mesh:
    path = _write_step(shape, directory / "shape.step")
    return brep.triangulate(brep.read_solid(path), deflection_mm)


def assert_paths_agree(label: str, raw: brep.Mesh) -> tuple[Measured, Measured]:
    old, new = open3d_path(raw), numpy_path(raw)
    assert (new.vertices, new.triangles, new.watertight) == (old.vertices, old.triangles, old.watertight), (
        f"{label}: Open3D {old} but NumPy {new}"
    )
    assert new.volume == pytest.approx(old.volume, rel=1e-6, abs=1e-6), f"{label}: volume Open3D {old} NumPy {new}"
    return old, new


@needs_both
@pytest.mark.parametrize("name", CASES)
def test_numpy_weld_matches_the_open3d_path(name, tmp_path):
    case = CASES[name]
    raw = raw_mesh(case.build(), case.deflection_mm, tmp_path)
    _old, new = assert_paths_agree(name, raw)
    assert new.watertight is case.watertight
    if case.counts is not None:
        assert (new.vertices, new.triangles) == case.counts
    if case.watertight:
        assert new.volume > 0


def _loss(raw: brep.Mesh, measured: Measured) -> tuple[int, int]:
    """Vertices merged away and triangles dropped by the weld."""
    return len(raw.vertices) - measured.vertices, len(raw.triangles) - measured.triangles


@needs_both
@pytest.mark.parametrize("name", CASES)
def test_numpy_weld_matches_the_open3d_path_when_occt_tessellates_the_moved_shape(name, tmp_path):
    """OCCT does not tessellate a rotated shape identically (a torus gains or loses two triangles), so the invariants are
    the agreement with Open3D on every tessellation, what the weld removes from it, and the closure flag."""
    case = CASES[name]
    shape = case.build()
    raw_still = raw_mesh(shape, case.deflection_mm, tmp_path)
    _, still = assert_paths_agree(name, raw_still)
    for i, motion in enumerate(MOTIONS):
        raw = raw_mesh(_moved(shape, motion), case.deflection_mm, tmp_path)
        _, moved = assert_paths_agree(f"{name} motion {i} {motion}", raw)
        assert _loss(raw, moved) == _loss(raw_still, still), f"{name} motion {i} {motion}: {moved} but untransformed {still}"
        assert moved.watertight is still.watertight
        if case.watertight:
            assert moved.volume == pytest.approx(still.volume, rel=1e-4)


def _rotation_matrix(axis, angle: float) -> np.ndarray:
    x, y, z = axis
    cross = np.array([[0, -z, y], [z, 0, -x], [-y, x, 0]])
    return np.eye(3) + np.sin(angle) * cross + (1 - np.cos(angle)) * cross @ cross


@needs_ocp
@pytest.mark.parametrize("name", CASES)
def test_numpy_weld_of_one_tessellation_is_the_same_under_20_random_rigid_motions(name, tmp_path):
    case = CASES[name]
    raw = raw_mesh(case.build(), case.deflection_mm, tmp_path)
    still = brep.weld(raw)
    still_watertight = brep.mesh_topology(still).watertight
    assert still_watertight is case.watertight
    for i, (axis, angle, shift) in enumerate(MOTIONS):
        moved_raw = brep.Mesh(raw.vertices @ _rotation_matrix(axis, angle).T + np.array(shift), raw.triangles, raw.deflection_mm)
        moved = brep.weld(moved_raw)
        assert (len(moved.vertices), len(moved.triangles)) == (len(still.vertices), len(still.triangles)), f"{name} motion {i}"
        assert brep.mesh_topology(moved).watertight is still_watertight, f"{name} motion {i}"
