"""Mesh arithmetic, rigid-body detection, the CalculiX deck and the result parsers of the fem domain, checked by hand.  No program is started.

One right-angle tetrahedron with legs of 10 mm (corners (0,0,0), (10,0,0), (0,10,0), (0,0,10), straight edges):
    volume              = 10^3 / 6 = 166.6667 mm^3
    edge lengths        = 3 x 10 and 3 x 10 sqrt(2); sum of squares = 3 * 100 + 3 * 200 = 900
    mean ratio          = 12 (3 V)^(2/3) / 900 = 12 * 500^(2/3) / 900 = 12 * 62.99605 / 900 = 0.839947
    boundary area       = 3 * 50 + |(-10,10,0) x (-10,0,10)| / 2 = 150 + 86.60254 = 236.60254 mm^2
A regular tetrahedron (corners at alternate corners of a cube of side 2): edge 2 sqrt 2, volume 2^3 / 3 = 8/3, mean ratio 1.
"""

import re

import numpy as np
import pytest

from eng_toolkit.domains.fem import mesh as M
from eng_toolkit.domains.fem import solver as V
from eng_toolkit.errors import ToolFailure
from tests.test_fem_selectors import one_tet


def regular_tet() -> M.TetMesh:
    corners = np.array([[1, 1, 1], [-1, 1, -1], [1, -1, -1], [-1, -1, 1]], dtype=float)
    mids = [(corners[a] + corners[b]) / 2 for a, b in M._MID_EDGES]
    return M.TetMesh(nodes=np.vstack([corners, mids]), tets=np.arange(10, dtype=np.int64).reshape(1, 10), size_mm=2.0)


# --------------------------------------------------------------------------- quality and volume


def test_quality_volume_of_the_right_angle_tetrahedron():
    quality, volume = M.check_and_measure(one_tet())
    assert volume == pytest.approx(1000.0 / 6.0, rel=1e-12)
    assert quality.min_ratio == pytest.approx(0.839947, abs=1e-6)
    assert quality.slivers == 0 and quality.min_jacobian_ratio == pytest.approx(1.0)


def test_a_regular_tetrahedron_has_mean_ratio_one_and_volume_eight_thirds():
    quality, volume = M.check_and_measure(regular_tet())
    assert quality.min_ratio == pytest.approx(1.0, abs=1e-12)
    assert volume == pytest.approx(8.0 / 3.0, rel=1e-12)


def test_a_flat_tetrahedron_is_refused_and_a_sliver_is_counted():
    flat = one_tet()
    nodes = flat.nodes.copy()
    nodes[3] = (0.0, 0.0, 0.0)  # apex into the base: zero volume
    nodes[4:] = [(nodes[a] + nodes[b]) / 2 for a, b in M._MID_EDGES]
    with pytest.raises(ToolFailure) as caught:
        M.check_and_measure(M.TetMesh(nodes=nodes, tets=flat.tets, size_mm=1.0))
    assert caught.value.family == "engine.failure" and "non-positive volume" in caught.value.message
    thin = one_tet()
    nodes = thin.nodes.copy()
    nodes[3] = (3.0, 3.0, 0.01)  # nearly flat, still positive
    nodes[4:] = [(nodes[a] + nodes[b]) / 2 for a, b in M._MID_EDGES]
    quality, _ = M.check_and_measure(M.TetMesh(nodes=nodes, tets=thin.tets, size_mm=1.0))
    assert quality.slivers == 1 and quality.min_ratio < M.SLIVER_QUALITY


def test_an_inverted_curved_element_is_refused():
    mesh = one_tet()
    nodes = mesh.nodes.copy()
    nodes[4] = (nodes[0] + nodes[1]) / 2 + np.array([0.0, 0.0, 0.0]) + np.array([0.0, 40.0, 0.0])  # midside node thrown far off its edge
    with pytest.raises(ToolFailure) as caught:
        M.check_and_measure(M.TetMesh(nodes=nodes, tets=mesh.tets, size_mm=1.0))
    assert "inverted" in caught.value.message


