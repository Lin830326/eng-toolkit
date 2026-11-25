"""Second independent review: exact gateway inputs, real readers, closed-form references.

Only the CAD loader and external programs are replaced. The supplied solver output
is an equilibrium fixture, not evidence of CalculiX accuracy or conditioning.
"""

from decimal import Decimal, localcontext
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from eng_toolkit.domains.fem import mesh as M
from eng_toolkit.domains.fem import solver as S
from eng_toolkit.domains.fem import tools as T
from tests.fem_view_reader import linear_to_srgb, read_fem_glb
from tests.test_fem_gateway import call, finite
from tests.test_fem_view import artifact, surface


INVERTED = np.array([
    [0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1],
    [.251, -.022, -.260], [.491, .465, .232], [-.081, .023, .235],
    [-.209, .095, .257], [.572, -.554, .739], [.108, .567, .511],
])
EDGES = ((0, 1), (1, 2), (2, 0), (0, 3), (1, 3), (2, 3))


def tet(length=10.0, origin=0.0):
    corners = np.array([[0, 0, 0], [length, 0, 0], [0, length, 0], [0, 0, length]])
    return np.vstack([corners, [(corners[a] + corners[b]) / 2 for a, b in EDGES]]) + origin


def write_mesh(path, nodes, extra=""):
    # Input nodes use C3D10 order; Gmsh swaps its last two edge nodes.
    rows = ["$MeshFormat", "2.2 0 8", "$EndMeshFormat", "$Nodes", str(len(nodes))]
    rows += [f"{i} " + " ".join(format(x, ".17g") for x in p) for i, p in enumerate(nodes, 1)]
    rows += ["$EndNodes", "$Elements", str(1 + bool(extra)), "1 11 0 1 2 3 4 5 6 7 8 10 9"]
    if extra:
        rows.append(extra)
    rows.append("$EndElements")
    path.write_text("\n".join(rows) + "\n", encoding="ascii")
    return path


@pytest.fixture
def pipeline(tmp_path, monkeypatch):
    config = SimpleNamespace(nodes=tet(), extra="", volume=1000 / 6, stress=0.0, displacement=0.0, unbalanced_moment=False, decks=[])
    model = tmp_path / "part.step"
    model.write_text("CAD fixture", encoding="ascii")
    config.args = dict(model_path=str(model), element_size="5 mm")
    config.static = dict(**config.args, youngs_modulus="1 MPa", poisson_ratio=.3,
                         supports="x = 0 mm", loads="force (0 N, 0 N, -10 N) on z = 0 mm")
    monkeypatch.setattr(T, "_require_programs", lambda *args: None)
    monkeypatch.setattr(T, "_version", lambda tool: "fixture")
    monkeypatch.setattr(T.brep, "read_solid", lambda *a, **k: SimpleNamespace(
        faces=4, solids=1, roots_transferred=1, roots_in_file=1))
    monkeypatch.setattr(T.brep, "closure", lambda solid: SimpleNamespace(closed=True))
    monkeypatch.setattr(T.brep, "mass_properties", lambda solid: SimpleNamespace(
        volume_mm3=config.volume, bbox_min_mm=tuple(config.nodes[:4].min(axis=0)),
        bbox_max_mm=tuple(config.nodes[:4].max(axis=0)), area_mm2=600.0))  # the surface of the 10 mm bounding cube: brep.mass_properties always has an area
    record = SimpleNamespace(seconds=0.0, returncode=0, stdout_tail="", stderr_tail="")

    def gmsh(solid, size, workdir):
        return write_mesh(workdir / "part.msh", config.nodes, config.extra), record

    def ccx(tool, args, *, workdir, **kwargs):
        deck = (workdir / "job.inp").read_text()
        config.decks.append(deck)
        n = 10
        force = np.zeros((n, 3))
        section = deck.split("*CLOAD\n")[1].split("*NODE FILE")[0]
        for row in section.splitlines():
            node, axis, value = row.split(",")
            force[int(node) - 1, int(axis) - 1] = float(value)
        held = np.flatnonzero(config.nodes[:n, 0] == 0)
        # Balance the applied force and moment independently of production code.
        matrix = np.zeros((6, 3 * len(held)))
        for k, node in enumerate(held):
            for axis in range(3):
                basis = np.eye(3)[axis]
                matrix[:3, 3 * k + axis] = basis
                matrix[3:, 3 * k + axis] = np.cross(config.nodes[node], basis)
        wrench = np.r_[force.sum(axis=0), np.cross(config.nodes[:n], force).sum(axis=0)]
        reaction = np.linalg.lstsq(matrix, -wrench, rcond=None)[0].reshape(-1, 3)
        if config.unbalanced_moment:
            # Keep total force unchanged but add a couple as large as the input force.
            reaction[0, 2] += abs(force[:, 2].sum())
            reaction[1, 2] -= abs(force[:, 2].sum())
        internal = reaction + force[held]
        rows = ["displacements (vx,vy,vz) for set nall"]
        rows += [f"{i} {config.displacement:.17g} 0 0" for i in range(1, n + 1)]
        rows += ["forces (fx,fy,fz) for set nsup"]
        rows += [f"{i + 1} " + " ".join(format(x, ".17g") for x in r) for i, r in zip(held, internal)]
        rows += ["total force (fx,fy,fz) for set nsup", " ".join(format(x, ".17g") for x in internal.sum(axis=0))]
        (workdir / "job.dat").write_text("\n".join(rows), encoding="ascii")
        rows = [" -4  STRESS"]
        rows += [f" -1{i:10d}" + "".join(f"{x:12.5E}" for x in (config.stress, 0, 0, 0, 0, 0)) for i in range(1, n + 1)]
        rows.append(" -3")
        (workdir / "job.frd").write_text("\n".join(rows), encoding="ascii")
        return record

    monkeypatch.setattr(M, "run_gmsh", gmsh)
    monkeypatch.setattr(T.ext, "run", ccx)
    return config


