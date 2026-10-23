"""Regression tests for the review of fem_view (one block per finding; docs/fem/design.md, section fem_view).

1. A part far from the origin was refused as tampered: results.csv prints coordinates with %.9g (error up to 5e-9 |x|), the
   tolerance was 1e-6 x diagonal only.  Now 1e-6 x diagonal + 4 x 5e-9 x max|x|.
   Hand numbers: box 20 x 10 x 10 at x = 20000 mm: diagonal 24.49 mm, old tolerance 2.45e-5 mm, printing error up to
   5e-9 * 20020 = 1.0e-4 mm; new tolerance 2.45e-5 + 2e-8 * 20020 = 4.25e-4 mm.  At x = 100000: 2.45e-5 + 2.0e-3 mm.
2. Sharp edges inside a transitively "smooth patch" lost their split normals and their outline: now per facet pair.
   Box: every node on a box edge has one vertex per box face it lies on (2, corners 3), so vertices = sum over the six faces of
   the surface nodes on that face; the outline is every facet edge on the 12 box edges: (nodes on the edge - 1) segments each.
   Cylinder: only the two rims are feature edges: vertices = surface nodes + rim nodes, outline segments = rim nodes (closed loops).
   Keyhole (cylinder r 10 fused with the box 0..10 x -10..0, height 20): the vertical edge at (10, -10) is a 90-degree edge, the
   joins at (10, 0) and (0, -10) are tangent: every node of the 90-degree edge has two vertices with normals +x and -y.
3. An element listed twice leaves no boundary facet: input.invalid at /mesh_file, no traceback, no path.
4. Coplanar or collinear corners: input.invalid at /mesh_file naming the element (was blamed on /output_unit).
5. An interior node that ties the surface extreme no longer triggers "maximum inside the part".
6. The drawn surface encloses less than the mesh on convex curved faces and more on concave ones (holes).
7. The msh counts are checked before parsing, the file cap is 16 MB, and the deadline is checked while parsing.
Wording: blue-grey-red, light grey = 0.  Extras: a drawn shape turned inside out is warned about; a factor on a zero
displacement field is not called an exaggeration.
"""

import asyncio
import json
import math
from pathlib import Path

import numpy as np
import pytest

from eng_toolkit import external as X
from eng_toolkit.domains.fem import mesh as meshing
from eng_toolkit.domains.fem import tools as T
from eng_toolkit.domains.fem import view as viewer
from eng_toolkit.external_catalog import CCX, GMSH
from tests.fem_view_reader import gltf_to_model_mm, read_fem_glb, signed_volume
from tests.test_fem_view import artifact, call, fake_job, refused, run, surface, tet_nodes, write_csv, write_msh

HAVE_PROGRAMS = X.locate(GMSH)[0] is not None and X.locate(CCX)[0] is not None
try:
    import OCP  # noqa: F401

    HAVE_OCP = True
except ImportError:
    HAVE_OCP = False
real = pytest.mark.skipif(not (HAVE_PROGRAMS and HAVE_OCP), reason="needs OCP (uv sync --extra cad), Gmsh and CalculiX installed")
BAR_SUPPORTS = "x = 0 mm : ux; point(0 mm, 5 mm, 0 mm) : uy; point(0 mm, 5 mm, 10 mm) : uy; point(0 mm, 0 mm, 5 mm) : uz"