def test_a_curved_edge_changes_the_volume_linearly_and_with_the_sign_of_the_bulge():
    # Moving the midside node of edge 0-1 by d along +y bends that one edge; the volume of the element is the integral of det J, which is
    # linear in the midside coordinates, so the volume is linear in d (the exact amount is not derived here), and the straight element is the
    # d = 0 member of the family.
    mesh = one_tet()
    straight = M.check_and_measure(mesh)[1]
    volumes = []
    for d in (-1.0, 0.0, 1.0):
        nodes = mesh.nodes.copy()
        nodes[4] = nodes[4] + np.array([0.0, d, 0.0])
        volumes.append(M.check_and_measure(M.TetMesh(nodes=nodes, tets=mesh.tets, size_mm=1.0))[1])
    assert volumes[1] == pytest.approx(straight)
    assert volumes[2] - volumes[1] == pytest.approx(volumes[1] - volumes[0], rel=1e-12)  # linear in d
    assert volumes[2] != volumes[1]


# --------------------------------------------------------------------------- boundary, integrals


def test_boundary_of_one_tetrahedron_has_four_outward_facets_of_the_hand_area():
    mesh = one_tet()
    facets = M.boundary_facets(mesh)
    assert len(facets.areas) == 4
    assert facets.areas.sum() == pytest.approx(150.0 + 86.60254038, abs=1e-6)
    centre = mesh.nodes[:4].mean(axis=0)
    for k in range(4):
        middle = mesh.nodes[facets.corners[k]].mean(axis=0)
        assert float(np.dot(facets.normals[k], middle - centre)) > 0.0  # outward


def test_two_tetrahedra_sharing_a_face_have_six_boundary_facets():
    corners = np.array([[0, 0, 0], [10, 0, 0], [0, 10, 0], [0, 0, 10], [10, 10, 10]], dtype=float)
    # tetrahedra (0,1,2,3) and (1,2,3,4) share the face 1-2-3; node numbers 0..4, then the midside nodes of each, appended
    nodes = [*corners]
    tets = []
    for quad in ((0, 1, 2, 3), (1, 3, 2, 4)):
        row = list(quad)
        for a, b in M._MID_EDGES:
            mid = (corners[quad[a]] + corners[quad[b]]) / 2
            for k, p in enumerate(nodes):
                if np.allclose(p, mid):
                    row.append(k)
                    break
            else:
                nodes.append(mid)
                row.append(len(nodes) - 1)
        tets.append(row)
    mesh = M.TetMesh(nodes=np.array(nodes), tets=np.array(tets), size_mm=10.0)
    assert len(M.boundary_facets(mesh).areas) == 6


def test_nodal_shares_of_a_straight_facet_are_zero_at_corners_and_a_third_at_midsides():
    mesh = one_tet()
    facets = M.boundary_facets(mesh)
    k = int(np.argmin([mesh.nodes[c][:, 2].max() for c in facets.corners]))  # the base facet z = 0
    scalar, vector, area = M.facet_integrals(mesh, facets, np.array([k]))
    assert area[0] == pytest.approx(50.0, rel=1e-12)
    assert scalar[0, :3] == pytest.approx([0.0, 0.0, 0.0], abs=1e-12)
    assert scalar[0, 3:] == pytest.approx([50.0 / 3.0] * 3, rel=1e-12)
    assert vector[0].sum(axis=0) == pytest.approx([0.0, 0.0, -50.0], abs=1e-9)  # area vector: 50 mm^2 pointing down


def test_moving_a_midside_node_along_its_straight_edge_keeps_the_area_and_the_total_share():
    mesh = one_tet()
    nodes = mesh.nodes.copy()
    nodes[4] = nodes[0] + 0.62 * (nodes[1] - nodes[0])  # edge 0-1 midside node at 0.62 instead of 0.5: same straight boundary
    moved = M.TetMesh(nodes=nodes, tets=mesh.tets, size_mm=1.0)
    facets = M.boundary_facets(moved)
    for k in range(4):
        scalar, vector, area = M.facet_integrals(moved, facets, np.array([k]))
        assert scalar.sum() == pytest.approx(area[0], rel=1e-12)
        assert np.linalg.norm(vector[0].sum(axis=0)) == pytest.approx(area[0], rel=1e-9)  # a flat facet: |area vector| = area
    assert facets.areas.sum() == pytest.approx(150.0 + 86.60254038, abs=1e-6)