def refusal(result, family, pointer):
    assert not result.ok, result
    assert result.error.family == family, result.error
    assert result.error.details.get("param") == pointer, result.error
    assert "traceback" not in str(result.model_dump()).lower()
    assert result.review_status == "unreviewed"
    finite(result.model_dump())


def independent_gauss_det():
    """Differentiate the quadratic barycentric basis with 60-digit Decimal arithmetic."""
    with localcontext() as ctx:
        ctx.prec = 60
        small = (Decimal(5) - Decimal(5).sqrt()) / 20
        bary = [1 - 3 * small, small, small, small]
        dl = [(-1, -1, -1), (1, 0, 0), (0, 1, 0), (0, 0, 1)]
        deriv = [[(4 * bary[i] - 1) * d for d in dl[i]] for i in range(4)]
        deriv += [[4 * (bary[b] * dl[a][j] + bary[a] * dl[b][j]) for j in range(3)] for a, b in EDGES]
        coords = [[Decimal(str(x)) for x in p] for p in INVERTED]
        j = [[sum(coords[k][a] * deriv[k][b] for k in range(10)) for b in range(3)] for a in range(3)]
        return float(sum(j[0][a] * (j[1][(a + 1) % 3] * j[2][(a + 2) % 3] - j[1][(a + 2) % 3] * j[2][(a + 1) % 3]) for a in range(3)))


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", ["fem_mesh", "fem_static"])
async def test_01_inverted_gauss_point(pipeline, tool):
    assert independent_gauss_det() == pytest.approx(-.02182089431, abs=5e-11)
    pipeline.nodes = INVERTED
    pipeline.volume = 1 / 6
    args = pipeline.args if tool == "fem_mesh" else pipeline.static
    r = await call(tool, {**args, "element_size": "0.5 mm"})
    refusal(r, "engine.failure", "/element_size")