@pytest.fixture(scope="module")
def solved(tmp_path_factory):
    if not (HAVE_PROGRAMS and HAVE_OCP):
        pytest.skip("needs OCP, Gmsh and CalculiX")
    from tests import cad_fixtures as cad
    from tests import dfm_fixtures as dfm

    root = tmp_path_factory.mktemp("fem_view_review")
    cases = {
        "off20000": (cad.box(20000, 0, 0, 20, 10, 10), dict(element_size="3 mm", supports="x = 20000 mm", loads="force (1 kN, 0 N, 0 N) on x = 20020 mm")),
        "off100000": (cad.box(100000, 0, 0, 20, 10, 10), dict(element_size="3 mm", supports="x = 100000 mm", loads="force (1 kN, 0 N, 0 N) on x = 100020 mm")),
        "bar": (cad.box(0, 0, 0, 100, 10, 10), dict(element_size="5 mm", supports=BAR_SUPPORTS, loads="force (10 kN, 0 N, 0 N) on x = 100 mm")),
        "cylinder": (cad.cylinder(0, 0, 0, 10.0, 30.0), dict(element_size="3 mm", supports="z = 0 mm", loads="pressure 20 MPa on z = 30 mm")),
        "keyhole": (
            dfm.fuse(cad.cylinder(0, 0, 0, 10.0, 20.0), cad.box(0, -10, 0, 10, 10, 20)),
            dict(element_size="2 mm", supports="z = 0 mm", loads="force (1 kN, 0 N, 0 N) on z = 20 mm"),
        ),
        "plate_hole": (
            cad.cut(cad.box(0, 0, 0, 40, 40, 5), cad.cylinder(20, 20, -1, 5.0, 7.0)),
            dict(element_size="3 mm", supports="z = 0 mm", loads="pressure 10 MPa on z = 5 mm"),
        ),
    }
    out = {}
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("ENG_TOOLKIT_WS", str(root / "ws"))
        for name, (shape, args) in cases.items():
            model = str(cad.write_step(shape, root / f"{name}.step"))
            r = asyncio.run(call("fem_static", {"model_path": model, "youngs_modulus": "210000 MPa", "poisson_ratio": 0.3, **args}))
            assert r.ok, (name, r.error)
            out[name] = Path(r.data["job_dir"])
    return out


def view(job_dir, **args):
    r = asyncio.run(call("fem_view", {"result_dir": str(job_dir), **args}))
    assert r.ok, r.error
    return r, read_fem_glb(artifact(r, "fem_view.glb"))


def csv_xyz(job_dir) -> np.ndarray:
    lines = Path(job_dir, "results.csv").read_text().splitlines()
    return np.array([[float(v) for v in row.split(",")[1:4]] for row in lines[1:]])


def outline_points(parsed) -> np.ndarray:
    (line,) = [p for p in parsed["primitives"] if p["mode"] == 1 and p["extras"]["kind"] == "undeformed_outline"]
    return np.array([gltf_to_model_mm(p) for p in line["attributes"]["POSITION"]]), len(line["indices"]) // 2


# --------------------------------------------------------------------------- 1. far from the origin


def test_the_coordinate_tolerance_is_the_csv_rounding_bound_plus_the_guard():
    nodes = np.array([[20000.0, 0, 0], [20020.0, 10, 10]])
    diagonal = math.sqrt(400 + 100 + 100)  # 24.495 mm
    assert viewer.coordinate_tolerance(nodes) == pytest.approx(1e-6 * diagonal + 4 * 5e-9 * 20020.0, rel=1e-12)  # 4.25e-4 mm
    assert viewer.coordinate_tolerance(nodes) > 5e-9 * 20020.0 * 2  # well above the worst %.9g rounding (1.0e-4 mm)


def test_a_synthetic_job_100_m_from_the_origin_is_accepted_and_a_moved_node_is_still_refused(tmp_path):
    nodes = [(x + 100000.0, y, z) for x, y, z in tet_nodes()]
    msh = write_msh(tmp_path / "far.msh", nodes)
    printed = write_csv(tmp_path / "far.csv", nodes)  # %.9g: 100005 is exact, 100000.123456 would print as 100000.123
    assert run(mesh_file=str(msh), results_file=str(printed)).ok
    rows = printed.read_text().splitlines()
    rows[1] = rows[1].replace("100000,", "100000.05,", 1)  # node 1 moved by 0.05 mm: far beyond 2e-8 * 1e5 = 2e-3 mm
    (tmp_path / "moved.csv").write_text("\n".join(rows) + "\n")
    refused(run(mesh_file=str(msh), results_file=str(tmp_path / "moved.csv")), "fem.integrity_mismatch", "/results_file")


