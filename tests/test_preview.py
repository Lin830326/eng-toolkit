"""The PNG preview (eng_toolkit/preview.py) checked without any CAD engine: a z-buffer rasteriser, a bitmap font and a PNG encoder.

Everything is compared with hand calculations and with an independent PNG reader (tests/png_reader.py):

* A box sx x sy x sz seen from (1, -1, 1) (orthographic, true isometric): a face with unit normal n and area A covers A |n . t|
  of the picture's plane, t = (1, -1, 1) / sqrt 3, so the three visible faces cover sx sy : sx sz : sy sz (top : front : right)
  of the pixels, whatever the scale.  The faces facing away (+y, -x, -z) cover nothing.
* A closed box seen from the camera hides the three edges that meet at its far corner (0, sy, 0): drawing all twelve edges must
  give the very same picture as drawing the nine others.
* The nearer of two overlapping faces wins whatever the order of the triangles.
* No Pillow, no matplotlib: the module imports and renders in a process where those imports are blocked.
"""

import itertools
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import pytest
from png_reader import read_png

from eng_toolkit import preview as P

ROOT = Path(__file__).resolve().parents[1]

# face order of box_mesh: +x, -x, +y, -y, +z, -z (the legend group index of a triangle is its face's index)
FACE_COLOURS = [(0.9, 0.2, 0.2), (0.9, 0.5, 0.2), (0.2, 0.7, 0.2), (0.2, 0.4, 0.9), (0.9, 0.9, 0.2), (0.5, 0.2, 0.8)]
FACES = [
    ((1, 0, 0), [(1, 0, 0), (1, 1, 0), (1, 1, 1), (1, 0, 1)]),
    ((-1, 0, 0), [(0, 1, 0), (0, 0, 0), (0, 0, 1), (0, 1, 1)]),
    ((0, 1, 0), [(1, 1, 0), (0, 1, 0), (0, 1, 1), (1, 1, 1)]),
    ((0, -1, 0), [(0, 0, 0), (1, 0, 0), (1, 0, 1), (0, 0, 1)]),
    ((0, 0, 1), [(0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1)]),
    ((0, 0, -1), [(0, 1, 0), (1, 1, 0), (1, 0, 0), (0, 0, 0)]),
]


def box_mesh(sx, sy, sz, *, lines=(), markers=(), order=None, shade=0.45):
    verts, tris, normals, colours, groups = [], [], [], [], []
    for k, (normal, corners) in enumerate(FACES):
        base = len(verts)
        verts += [(c[0] * sx, c[1] * sy, c[2] * sz) for c in corners]
        normals += [normal] * 4
        tris += [(base, base + 1, base + 2), (base, base + 2, base + 3)]
        colours += [FACE_COLOURS[k]] * 2
        groups += [k] * 2
    order = list(range(len(tris))) if order is None else order
    return P.Mesh(
        np.array(verts, float),
        np.array(tris)[order],
        np.array(normals, float),
        face_rgb=np.array(colours)[order],
        tri_group=np.array(groups)[order],
        lines=tuple(lines),
        markers=tuple(markers),
        shade_strength=shade,
    )


def edge_lines(sx, sy, sz, skip_corner=None):
    corners = np.array(list(itertools.product([0, sx], [0, sy], [0, sz])), float)
    segments = []
    for a, b in itertools.combinations(range(8), 2):
        if (corners[a] != corners[b]).sum() == 1:
            if skip_corner is not None and (np.allclose(corners[a], skip_corner) or np.allclose(corners[b], skip_corner)):
                continue
            segments.append([corners[a], corners[b]])
    return P.LineSet(np.array(segments), (0.1, 0.1, 0.1), 1.0)


def sheet(mesh, **kwargs):
    return P.render_sheet(mesh, P.SheetSpec("box", ("a subtitle",), **kwargs))


# --------------------------------------------------------------------------- the PNG encoder