@pytest.mark.asyncio
@pytest.mark.parametrize("nu", [.49999999999999, -.99999999999999, np.nextafter(.5, 0), np.nextafter(-1., 0)])
async def test_02_poisson_round_trip(pipeline, nu):
    r = await call("fem_static", {**pipeline.static, "poisson_ratio": nu})
    assert r.ok, r.error
    serialized = pipeline.decks[-1].split("*ELASTIC\n")[1].splitlines()[0].split(",")[1]
    assert float(serialized) == nu
    assert len(serialized.strip()) <= 20
    # K and G are finite positive rationals of the serialized decimal numbers.
    value = Decimal(serialized.strip())
    assert Decimal(1) / (3 * (1 - 2 * value)) > 0
    assert Decimal(1) / (2 * (1 + value)) > 0


@pytest.mark.asyncio
@pytest.mark.parametrize("loads", [
    "pressure 6 MPa on z = 0 mm; pressure 6 MPa on z = 0 mm", "pressure 12 MPa on z = 0 mm",
    "force (0 N, 0 N, 300 N) on z = 0 mm; pressure 6 MPa on z = 0 mm",
    "force (0 N, 0 N, 300 N) on z = 0 mm; force (0 N, 0 N, 300 N) on z = 0 mm",
])
async def test_03_accumulated_traction(pipeline, loads):
    # Each triangle has area 10*10/2 = 50 mm2; 12 MPa gives 600 N and stress/E = 12.
    r = await call("fem_static", {**pipeline.static, "loads": loads})
    refusal(r, "input.out_of_range", "/loads")
    assert r.error.details["nominal_strain"] == pytest.approx(12)


@pytest.mark.asyncio
async def test_03_opposing_tractions_use_the_combined_value(pipeline):
    r = await call("fem_static", {**pipeline.static, "loads":
        "pressure 12 MPa on z = 0 mm; force (0 N, 0 N, -300 N) on z = 0 mm"})
    assert r.ok, r.error
    assert r.values["applied_force_z"].quantity == pytest.approx(300)


@pytest.mark.asyncio
async def test_04_cancelling_loads(pipeline):
    r = await call("fem_static", {**pipeline.static, "loads":
        "force (0 N, 0 N, -10 N) on z = 0 mm; force (0 N, 0 N, 10 N) on z = 0 mm"})
    refusal(r, "input.invalid", "/loads")


@pytest.mark.asyncio
async def test_04_tiny_load_norm(pipeline):
    r = await call("fem_static", {**pipeline.static, "loads": "force (0 N, 0 N, -1e-200 N) on z = 0 mm"})
    assert r.ok, r.error
    assert r.values["applied_force_z"].quantity == pytest.approx(-1e-200, rel=1e-12, abs=0)
    assert r.data["equilibrium"]["scale_n"] > 0
    assert r.values["equilibrium_residual"].quantity < 1e-6


@pytest.mark.asyncio
async def test_04_tiny_load_still_requires_moment_balance(pipeline):
    pipeline.unbalanced_moment = True
    r = await call("fem_static", {**pipeline.static, "loads": "force (0 N, 0 N, -1e-200 N) on z = 0 mm"})
    refusal(r, "fem.equilibrium_failed", "/loads")
    assert r.error.details["moment_residual_relative"] > 1e-5


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", ["fem_mesh", "fem_static"])
@pytest.mark.parametrize("size,family", [("1e-110 mm", "input.too_large"), ("1e110 mm", "input.out_of_range"), ("1e-110 meter", "input.too_large")])
async def test_05_size_before_cube(pipeline, tool, size, family):
    pipeline.volume = 1000  # exact 10 mm cube volume and bounding box
    r = await call(tool, {**(pipeline.args if tool == "fem_mesh" else pipeline.static), "element_size": size})
    refusal(r, family, "/element_size")


@pytest.mark.asyncio
@pytest.mark.parametrize("unit", ["ym**11/m**10", "m**11/ym**10"])
async def test_06_unrepresentable_volume_unit(pipeline, unit):
    r = await call("fem_mesh", {**pipeline.args, "output_unit": unit})
    refusal(r, "input.out_of_range", "/output_unit")


@pytest.mark.asyncio
async def test_06_unrepresentable_length_unit(pipeline):
    r = await call("fem_static", {**pipeline.static, "output_length_unit": "ym**14/m**13"})
    refusal(r, "input.out_of_range", "/output_length_unit")