@real
@pytest.mark.parametrize("case", ["off20000", "off100000"])
def test_genuine_jobs_far_from_the_origin_are_accepted(solved, case):
    job_dir = solved[case]
    gap = float(np.abs(csv_xyz(job_dir) - viewer.read_mesh_file(job_dir / "mesh.msh", "/mesh_file", viewer.Deadline(60)).nodes).max())
    assert gap > 1e-6 * math.sqrt(600)  # the old tolerance (2.45e-5 mm) is exceeded by the %.9g printing alone
    r, parsed = view(job_dir)
    assert r.values["field_max"].quantity > 0
    x = [gltf_to_model_mm(p)[0] for p in surface(parsed)["attributes"]["POSITION"]]
    offset = float(case[3:])
    assert min(x) == pytest.approx(offset, abs=0.02) and max(x) == pytest.approx(offset + 20, abs=0.02)  # float32 at 100 m: 8e-3 mm steps


@real
def test_a_different_mesh_or_swapped_nodes_are_still_refused(solved, tmp_path):
    job_dir = solved["off20000"]
    lines = (job_dir / "results.csv").read_text().splitlines()
    a, b = lines[1].split(","), lines[2].split(",")
    a[1:4], b[1:4] = b[1:4], a[1:4]  # the coordinates of nodes 1 and 2 swapped
    swapped = tmp_path / "swapped.csv"
    swapped.write_text("\n".join([lines[0], ",".join(a), ",".join(b), *lines[3:]]) + "\n")
    refused(asyncio.run(call("fem_view", {"mesh_file": str(job_dir / "mesh.msh"), "results_file": str(swapped)})), "fem.integrity_mismatch", "/results_file")
    other = solved["off100000"]  # a different mesh of a different part
    r = asyncio.run(call("fem_view", {"mesh_file": str(job_dir / "mesh.msh"), "results_file": str(other / "results.csv")}))
    refused(r, "fem.integrity_mismatch", "/results_file")


# --------------------------------------------------------------------------- 2. feature edges per facet pair


def vertices_per_node(parsed) -> dict[int, list[int]]:
    s = surface(parsed)
    out: dict[int, list[int]] = {}
    for i, node in enumerate(s["attributes"]["_NODE"]):
        out.setdefault(int(node[0]), []).append(i)
    return out


@real
def test_box_every_edge_is_a_feature_edge(solved):
    job_dir = solved["bar"]
    r, parsed = view(job_dir, deformation_scale="100")
    xyz = csv_xyz(job_dir)
    groups = vertices_per_node(parsed)
    surface_nodes = np.array(sorted(groups)) - 1
    p = xyz[surface_nodes]
    faces = [np.abs(p[:, 0]) < 1e-9, np.abs(p[:, 0] - 100) < 1e-9, np.abs(p[:, 1]) < 1e-9, np.abs(p[:, 1] - 10) < 1e-9, np.abs(p[:, 2]) < 1e-9, np.abs(p[:, 2] - 10) < 1e-9]
    assert r.data["counts"]["vertices"] == sum(int(f.sum()) for f in faces)  # one vertex per (node, box face)
    on_faces = np.sum(faces, axis=0)
    for node, count in zip(surface_nodes, on_faces):
        assert len(groups[node + 1]) == count  # 1 inside a face, 2 on an edge, 3 at a corner
    edge_nodes = int(np.sum(on_faces == 2)) + 8  # nodes on the 12 edges, corners counted once
    # each of the 12 edges with k nodes (corners included) has k - 1 segments; summed: (edge nodes + corners counted 3 times) - 12
    segments_expected = int(np.sum(on_faces == 2)) + 8 * 3 - 12
    points, segments = outline_points(parsed)
    assert segments == segments_expected and len(points) == edge_nodes
    assert r.data["counts"]["feature_edges"] == segments_expected // 2


@real
def test_cylinder_only_the_rims_are_feature_edges(solved):
    job_dir = solved["cylinder"]
    r, parsed = view(job_dir, deformation_scale="100")
    xyz = csv_xyz(job_dir)
    groups = vertices_per_node(parsed)
    surface_nodes = np.array(sorted(groups)) - 1
    p = xyz[surface_nodes]
    rim = (np.abs(np.hypot(p[:, 0], p[:, 1]) - 10.0) < 1e-6) & ((np.abs(p[:, 2]) < 1e-9) | (np.abs(p[:, 2] - 30) < 1e-9))
    assert r.data["counts"]["vertices"] == len(surface_nodes) + int(rim.sum())
    points, segments = outline_points(parsed)
    assert segments == int(rim.sum())  # two closed loops: as many segments as nodes
    assert np.all(np.abs(np.hypot(points[:, 0], points[:, 1]) - 10.0) < 1e-3) and np.all((np.abs(points[:, 2]) < 1e-3) | (np.abs(points[:, 2] - 30) < 1e-3))


