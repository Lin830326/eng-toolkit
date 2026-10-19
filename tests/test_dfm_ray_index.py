"""Differential checks against the whole-shape OCCT ray query."""
import numpy as np
import pytest

pytest.importorskip('OCP')
from eng_toolkit.brep._query import _array, _bbox, _hits, _IndexedIntersector, _load_intersector
import cad_fixtures as cad
import dfm_fixtures as fx


@pytest.mark.parametrize('builder', [fx.wall_block, fx.hollow_block, fx.tube,
    fx.flat_sheet, fx.bent_sheet, lambda: fx.comb(8), cad.drill_point_part,
    cad.groove_part, cad.counterbore_part, lambda: cad.split_faces(fx.tube())])
def test_indexed_ray_hits_match_whole_shape(builder):
    shape = builder()
    lo, hi = map(np.array, _bbox(shape))
    reach = float(4 * np.linalg.norm(hi - lo))
    indexed, original = _IndexedIntersector(shape), _load_intersector(shape)
    rng = np.random.default_rng(1977)
    for _ in range(100):
        start = rng.uniform(lo - 1, hi + 1)
        direction = rng.normal(size=3)
        direction /= np.linalg.norm(direction)
        assert _hits(indexed, start, direction, reach) == _hits(original, start, direction, reach)


@pytest.mark.parametrize(('origin', 'direction', 'reach', 'expected'), [
    ((-1, 10, 15), (1, 0, 0), 12., [1., 11.]),
    ((-1, 10, 15), (1, 0, 0), 1., [1.]),
    ((11, 10, 15), (-1, 0, 0), 12., [1., 11.]),
    ((-1, 0, 0), (1, 0, 0), 12., [1., 11.]),
    ((5, 10, -1), (0, 0, 1), 32., [1., 31.]),
    ((-1, 21, 15), (1, 0, 0), 12., []),
])
def test_box_edges_and_finite_ray_endpoints(origin, direction, reach, expected):
    query = _IndexedIntersector(cad.box(length=10, width=20, height=30))
    assert _hits(query, np.array(origin), np.array(direction), reach) == expected


def test_located_curved_faces_and_near_tangent_rays():
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
    from OCP.gp import gp_Ax1, gp_Dir, gp_Pnt, gp_Trsf, gp_Vec

    transform = gp_Trsf()
    transform.SetRotation(gp_Ax1(gp_Pnt(0, 0, 0), gp_Dir(1, 2, 3)), 0.37)
    transform.SetTranslationPart(gp_Vec(1e5, -2e5, 3e5))
    shape = BRepBuilderAPI_Transform(fx.flat_sheet(), transform, False).Shape()
    indexed, original = _IndexedIntersector(shape), _load_intersector(shape)
    for x in (6., 8.5 - 1e-8, 8.5, 8.5 + 1e-8, 10.):
        point = _array(gp_Pnt(x, 15, -1).Transformed(transform))
        direction = _array(gp_Vec(0, 0, 1).Transformed(transform))
        assert _hits(indexed, point, direction, 4.) == _hits(original, point, direction, 4.)


def test_edge_tolerances_are_included_in_pruning_boxes():
    from OCP.BRep import BRep_Builder
    from OCP.TopAbs import TopAbs_EDGE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    shape = cad.box(length=10, width=20, height=30)
    explorer = TopExp_Explorer(shape, TopAbs_EDGE)
    builder = BRep_Builder()
    while explorer.More():
        builder.UpdateEdge(TopoDS.Edge(explorer.Current()), 1e-3)
        explorer.Next()
    indexed, original = _IndexedIntersector(shape), _load_intersector(shape)
    for y in (-1e-3, -1e-6, -1e-8, 0., 1e-8):
        point, direction = np.array((-1., y, 15.)), np.array((1., 0., 0.))
        assert _hits(indexed, point, direction, 12.) == _hits(original, point, direction, 12.)