def test_the_two_pieces_of_a_mesh_are_counted():
    a = one_tet()
    nodes = np.vstack([a.nodes, a.nodes + 100.0])
    tets = np.vstack([a.tets, a.tets + 10])
    assert M.connected_components(M.TetMesh(nodes=nodes, tets=tets, size_mm=1.0)) == 2
    assert M.connected_components(a) == 1


# --------------------------------------------------------------------------- limits


def test_the_element_estimate_and_the_limits():
    # a 10 x 10 x 100 bar (V = 10000 mm^3, A = 2 (1000 + 1000 + 100) = 4200 mm^2) at 5 mm: 4 * 10000 / 125 + 1.7 * 4200 / 25 = 320 + 285.6 elements
    assert M.estimate_elements(10000.0, 5.0, 4200.0) == pytest.approx(605.6, abs=1e-3)
    assert M.estimate_elements(10000.0, 5.0) == pytest.approx(320.0, abs=1e-3)  # no surface term without an area
    bbox = ((0.0, 0.0, 0.0), (100.0, 10.0, 10.0))
    assert M.check_before_meshing(10000.0, *bbox, 5.0) == pytest.approx(605.6, abs=1e-3)  # the bounding box surface stands in for the area
    assert M.check_before_meshing(10000.0, *bbox, 5.0, 4200.0) == pytest.approx(605.6, abs=1e-3)
    with pytest.raises(ToolFailure) as caught:
        M.check_before_meshing(10000.0, *bbox, 1e-9)
    assert caught.value.family == "input.too_large" and caught.value.details["param"] == "/element_size"
    assert "1/1000" in caught.value.message  # reject the size before evaluating h**3
    with pytest.raises(ToolFailure) as caught:
        M.check_before_meshing(10000.0, *bbox, 0.5)  # 4 * 10000 / 0.125 = 320 000 elements and more
    assert caught.value.family == "input.too_large" and caught.value.details["estimated_elements"] > M.MAX_ELEMENTS
    with pytest.raises(ToolFailure) as caught:
        M.check_before_meshing(0.01, (0.0, 0.0, 0.0), (1000.0, 0.01, 0.01), 0.09)  # few elements by volume, but 0.09 < diagonal / 1000
    assert caught.value.family == "input.too_large" and "1/1000" in caught.value.message
    with pytest.raises(ToolFailure) as caught:
        M.check_before_meshing(10000.0, *bbox, 1000.0)
    assert caught.value.family == "input.out_of_range"
    with pytest.raises(ToolFailure) as caught:
        M.check_before_meshing(1e-18, (0.0,) * 3, (1e-6,) * 3, 1e-7)  # a part of a nanometre
    assert caught.value.family == "input.out_of_range"


# --------------------------------------------------------------------------- rigid body motion


def bar_nodes():
    corners = np.array([[x, y, z] for x in (0.0, 100.0) for y in (0.0, 10.0) for z in (0.0, 10.0)])
    return np.vstack([corners, [[0.0, 5.0, 0.0], [0.0, 5.0, 10.0], [0.0, 0.0, 5.0], [0.0, 10.0, 5.0], [0.0, 5.0, 5.0]]])


def test_nothing_fixed_leaves_all_six_motions_free():
    nodes = bar_nodes()
    assert len(V.free_rigid_motions(nodes, np.zeros((len(nodes), 3), dtype=bool))) == 6