@real
def test_keyhole_the_ninety_degree_edge_is_split_and_outlined_and_the_tangent_joins_are_not(solved):
    job_dir = solved["keyhole"]
    r, parsed = view(job_dir, deformation_scale="100")
    xyz = csv_xyz(job_dir)
    groups = vertices_per_node(parsed)
    s = surface(parsed)
    normals = [gltf_to_model_mm(n) for n in s["attributes"]["NORMAL"]]
    edge = [n for n in groups if abs(xyz[n - 1][0] - 10) < 1e-6 and abs(xyz[n - 1][1] + 10) < 1e-6 and 1e-6 < xyz[n - 1][2] < 20 - 1e-6]
    assert len(edge) >= 10
    for node in edge:
        assert len(groups[node]) == 2
        directions = sorted((round(normals[i][0] / 1000), round(normals[i][1] / 1000)) for i in groups[node])
        assert directions == [(0, -1), (1, 0)]  # +x and -y, not their average
    points, _ = outline_points(parsed)
    on_edge = (np.abs(points[:, 0] - 10) < 1e-3) & (np.abs(points[:, 1] + 10) < 1e-3)
    assert int(on_edge.sum()) == len(edge) + 2  # the edge's interior nodes and its two ends on the rims
    for x0, y0 in ((10.0, 0.0), (0.0, -10.0)):  # tangent joins: outline points there only on the rims
        here = (np.abs(points[:, 0] - x0) < 1e-3) & (np.abs(points[:, 1] - y0) < 1e-3)
        assert np.all((np.abs(points[here, 2]) < 1e-3) | (np.abs(points[here, 2] - 20) < 1e-3))


def test_one_tetrahedron_every_node_has_one_vertex_per_facet():
    tetra = meshing.TetMesh(nodes=np.array(tet_nodes()), tets=np.array([[0, 1, 2, 3, 4, 5, 6, 7, 9, 8]]), size_mm=0.0)
    built = viewer.build_surface(tetra, meshing.boundary_facets(tetra))
    assert len(built.vertex_node) == 24 and built.feature.sum() == 6  # corners 3 facets, midside nodes 2; six sharp edges


# --------------------------------------------------------------------------- 3 and 4. meshes that have no surface to draw

GM = [(0, 1), (1, 2), (2, 0), (0, 3), (2, 3), (1, 3)]


def tet_files(tmp_path, corners, copies=1):
    mids = [tuple((corners[a][k] + corners[b][k]) / 2 for k in range(3)) for a, b in GM]
    nodes = list(corners) + mids
    rows = ["$MeshFormat", "2.2 0 8", "$EndMeshFormat", "$Nodes", "10"] + [f"{i + 1} {x} {y} {z}" for i, (x, y, z) in enumerate(nodes)]
    rows += ["$EndNodes", "$Elements", str(copies)] + [f"{k + 1} 11 2 0 1 1 2 3 4 5 6 7 8 9 10" for k in range(copies)] + ["$EndElements"]
    (tmp_path / "t.msh").write_text("\n".join(rows) + "\n")
    write_csv(tmp_path / "t.csv", nodes)
    return str(tmp_path / "t.msh"), str(tmp_path / "t.csv")


@pytest.mark.asyncio
async def test_an_element_listed_twice_has_no_boundary_and_is_refused_cleanly(tmp_path):
    msh, csv = tet_files(tmp_path, [(0, 0, 0), (10, 0, 0), (0, 10, 0), (0, 0, 10)], copies=2)
    r = refused(await call("fem_view", {"mesh_file": msh, "results_file": csv}), "input.invalid", "/mesh_file")
    assert "no boundary facets" in r.error.message and "traceback" not in r.error.details
    msh, csv = tet_files(tmp_path, [(0, 0, 0), (10, 0, 0), (0, 10, 0), (0, 0, 10)], copies=3)
    refused(await call("fem_view", {"mesh_file": msh, "results_file": csv}), "input.invalid", "/mesh_file")