def test_the_png_encoder_writes_a_valid_file_that_an_independent_reader_decodes_exactly():
    rng = np.random.default_rng(7)
    for shape in ((1, 1, 3), (5, 7, 3), (40, 3, 3), (64, 64, 3)):
        image = rng.integers(0, 256, size=shape, dtype=np.uint8)
        image[:, : shape[1] // 2] = image[:1, :1]  # a flat part as well
        data = P.encode_png(image)
        width, height, decoded = read_png(data)
        assert (width, height) == (shape[1], shape[0])
        assert np.array_equal(decoded, image)
        assert P.encode_png(image) == data  # same pixels, same bytes
    with pytest.raises(ValueError):
        P.encode_png(np.zeros((4, 4), dtype=np.uint8))


def test_pillow_reads_the_png_too_when_it_is_installed():
    PIL = pytest.importorskip("PIL.Image", reason="Pillow is only a cross-check here")
    import io

    image = np.arange(24 * 17 * 3, dtype=np.uint8).reshape(24, 17, 3)
    assert np.array_equal(np.asarray(PIL.open(io.BytesIO(P.encode_png(image))).convert("RGB")), image)


# --------------------------------------------------------------------------- the rasteriser


def shaded_matches(region: np.ndarray, base) -> int:
    """Pixels of ``region`` that are ``base`` times one factor between 0.5 and 1 (any Lambert shade of that colour)."""
    base = np.asarray(base, float) * 255.0
    pixels = region.reshape(-1, 3).astype(float)
    factor = pixels @ base / (base @ base)
    off = np.abs(pixels - factor[:, None] * base).max(axis=1)
    return int(np.sum((off <= 3.0) & (factor >= 0.5) & (factor <= 1.001)))


def test_the_isometric_areas_of_a_box_follow_n_dot_t_and_the_faces_that_point_away_are_not_drawn():
    sx, sy, sz = 100.0, 60.0, 20.0
    result = sheet(box_mesh(sx, sy, sz))
    seen = result.group_pixels
    assert set(seen) == {0, 3, 4}, "+x, -y and +z face the camera at (1, -1, 1); the other three do not"
    total = sum(seen.values())
    assert result.visible_pixels == pytest.approx(total, abs=3)
    expected = {4: sx * sy, 3: sx * sz, 0: sy * sz}
    norm = sum(expected.values())
    for group, area in expected.items():
        assert seen[group] / total == pytest.approx(area / norm, rel=0.01), group
    # all three small views, the isometric one included: every one of the six faces is seen from outside somewhere? no: the
    # small views look along an axis, so only +x (right), -y (front) and +z (top) appear there as well
    assert set(sheet(box_mesh(sx, sy, sz), views=("iso", "front", "top", "right")).group_pixels_all) == {0, 3, 4}


def test_the_colour_of_each_visible_face_is_its_base_colour_times_a_lambert_factor():
    mesh = box_mesh(100.0, 60.0, 20.0)
    result = sheet(mesh)
    _, _, image = read_png(result.png)
    panel = image[84 + 22 : 84 + 640 - 4, 18 : 18 + 760 - 4]  # inside the isometric panel, below its caption
    for group in (0, 3, 4):
        found = shaded_matches(panel, FACE_COLOURS[group])
        assert found == pytest.approx(result.group_pixels[group], rel=0.02), group
    # three different faces get three different shades: the form can be read
    flat = sheet(box_mesh(100.0, 60.0, 20.0, shade=0.0))
    _, _, flat_image = read_png(flat.png)
    flat_panel = flat_image[84 + 22 : 84 + 640 - 4, 18 : 18 + 760 - 4]
    for group in (0, 3, 4):  # strength 0: the exact colours
        exact = np.all(flat_panel == np.round(np.array(FACE_COLOURS[group]) * 255), axis=2).sum()
        assert exact == pytest.approx(flat.group_pixels[group], rel=0.02)


def test_the_nearer_of_two_overlapping_faces_wins_whatever_the_triangle_order():
    def two_squares(first_is_far):
        far = [(0, 0, 0), (80, 0, 0), (80, 80, 0), (0, 80, 0)]
        near = [(50, -10, 30), (90, -10, 30), (90, 30, 30), (50, 30, 30)]  # the 40 x 40 square (20..60)^2, moved 30 along the line of sight
        verts, tris, groups = [], [], []
        for k, quad in enumerate([far, near] if first_is_far else [near, far]):
            base = len(verts)
            verts += quad
            tris += [(base, base + 1, base + 2), (base, base + 2, base + 3)]
            groups += [1 if (k == 1) == first_is_far else 0] * 2  # group 1 = near, 0 = far
        colours = np.array([(0.9, 0.1, 0.1) if g == 0 else (0.1, 0.8, 0.1) for g in groups])
        return P.Mesh(np.array(verts, float), np.array(tris), np.tile([0.0, 0.0, 1.0], (len(verts), 1)), face_rgb=colours, tri_group=np.array(groups))

    a = sheet(two_squares(True))
    b = sheet(two_squares(False))
    assert a.png == b.png
    far_only, near = a.group_pixels[0], a.group_pixels[1]
    # the near 40 x 40 square covers the middle of the far 80 x 80 one: 6400 - 1600 of it stay visible
    assert far_only / near == pytest.approx((80 * 80 - 40 * 40) / (40 * 40), rel=0.02)


def test_edges_behind_the_part_are_not_drawn_and_the_visible_ones_are_dark():
    sx, sy, sz = 100.0, 60.0, 20.0
    far_corner = (0.0, sy, 0.0)  # the corner farthest from a camera at (+x, -y, +z)
    all_twelve = sheet(box_mesh(sx, sy, sz, lines=[edge_lines(sx, sy, sz)]))
    nine = sheet(box_mesh(sx, sy, sz, lines=[edge_lines(sx, sy, sz, skip_corner=far_corner)]))
    none = sheet(box_mesh(sx, sy, sz))
    assert all_twelve.png != none.png
    _, _, image = read_png(all_twelve.png)
    _, _, plain = read_png(none.png)
    _, _, without_hidden = read_png(nine.png)
    # the hidden edges run behind the faces from the far corner to the silhouette: nothing differs in the interior of the
    # model (the few pixels where such an edge ends on the silhouette are the only ones that may differ)
    inside = (slice(84 + 22, 84 + 640 - 4), slice(18, 18 + 760 - 4))
    covered = np.any(plain != 255, axis=2)[inside]
    deep = covered.copy()
    for dy, dx in itertools.product(range(-6, 7), repeat=2):
        deep &= np.roll(np.roll(covered, dy, axis=0), dx, axis=1)
    differing = np.any(image != without_hidden, axis=2)[inside]
    assert deep.sum() > 10_000 and not (differing & deep).any(), "the three hidden edges draw nothing inside the model"
    assert differing.sum() < 80, differing.sum()
    changed = np.any(image != plain, axis=2)
    assert changed.sum() > 400
    assert image[changed].mean() < plain[changed].mean(), "the edges are darker than the faces they lie on"
    # no edge pixel outside the model's silhouette plus half a line width: the corners of the panel stay white
    assert np.all(image[84 + 30 : 84 + 40, 20:40] == 255)


def test_the_picture_is_deterministic_and_the_preview_ignores_the_order_of_the_triangles():
    lines = [edge_lines(100.0, 60.0, 20.0)]
    a = sheet(box_mesh(100.0, 60.0, 20.0, lines=lines))
    b = sheet(box_mesh(100.0, 60.0, 20.0, lines=lines))
    shuffled = sheet(box_mesh(100.0, 60.0, 20.0, lines=lines, order=list(np.random.default_rng(1).permutation(12))))
    assert a.png == b.png == shuffled.png


def test_markers_are_drawn_where_they_can_be_seen_and_a_second_view_shows_a_hidden_one():
    sx, sy, sz = 100.0, 60.0, 20.0
    on_top = P.Marker(np.array([50.0, 30.0, sz]), "top")
    on_left = P.Marker(np.array([0.0, 30.0, 10.0]), "back")  # the -x face: hidden from (+x, -y, +z)
    on_bottom = P.Marker(np.array([50.0, 30.0, 0.0]), "under")
    mesh = box_mesh(sx, sy, sz, markers=[on_top, on_left, on_bottom])
    single = P.render_sheet(mesh, P.SheetSpec("box"))
    assert single.markers_visible == (True, False, False) and single.markers_view == ("iso", None, None)
    spec = P.SheetSpec("box", back_view_for_markers=True)
    # one extra view only: the one that shows most of the hidden markers (the earlier one in iso_back, iso_under, iso_under_back on a tie)
    assert P.render_sheet(box_mesh(sx, sy, sz, markers=[on_top, on_left]), spec).markers_view == ("iso", "iso_back")
    assert P.render_sheet(box_mesh(sx, sy, sz, markers=[on_top, on_bottom]), spec).markers_view == ("iso", "iso_under")
    both = P.render_sheet(mesh, spec)  # from (-x, +y, -z) the left face and the bottom face are both seen
    assert both.markers_visible == (True, True, True) and both.markers_view == ("iso", "iso_under_back", "iso_under_back")
    only_top = P.render_sheet(box_mesh(sx, sy, sz, markers=[on_top]), P.SheetSpec("box", back_view_for_markers=True))
    assert only_top.markers_view == ("iso",), "no second view when nothing is hidden"


def test_a_view_that_does_not_start_with_the_isometric_one_is_refused():
    for views in ((), ("front",), ("iso", "bogus"), ("iso", "iso_back")):
        with pytest.raises(ValueError):
            P.render_sheet(box_mesh(1.0, 1.0, 1.0), P.SheetSpec("x", views=views))


def test_a_mesh_that_cannot_be_drawn_is_refused_with_a_value_error():
    good = box_mesh(1.0, 1.0, 1.0)
    nan_vertices = good.vertices.copy()
    nan_vertices[3, 1] = np.nan
    cases = {
        "a NaN vertex": P.Mesh(nan_vertices, good.triangles, good.normals, face_rgb=good.face_rgb),
        "no triangle": P.Mesh(good.vertices, good.triangles[:0], good.normals, face_rgb=good.face_rgb[:0]),
        "no vertex": P.Mesh(good.vertices[:0], good.triangles, good.normals, face_rgb=good.face_rgb),
        "no colour": P.Mesh(good.vertices, good.triangles, good.normals),
        "two colourings": P.Mesh(good.vertices, good.triangles, good.normals, face_rgb=good.face_rgb, vertex_rgb=np.zeros((24, 3))),
    }
    for what, mesh in cases.items():
        with pytest.raises(ValueError):
            P.render_sheet(mesh, P.SheetSpec("x"))


def test_a_deadline_callback_can_stop_the_render():
    class Stop(Exception):
        pass

    def check(phase):
        raise Stop(phase)

    with pytest.raises(Stop):
        P.render_sheet(box_mesh(1.0, 1.0, 1.0), P.SheetSpec("x"), check=check)


def uv_sphere(n, radius=60.0):
    u = np.linspace(0, 2 * np.pi, n + 1)[:-1]
    v = np.linspace(0, np.pi, n // 2 + 1)
    uu, vv = np.meshgrid(u, v)
    points = np.stack([radius * np.sin(vv) * np.cos(uu), radius * np.sin(vv) * np.sin(uu), radius * np.cos(vv)], -1).reshape(-1, 3)
    index = np.arange(len(points)).reshape(n // 2 + 1, n)
    nxt = list(range(1, n)) + [0]
    a, b, c, d = index[:-1, :], index[:-1, nxt], index[1:, :], index[1:, nxt]
    triangles = np.concatenate([np.stack([a, c, b], -1).reshape(-1, 3), np.stack([b, c, d], -1).reshape(-1, 3)])
    return points, triangles, points / radius


def test_a_sphere_of_600000_triangles_renders_in_a_few_seconds_with_bounded_memory_and_the_right_area():
    import tracemalloc

    points, triangles, normals = uv_sphere(776)
    assert len(triangles) >= 600_000
    mesh = P.Mesh(points, triangles, normals, face_rgb=np.tile([0.34, 0.71, 0.91], (len(triangles), 1)))
    tracemalloc.start()
    started = time.perf_counter()
    result = sheet(mesh)
    seconds = time.perf_counter() - started
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    assert seconds < 10.0, seconds
    assert peak < 1.2e9, peak  # the mesh itself is about 100 MB
    # a sphere's silhouette is a disc of the sphere's radius: pi r^2 in pixels, r = scale x 60 mm; the panel fits the diameter
    # into its height: (640 - 22 - 20) pixels
    radius_px = (640 - 22 - 20) / 2
    assert result.visible_pixels == pytest.approx(np.pi * radius_px**2, rel=0.01)
    _, _, image = read_png(result.png)
    assert image.shape[1] == 1200


def test_the_module_needs_neither_pillow_nor_matplotlib():
    code = (
        "import sys\n"
        f"sys.path.insert(0, {str(ROOT)!r})\n"
        "for name in ('PIL', 'PIL.Image', 'matplotlib', 'scipy'):\n"
        "    sys.modules[name] = None\n"  # an import of any of them now raises ImportError
        "import numpy as np\n"
        "from eng_toolkit import preview as P\n"
        "v = np.array([[0, 0, 0], [10, 0, 0], [0, 10, 0], [0, 0, 10]], float)\n"
        "t = np.array([[0, 2, 1], [0, 1, 3], [0, 3, 2], [1, 2, 3]])\n"
        "n = np.tile([0, 0, 1.0], (4, 1))\n"
        "r = P.render_sheet(P.Mesh(v, t, n, face_rgb=np.full((4, 3), 0.5)), P.SheetSpec('tet', ('sub',), legend=(P.LegendItem('x', (1, 2, 3), 'y'),)))\n"
        "assert r.png[:8] == b'\\x89PNG\\r\\n\\x1a\\n' and r.visible_pixels > 100\n"
        "print('ok', sorted(m for m in sys.modules if sys.modules[m] is not None and m.split('.')[0] in ('PIL', 'matplotlib', 'scipy')))\n"
    )
    done = subprocess.run([sys.executable, "-I", "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=120, env={"SYSTEMROOT": "C:\\Windows"})
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "ok []"


# --------------------------------------------------------------------------- the text


def test_every_printable_ascii_character_has_a_glyph_and_the_glyphs_differ():
    shapes = {}
    for code in range(32, 127):
        ch = chr(code)
        bitmap = P._BITMAPS[ch]
        assert bitmap.shape == (9, 5)
        assert ch == " " or bitmap.any(), ch
        shapes.setdefault(bitmap.tobytes(), []).append(ch)
    assert [chars for chars in shapes.values() if len(chars) > 1] == [], "two characters share one bitmap"


def test_text_is_inked_on_the_glyph_pixels_scaled_and_clipped_at_the_edge():
    img = np.full((30, 40, 3), 255, dtype=np.uint8)
    width = P.draw_text(img, 2, 3, "I", (10, 20, 30), scale=1)
    assert width == 5
    ink = np.all(img == (10, 20, 30), axis=2)
    assert ink.sum() == P._BITMAPS["I"].sum()
    assert ink[3 : 3 + 9, 2 : 2 + 5].tolist() == P._BITMAPS["I"].tolist()
    big = np.full((30, 40, 3), 255, dtype=np.uint8)
    P.draw_text(big, 0, 0, "I", (0, 0, 0), scale=2)
    assert (big[:, :, 0] != 255).sum() == 4 * P._BITMAPS["I"].sum()
    assert P.text_width("MPa", 2) == (3 * 6 - 1) * 2
    P.draw_text(big, 35, 25, "hello", (0, 0, 0), scale=3)  # runs over the edge: no exception, nothing outside the image


def test_characters_without_a_glyph_become_a_question_mark_and_a_few_are_transliterated():
    assert P._plain("\u00b5m \u00d7 \u00b0") == "um x  deg"
    assert P._plain("caf\u00e9 \u4e2d") == "caf? ?"
    lines = P._wrap("a" * 100 + " tail", 120, 2)
    assert all(len(line) <= 10 for line in lines) and "".join(lines).replace(" ", "").endswith("tail")


def test_a_long_legend_and_long_notes_still_make_a_valid_png():
    items = tuple(P.LegendItem(f"group number {k} with a long name that has to wrap", (10 * k, 200 - 10 * k, 90), "face " * 30) for k in range(8))
    result = sheet(box_mesh(10.0, 10.0, 10.0), legend=items, notes=("note " * 400,), info=("x" * 500,))
    width, height, _ = read_png(result.png)
    assert (width, height) == (result.width, result.height) and height <= P.MAX_CANVAS[1]