@pytest.mark.asyncio
async def test_06_converted_stress_is_bounded_before_csv(pipeline):
    pipeline.stress = 10
    r = await call("fem_static", {**pipeline.static, "output_stress_unit": "yPa"})
    refusal(r, "input.out_of_range", "/output_stress_unit")
    assert not (Path(r.data["job_dir"]) / "results.csv").exists()


@pytest.mark.asyncio
async def test_07_absurd_solver_stress(pipeline):
    pipeline.stress = 1e200  # uniaxial von Mises is |sxx| = 1e200, not infinity
    r = await call("fem_static", pipeline.static)
    refusal(r, "input.out_of_range", "/loads")
    assert not (Path(r.data["job_dir"]) / "results.csv").exists()


@pytest.mark.asyncio
async def test_07_absurd_displacement_is_refused_before_csv(pipeline):
    pipeline.displacement = 1e200
    r = await call("fem_static", pipeline.static)
    refusal(r, "input.out_of_range", "/loads")
    assert not (Path(r.data["job_dir"]) / "results.csv").exists()


def test_07_von_mises_scaled_norm():
    # Closed forms: uniaxial |s|; hydrostatic 0; pure shear sqrt(3)*|tau|.
    stress = np.array([[1e200, 0, 0, 0, 0, 0], [1e200] * 3 + [0] * 3, [0, 0, 0, 1e-200, 0, 0]])
    assert S.von_mises(stress) == pytest.approx([1e200, 0, 3**.5 * 1e-200], rel=1e-14, abs=0)


@pytest.mark.asyncio
@pytest.mark.parametrize("override,family,pointer", [
    ({"poisson_ratio": .5}, "input.invalid", "/poisson_ratio"),
    ({"youngs_modulus": "1e6 GPa"}, "input.out_of_range", "/youngs_modulus"),
    ({"element_size": "100 mm"}, "input.out_of_range", "/element_size"),
    ({"loads": "pressure 12 MPa on z = 0 mm"}, "input.out_of_range", "/loads"),
])
async def test_08_pointer_contract(pipeline, override, family, pointer):
    r = await call("fem_static", {**pipeline.static, **override})
    refusal(r, family, pointer)


def view_inputs(tmp_path, nodes, extra="", signed=False, displacement=(0, 0, 0)):
    mesh = write_mesh(tmp_path / "mesh.msh", nodes, extra)
    rows = [",".join(T.CSV_COLUMNS)]
    for i, p in enumerate(nodes[:10], 1):
        value = (-100, 0, 25, 200)[(i - 1) % 4] if signed else 0
        row = [i, *p, *displacement, float(np.hypot.reduce(displacement)), value, 0, 0, 0, 0, 0, abs(value)]
        rows.append(",".join(format(x, ".17g") for x in row))
    csv = tmp_path / "results.csv"
    csv.write_text("\n".join(rows), encoding="ascii")
    return dict(mesh_file=str(mesh), results_file=str(csv))


def decimal_volume(points, triangles):
    with localcontext() as ctx:
        ctx.prec = 60
        p = [[Decimal.from_float(float(x)) for x in row] for row in points]
        p = [[x - o for x, o in zip(row, p[0])] for row in p]
        total = Decimal(0)
        for ia, ib, ic in triangles:
            a, b, c = p[ia], p[ib], p[ic]
            total += sum(a[k] * (b[(k + 1) % 3] * c[(k + 2) % 3] - b[(k + 2) % 3] * c[(k + 1) % 3]) for k in range(3))
        return float(total / 6)