def test_an_unexpected_error_in_the_surface_code_is_input_invalid_without_traceback_or_path(job, monkeypatch):
    def boom(*args):
        raise RuntimeError(r"C:\secret\place\mesh.msh went wrong")

    monkeypatch.setattr(viewer, "build_surface", boom)
    r = refused(run(result_dir=str(job)), "input.invalid", "/result_dir")
    assert "RuntimeError" in r.error.message and "secret" not in r.error.message and "secret" not in json.dumps(r.error.details)
    assert "traceback" not in r.error.details


@pytest.fixture
def job(tmp_path):
    return fake_job(tmp_path / "jobs" / "20261023T000000Z-00000000-fem_static")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "corners",
    [[(0, 0, 0), (10, 0, 0), (0, 10, 0), (5, 5, 0)], [(0, 0, 0), (10, 0, 0), (20, 0, 0), (0, 0, 10)]],
    ids=["coplanar", "collinear"],
)
async def test_degenerate_elements_are_blamed_on_the_mesh(tmp_path, corners):
    msh, csv = tet_files(tmp_path, corners)
    r = refused(await call("fem_view", {"mesh_file": msh, "results_file": csv}), "input.invalid", "/mesh_file")
    assert "element 1" in r.error.message and "coplanar or collinear" in r.error.message


# --------------------------------------------------------------------------- 5. ties between interior and surface extremes


def test_beyond_float32():
    assert not viewer.beyond_float32(99.999, 99.999)
    assert not viewer.beyond_float32(99.999 + 1e-12, 99.999)  # below one float32 step (7.6e-6 at 100)
    assert viewer.beyond_float32(100.001, 99.999)


@real
def test_an_interior_node_that_ties_the_surface_maximum_is_not_reported_inside(solved, tmp_path):
    job_dir = solved["bar"]
    _, parsed = view(job_dir)
    on_surface = set(vertices_per_node(parsed))
    msh = (job_dir / "mesh.msh").read_text().splitlines()
    k = msh.index("$Nodes")
    n = int(msh[k + 1])
    inner = next(i for i in range(n) if i + 1 not in on_surface)  # 0-based, the first interior node
    block = msh[k + 2 : k + 2 + n]
    (tmp_path / "tie.msh").write_text("\n".join(msh[: k + 2] + [block[inner]] + block[:inner] + block[inner + 1 :] + msh[k + 2 + n :]) + "\n")
    rows = (job_dir / "results.csv").read_text().splitlines()
    order = [inner] + list(range(inner)) + list(range(inner + 1, n))
    renumbered = [",".join([str(i + 1)] + rows[1 + o].split(",")[1:]) for i, o in enumerate(order)]
    (tmp_path / "tie.csv").write_text("\n".join([rows[0]] + renumbered) + "\n")
    r = asyncio.run(call("fem_view", {"mesh_file": str(tmp_path / "tie.msh"), "results_file": str(tmp_path / "tie.csv")}))
    assert r.ok, r.error
    stats = r.data["statistics"]["all_nodes"]
    assert stats["max_on_surface"] and stats["min_on_surface"] and stats["max_node"] != 1  # node 1 is now the interior one
    assert not any("inside the part" in w for w in r.warnings)
    # and a strictly larger interior value is still reported: node 1 (interior) gets 200 MPa
    raised = [rows[0], ",".join(renumbered[0].split(",")[:-1] + ["200"]), *renumbered[1:]]
    (tmp_path / "hot.csv").write_text("\n".join(raised) + "\n")
    r = asyncio.run(call("fem_view", {"mesh_file": str(tmp_path / "tie.msh"), "results_file": str(tmp_path / "hot.csv")}))
    assert r.ok and r.data["statistics"]["all_nodes"]["max_node"] == 1 and not r.data["statistics"]["all_nodes"]["max_on_surface"]
    assert any("maximum of von_mises over all nodes (200 MPa, node 1) is inside the part" in w for w in r.warnings)
    assert r.values["field_max"].quantity == pytest.approx(99.999)  # the surface does not show it


# --------------------------------------------------------------------------- 6. convex versus concave curved faces