def test_cylinder_grouping_keeps_split_faces_and_tolerance_boundaries(monkeypatch):
    from eng_toolkit.brep import _query

    def cylinder(face_id, location, axis, radius=1., span=np.pi):
        return _query._Cylinder(face_id, np.array(axis), np.array(location), radius, span,
                                -5. if axis[2] < 0 else 0., 0. if axis[2] < 0 else 5.)

    cylinders = [
        cylinder(1, (0., 0., 0.), (0., 0., 1.)),
        cylinder(2, (0.5e-6, 0., 0.), (0., 0., -1.), radius=1. + 0.5e-6),
        cylinder(3, (2e-6, 0., 0.), (0., 0., 1.), span=2 * np.pi),
        cylinder(4, (0., 0., 0.), (0., 1e-5, np.sqrt(1 - 1e-10))),
        cylinder(5, (0., 0., 0.), (0., 0., 1.), radius=1. + 2e-6, span=2 * np.pi),
        cylinder(6, (1e9, 0., 0.), (0., 0., 1.), span=2 * np.pi),
    ]
    monkeypatch.setattr(_query, '_bore', lambda c, *_: (c.origin + c.t1 * c.axis, -c.axis, True))
    holes = _query._find_holes(cad.box(), cylinders, 100.)
    assert [h.face_ids for h in holes] == [(1, 2), (3,), (6,), (5,)]
    assert [h.diameter_mm for h in holes] == [2., 2., 2., 2.000004]
    assert holes[0].entry_mm == (0., 0., 5.)


@pytest.mark.parametrize('builder', [lambda: fx.hole_grid(4, 4), lambda: fx.comb(8), fx.bent_sheet])
def test_samples_keep_the_original_triangle_ranking_and_arithmetic(builder):
    from OCP.BRep import BRep_Tool
    from OCP.TopLoc import TopLoc_Location
    from eng_toolkit.brep._dfm import _Samples
    from eng_toolkit.brep._query import _faces

    shape = builder()
    faces = _faces(shape)
    samples = _Samples(shape, faces, 0.1)
    for face_id, face in enumerate(faces, 1):
        location = TopLoc_Location()
        mesh = BRep_Tool.Triangulation_s(face, location)
        ranked = []
        if mesh is not None:
            nodes = [_array(mesh.Node(i).Transformed(location.Transformation())) for i in range(1, mesh.NbNodes() + 1)]
            for i in range(1, mesh.NbTriangles() + 1):
                p, q, r = (nodes[k - 1] for k in mesh.Triangle(i).Get())
                ranked.append((0.5 * float(np.linalg.norm(np.cross(q - p, r - p))), (p + q + r) / 3.0))
        ranked.sort(key=lambda item: -item[0])
        np.testing.assert_array_equal(samples.of(face_id, 12), [point for _, point in ranked[:12]])


def test_hole_edge_search_skips_provably_distant_faces_and_keeps_the_limit(tmp_path, monkeypatch):
    from eng_toolkit import brep
    from eng_toolkit.domains.geometry.dfm import _nearest_edge_face

    path = cad.write_brep(fx.flat_sheet(), tmp_path / 'flat.brep')
    solid = brep.read_solid(path)
    inv = brep.inventory(solid)
    candidates = [f for f in inv.faces if f.kind == 'plane' and abs(f.normal[2]) < 0.5]
    calls = []
    original = brep.distance

    def distance(*args, **kwargs):
        calls.append(args)
        return original(*args, **kwargs)

    monkeypatch.setattr(brep, 'distance', distance)
    assert _nearest_edge_face(solid, inv, inv.holes[0], candidates, max_distance_mm=3.) is None
    assert calls == []
    value, face_id = _nearest_edge_face(solid, inv, inv.holes[0], candidates, max_distance_mm=3.5)
    assert value == pytest.approx(3.5, abs=1e-9)
    assert inv.faces[face_id - 1].origin_mm[0] == 0.
    assert len(calls) == 1