def test_a_fixed_face_holds_the_body_and_ux_alone_does_not():
    nodes = bar_nodes()
    face = nodes[:, 0] == 0.0
    fixed = np.zeros((len(nodes), 3), dtype=bool)
    fixed[face] = True
    assert V.free_rigid_motions(nodes, fixed) == []
    only_ux = np.zeros_like(fixed)
    only_ux[face, 0] = True
    free = V.free_rigid_motions(nodes, only_ux)
    assert len(free) == 3  # y and z translation and rotation about x; ux = 0 on a plane takes the other three
    assert any("translation along y" in f for f in free) or any("translation" in f for f in free)


def test_the_minimal_constraints_of_the_bar_hold_it():
    nodes = bar_nodes()
    fixed = np.zeros((len(nodes), 3), dtype=bool)
    fixed[nodes[:, 0] == 0.0, 0] = True
    fixed[8, 1] = fixed[9, 1] = True  # uy at (0, 5, 0) and (0, 5, 10)
    fixed[10, 2] = True  # uz at (0, 0, 5)
    assert V.free_rigid_motions(nodes, fixed) == []
    fixed[10, 2] = False  # without uz: translation along z is free
    free = V.free_rigid_motions(nodes, fixed)
    assert len(free) == 1 and "z" in free[0]


def test_two_pinned_points_leave_the_rotation_about_the_line_through_them():
    nodes = bar_nodes()
    fixed = np.zeros((len(nodes), 3), dtype=bool)
    fixed[0] = True  # (0, 0, 0)
    fixed[4] = True  # (100, 0, 0)
    free = V.free_rigid_motions(nodes, fixed)
    assert len(free) == 1 and free[0] == "rotation about x"


# --------------------------------------------------------------------------- the deck and the readers

DECK_LINE = re.compile(
    r"^(?:"
    r"\*(?:HEADING|NODE|ELEMENT|MATERIAL|ELASTIC|SOLID SECTION|NSET|BOUNDARY|STEP|STATIC|CLOAD|NODE FILE|EL FILE|NODE PRINT|END STEP)\b[^\n]*"
    r"|eng-toolkit fem_static|U|S|RF|NALL|"
    r"[-+]?\d+(?:\.\d*)?(?:[eE][-+]?\d+)?(?:, *[-+]?\d+(?:\.\d*)?(?:[eE][-+]?\d+)?)*\.?"  # numeric lines: integers and floats, comma separated
    r")$"
)


def assert_deck_is_closed(deck: str) -> None:
    keywords = set()
    for line in deck.splitlines():
        assert DECK_LINE.match(line), f"a line outside the closed vocabulary: {line!r}"
        if line.startswith("*"):
            keywords.add(re.match(r"\*([A-Z ]+)", line).group(1).strip())
            assert re.fullmatch(r"\*[A-Z ]+(?:, (?:NSET|ELSET|TYPE|NAME|MATERIAL|TOTALS)=[A-Z0-9]+)*", line), line
    assert keywords <= {"HEADING", "NODE", "ELEMENT", "MATERIAL", "ELASTIC", "SOLID SECTION", "NSET", "BOUNDARY", "STEP", "STATIC", "CLOAD", "NODE FILE", "EL FILE", "NODE PRINT", "END STEP"}
    assert "INCLUDE" not in deck and "INPUT" not in deck


def test_the_deck_uses_only_the_closed_vocabulary_and_the_numbers_given():
    mesh = one_tet()
    fixed = np.zeros((10, 3), dtype=bool)
    fixed[0] = True
    fixed[1, 1] = True
    force = np.zeros((10, 3))
    force[2] = (0.0, 0.0, -12.5)
    deck = V.build_deck(mesh, 210000.0, 0.3, fixed, force)
    assert_deck_is_closed(deck)
    lines = deck.splitlines()
    assert "*ELEMENT, TYPE=C3D10, ELSET=EALL" in lines
    assert "1, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10" in lines
    assert tuple(float(x) for x in lines[lines.index("*ELASTIC") + 1].split(",")) == (210000.0, 0.3)
    assert "1, 1, 1, 0." in lines and "1, 3, 3, 0." in lines and "2, 2, 2, 0." in lines
    assert "3, 3, -1.250000000000e+01" in lines
    assert "2, 1, 1, 0." not in lines