@real
def test_drawn_volume_is_below_the_mesh_on_a_convex_face_and_above_it_around_a_hole(solved):
    for name, sign in (("cylinder", -1), ("plate_hole", +1)):
        r, parsed = view(solved[name])
        s = surface(parsed)
        drawn = signed_volume([gltf_to_model_mm(p) for p in s["attributes"]["POSITION"]], s["triangles"])
        mesh_volume = r.data["closed_surface"]["mesh_volume_mm3"]
        assert math.copysign(1.0, drawn - mesh_volume) == sign, (name, drawn, mesh_volume)
        model = [gltf_to_model_mm(p) for p in s["attributes"]["POSITION"]]
        longest = max(math.dist(model[t[i]], model[t[(i + 1) % 3]]) for t in s["triangles"] for i in range(3))
        radius, area = (10.0, 2 * math.pi * 10 * 30) if name == "cylinder" else (5.0, 2 * math.pi * 5 * 5)
        assert abs(drawn - mesh_volume) < area * longest**2 / (8 * radius)  # sagitta bound


# --------------------------------------------------------------------------- 7. msh caps before parsing, deadline while parsing


def test_declared_counts_above_the_caps_are_refused_before_any_row_is_parsed(tmp_path, job):
    big = tmp_path / "declares.msh"
    big.write_text("$MeshFormat\n2.2 0 8\n$EndMeshFormat\n$Nodes\n5000000\n1 0 0 0\n$EndNodes\n$Elements\n1\n1 11 2 0 1 1 2 3 4 5 6 7 8 9 10\n$EndElements\n")
    r = refused(run(mesh_file=str(big), results_file=str(job / "results.csv")), "input.too_large", "/mesh_file")
    assert "5000000 nodes" in r.error.message
    big.write_text("$MeshFormat\n2.2 0 8\n$EndMeshFormat\n$Nodes\n1\n1 0 0 0\n$EndNodes\n$Elements\n9000000\n$EndElements\n")
    refused(run(mesh_file=str(big), results_file=str(job / "results.csv")), "input.too_large", "/mesh_file")


def test_the_mesh_file_cap_is_16_mb(tmp_path, job):
    assert viewer.MAX_MESH_BYTES == 16_000_000
    big = tmp_path / "padded.msh"
    with big.open("wb") as fh:
        fh.write(b"$MeshFormat\n2.2 0 8\n$EndMeshFormat\n")
        fh.write(b" " * 16_000_100)
    r = refused(run(mesh_file=str(big), results_file=str(job / "results.csv")), "input.too_large", "/mesh_file")
    assert "16000000" in r.error.message


def test_the_deadline_is_checked_while_parsing(tmp_path, job, monkeypatch):
    rows = ["$MeshFormat", "2.2 0 8", "$EndMeshFormat", "$Nodes", "100000"] + [f"{i + 1} 0.5 0.5 0.5" for i in range(100000)]
    rows += ["$EndNodes", "$Elements", "1", "1 11 2 0 1 1 2 3 4 5 6 7 8 9 10", "$EndElements"]
    (tmp_path / "slow.msh").write_text("\n".join(rows) + "\n")
    phases = []

    class Clock:
        now = 0.0

        @classmethod
        def monotonic(cls):
            cls.now += 0.01
            return cls.now

    real_call = viewer.Deadline.__call__

    def spy(self, phase):
        phases.append(phase)
        if phases.count("read mesh") == 3:
            Clock.now += 100.0
        return real_call(self, phase)

    monkeypatch.setattr(viewer, "time", Clock)
    monkeypatch.setattr(viewer.Deadline, "__call__", spy)
    r = refused(run(mesh_file=str(tmp_path / "slow.msh"), results_file=str(job / "results.csv"), max_seconds=5), "fem.time_budget_exceeded", "/max_seconds")
    assert "read mesh" in r.error.message and phases.count("read mesh") == 3  # stopped inside the node rows (5 chunks of 20000)


@real
def test_the_own_reader_reads_what_mesh_read_msh_reads(solved):
    for name in ("bar", "keyhole"):
        path = solved[name] / "mesh.msh"
        ours = viewer.read_mesh_file(path, "/mesh_file", viewer.Deadline(60))
        theirs = meshing.read_msh(path, 0.0)
        assert np.array_equal(ours.nodes, theirs.nodes) and np.array_equal(ours.tets, theirs.tets)