@pytest.mark.asyncio
@pytest.mark.parametrize("length", [.01, 1.0])
async def test_09_10_translated_geometry_and_volume(tmp_path, length):
    nodes = tet(length, 1000000)
    r = await call("fem_view", {**view_inputs(tmp_path, nodes), "mesh_edges": True})
    assert r.ok, r.error
    parsed = read_fem_glb(artifact(r, "fem_view.glb"), world_space=False)
    s = surface(parsed)
    positions = np.array(s["attributes"]["POSITION"])
    assert len(np.unique(positions, axis=0)) == 10
    triangles = np.array(s["triangles"])
    areas = np.linalg.norm(np.cross(positions[triangles[:, 1]] - positions[triangles[:, 0]], positions[triangles[:, 2]] - positions[triangles[:, 0]]), axis=1)
    assert np.all(areas > 0)
    exact_bytes = decimal_volume(positions, triangles) * 1e9
    assert r.data["closed_surface"]["enclosed_volume_mm3_drawn"] == pytest.approx(exact_bytes, rel=1e-8)
    assert exact_bytes == pytest.approx(length**3 / 6, rel=5e-7)
    translation = np.array(parsed["node"]["translation"])
    absolute = (positions + translation)[:, [0, 2, 1]] * [1000, -1000, 1000]
    node_ids = np.array(s["attributes"]["_NODE"]).ravel().astype(int) - 1
    np.testing.assert_allclose(absolute, nodes[node_ids], rtol=0, atol=length * 1e-6)
    assert np.max(np.abs(positions)) <= length / 1000


@pytest.mark.asyncio
async def test_09_deformed_surface_and_both_line_layers_share_the_origin(tmp_path):
    nodes = tet(.01, 1000000)
    displacement = np.array([0, 0, .0001])
    r = await call("fem_view", {**view_inputs(tmp_path, nodes, displacement=displacement),
                               "mesh_edges": True, "deformation_scale": "10"})
    assert r.ok, r.error
    parsed = read_fem_glb(artifact(r, "fem_view.glb"))
    for primitive in parsed["primitives"]:
        positions = np.array(primitive["attributes"]["POSITION"])
        model = positions[:, [0, 2, 1]] * [1000, -1000, 1000]
        expected = nodes if primitive["extras"]["kind"] == "undeformed_outline" else nodes + 10 * displacement
        nearest = np.min(np.linalg.norm(model[:, None] - expected[None, :], axis=2), axis=1)
        assert np.max(nearest) < 1e-8


@pytest.mark.asyncio
async def test_11_diverging_zero_is_grey(tmp_path):
    r = await call("fem_view", {**view_inputs(tmp_path, tet(), signed=True), "field": "s_xx", "range": "-100 MPa, 200 MPa"})
    assert r.ok, r.error
    parsed = read_fem_glb(artifact(r, "fem_view.glb"))
    attrs = surface(parsed)["attributes"]
    for (value,), color in zip(attrs["_FIELD"], attrs["COLOR_0"]):
        rgb = [round(linear_to_srgb(c) * 255) for c in color]
        if value == 0:
            assert rgb == [221, 221, 221]
        elif value > 0:
            assert rgb[0] > rgb[2]
        else:
            assert rgb[2] > rgb[0]
    assert r.data["range"]["low"] == -200 and r.data["range"]["high"] == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", ["fem_mesh", "fem_static", "fem_view"])
@pytest.mark.parametrize("extra", ["2 4 0 11 12 13 14", "2 5 0 1 2 3 4 11 12 13 14"])
async def test_12_mixed_volume_elements(pipeline, tmp_path, tool, extra):
    pipeline.nodes = np.vstack([tet(), [[20, 0, 0], [30, 0, 0], [20, 10, 0], [20, 0, 10]]])
    pipeline.extra = extra
    args = view_inputs(tmp_path, pipeline.nodes, extra) if tool == "fem_view" else (pipeline.args if tool == "fem_mesh" else pipeline.static)
    r = await call(tool, args)
    refusal(r, "input.unsupported" if tool == "fem_view" else "engine.failure", "/mesh_file" if tool == "fem_view" else "/element_size")


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", ["fem_mesh", "fem_static", "fem_view"])
async def test_12_known_boundary_elements_are_allowed(pipeline, tmp_path, tool):
    pipeline.extra = "2 9 0 1 2 3 5 6 7"  # six-node boundary triangle
    args = view_inputs(tmp_path, pipeline.nodes, pipeline.extra) if tool == "fem_view" else (pipeline.args if tool == "fem_mesh" else pipeline.static)
    r = await call(tool, args)
    assert r.ok, r.error