def test_the_deck_refuses_a_non_finite_number():
    mesh = one_tet()
    fixed = np.zeros((10, 3), dtype=bool)
    force = np.zeros((10, 3))
    force[0, 0] = np.nan
    with pytest.raises(ToolFailure):
        V.build_deck(mesh, 210000.0, 0.3, fixed, force)
    with pytest.raises(ToolFailure):
        V.build_deck(mesh, float("inf"), 0.3, fixed, np.zeros((10, 3)))


def test_fortran_numbers_with_three_digit_exponents_are_read():
    assert V._number("1.500000E+02") == 150.0
    assert V._number("-2.5E-03") == -2.5e-3
    assert V._number("1.000000-100") == 1e-100  # Fortran drops the E when the exponent has three digits
    assert V._number("4.5+101") == 4.5e101


def test_the_dat_reader_takes_displacements_and_the_reaction_total(tmp_path):
    text = """
 displacements (vx,vy,vz) for set NALL and time  0.1000000E+01

         1  1.000000E-03  0.000000E+00 -2.000000E-03
         2  1.000000-100  5.000000E-01  0.000000E+00

 forces (fx,fy,fz) for set NSUP and time  0.1000000E+01

         1 -5.000000E+01  0.000000E+00  1.000000E+01

 total force (fx,fy,fz) for set NSUP and time  0.1000000E+01

       -5.000000E+01  0.000000E+00  1.000000E+01
"""
    path = tmp_path / "job.dat"
    path.write_text(text)
    out = V.read_dat(path, 2)
    assert out.displacement.tolist() == [[1e-3, 0.0, -2e-3], [1e-100, 0.5, 0.0]]
    assert out.reaction_total.tolist() == [-50.0, 0.0, 10.0]
    assert out.reaction_nodes.tolist() == [0]
    with pytest.raises(ToolFailure):
        V.read_dat(path, 3)  # a node is missing


def test_the_frd_reader_takes_fixed_width_columns_even_when_numbers_touch(tmp_path):
    row = " -1" + f"{1:10d}" + "".join(f"{v:12.5E}" for v in (1.0, -2.0, 3.0e-12, -4.0, 5.0, -6.0))
    path = tmp_path / "job.frd"
    path.write_text("    1C\n -4  DISP        4    1\n -1" + f"{1:10d}" + "  1.00000E+00\n -3\n -4  STRESS      6    1\n" + row + "\n -3\n")
    stress = V.read_frd_stress(path, 1)
    assert stress[0].tolist() == [1.0, -2.0, 3.0e-12, -4.0, 5.0, -6.0]


def test_von_mises_of_known_states():
    uniaxial = np.array([[100.0, 0, 0, 0, 0, 0]])
    shear = np.array([[0, 0, 0, 50.0, 0, 0]])  # tau = 50: sqrt(3) * 50
    hydrostatic = np.array([[-80.0, -80.0, -80.0, 0, 0, 0]])
    assert V.von_mises(uniaxial)[0] == pytest.approx(100.0)
    assert V.von_mises(shear)[0] == pytest.approx(50.0 * 3**0.5)
    assert V.von_mises(hydrostatic)[0] == pytest.approx(0.0, abs=1e-12)


def test_the_gmsh_reader_refuses_a_file_without_second_order_tetrahedra(tmp_path):
    path = tmp_path / "part.msh"
    path.write_text("$MeshFormat\n2.2 0 8\n$EndMeshFormat\n$Nodes\n1\n1 0 0 0\n$EndNodes\n$Elements\n1\n1 15 2 0 1 1\n$EndElements\n")
    with pytest.raises(ToolFailure) as caught:
        M.read_msh(path, 1.0)
    assert caught.value.family == "engine.failure" and "no second-order tetrahedra" in caught.value.message
    path.write_text("not a mesh")
    with pytest.raises(ToolFailure):
        M.read_msh(path, 1.0)