def test_the_own_reader_matches_on_the_synthetic_tetrahedron(job):
    ours = viewer.read_mesh_file(job / "mesh.msh", "/mesh_file", viewer.Deadline(60))
    theirs = meshing.read_msh(job / "mesh.msh", 0.0)
    assert np.array_equal(ours.nodes, theirs.nodes) and np.array_equal(ours.tets, theirs.tets)


# --------------------------------------------------------------------------- wording and extras


def test_the_description_says_grey_not_white():
    assert "blue-grey-red" in T.VIEW_DESCRIPTION and "white" not in T.VIEW_DESCRIPTION
    middle = viewer.TABLES["diverging"][32]
    assert viewer.hex_colour(middle) == "#DDDDDD"  # Moreland's neutral: light grey, not white


def test_a_shape_turned_inside_out_is_warned_about(tmp_path):
    nodes = tet_nodes()
    msh = write_msh(tmp_path / "t.msh", nodes)
    rows = ["node,x_mm,y_mm,z_mm,ux_mm,uy_mm,uz_mm,u_mag_mm,von_mises_mpa"]
    rows += [f"{i + 1},{x:.9g},{y:.9g},{z:.9g},{-2 * x:.9g},0,0,{2 * x:.9g},1" for i, (x, y, z) in enumerate(nodes)]  # x -> x - 2x = -x: mirrored
    (tmp_path / "mirror.csv").write_text("\n".join(rows) + "\n")
    r = run(mesh_file=str(msh), results_file=str(tmp_path / "mirror.csv"), deformation_scale="1")
    assert r.ok and r.data["deformation"]["inverted"] is True
    assert r.data["closed_surface"]["enclosed_volume_mm3_drawn"] == pytest.approx(-1000 / 6, rel=1e-5)
    assert any("inside out" in w for w in r.warnings) and "inside out" in artifact(r, "legend.svg").read_text(encoding="utf-8")
    assert run(mesh_file=str(msh), results_file=str(tmp_path / "mirror.csv"), deformation_scale="0.1").data["deformation"]["inverted"] is False


def test_a_factor_on_a_zero_displacement_field_is_not_an_exaggeration(tmp_path):
    nodes = tet_nodes()
    msh = write_msh(tmp_path / "t.msh", nodes)
    rows = ["node,x_mm,y_mm,z_mm,ux_mm,uy_mm,uz_mm,u_mag_mm,von_mises_mpa"] + [f"{i + 1},{x:.9g},{y:.9g},{z:.9g},0,0,0,0,1" for i, (x, y, z) in enumerate(nodes)]
    (tmp_path / "still.csv").write_text("\n".join(rows) + "\n")
    r = run(mesh_file=str(msh), results_file=str(tmp_path / "still.csv"), deformation_scale="200")
    assert r.ok and r.data["deformation"]["exaggerated"] is False and r.values["deformation_factor"].quantity == 200.0
    legend = artifact(r, "legend.svg").read_text(encoding="utf-8")
    assert "exaggerated" not in legend and "Displacements are zero" in legend
    assert not any("exaggerated" in w for w in r.warnings) and any("every displacement is zero" in w for w in r.warnings)


@pytest.mark.asyncio
async def test_what_is_echoed_is_bounded(tmp_path):
    long_path = str(tmp_path / ("d" * 900) / "x.msh")  # within the 1024-character cap: named back in file_not_found, as documented
    r = refused(await call("fem_view", {"mesh_file": long_path, "results_file": "y.csv"}), None, "/mesh_file")
    assert len(r.error.message) < 1200
    r = refused(await call("fem_view", {"mesh_file": "x" * 6000 + ".msh", "results_file": "y.csv"}), "input.invalid", "/mesh_file")
    assert "x" * 100 not in r.error.message and "x" * 100 not in json.dumps(r.error.details)
    r = refused(await call("fem_view", {"result_dir": str(tmp_path), "field": "abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG"}), "input.invalid", "/field")
    assert "abcdefghijklmnopqrstuvwxyz012345..." in r.error.message and "ABCDEFG" not in r.error.message  # short values: 32 characters
