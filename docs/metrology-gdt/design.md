# metrology GD&T tools: design, algorithms, accuracy, limits

Scope: two tools added to the `metrology` domain (now 6 of the 6 tool budget), NumPy only, no new dependency, no new extra.

| tool | answers |
|---|---|
| `metrology_form_error` | flatness, straightness, circularity, cylindricity of a feature from measured points |
| `metrology_position_tolerance` | true position of features (nominal vs measured) with MMC / LMC bonus tolerance |

Code: `form.py` (geometry, pure NumPy, mm), `position.py` (arithmetic), `pointio.py` (text of point lists), `gdt_tools.py` (FastMCP wrappers, unit gatekeeper, contract). Tests: `tests/test_metrology_form.py`, `test_metrology_form_error_tool.py`, `test_metrology_position.py`.

## Contract of the results

Both tools return the unified contract with `review_status: unreviewed`. All inputs that are lengths cross `units.parse_quantity`; coordinate lists carry no unit, so the caller names it once in `unit` (a bare unit, `"2 mm"` is rejected as in `check_scan`). Point files go through `JobHandle.input_file` (opened once, the tool reads the checked copy, `provenance.input_files` records path, size, sha256).

## The one rule about honesty

Every `form_error` value is the width of a zone (pair of lines / planes / circles / cylinders) that **really contains every point**, so it is an **upper bound** of the minimum-zone form error. A part that passes with it passes with the true minimum zone. `data.method.exact` is `true` only where the value is proven to be the minimum; otherwise a failing `tolerance_check` carries `conclusive: false`. The least-squares zone is returned beside it (`least_squares_form_error`) so the caller sees the difference between the two references (ISO 12181 names them MZC and LSC).

| feature | estimator | `exact` | method |
|---|---|---|---|
| straightness (x,y) | minimum zone, strip between two parallel lines | always | convex hull + rotating calipers; closed form |
| flatness (x,y,z) | minimum zone, slab between two parallel planes, distances perpendicular to the planes | n ≤ 24 distinct points | see below |
| circularity (x,y; or x,y,z projected) | minimum zone annulus (MZC) | n ≤ 32 distinct points | see below |
| cylindricity (x,y,z) | coaxial cylinders, axis searched | never | see below |

For flatness and circularity above the exhaustive size the answer is "polished and optimality-checked" (`first_order_optimal: true`), not proven globally minimal.

## Algorithms

### Straightness

Planar profile. The thinnest strip has one side on an edge of the convex hull of the points (Andrew monotone chain), and the other on the farthest hull vertex; the calipers sweep visits each edge once, O(n log n). No tolerance on direction: the strip is free to rotate (this is the minimum-zone straightness of a planar profile; the cylindrical zone of a line in space is not implemented).

### Flatness

Width of the slab for a unit normal `n` is `max(n·p) - min(n·p)`; flatness is its minimum over `n`. At the optimum four points are in contact: three on one plane and one on the other, or two edges (2 + 2) that cross when seen along `n`. So every candidate normal is the normal of a face (3 points) or the cross product of two disjoint edges (2 + 2 points), and the candidate is **scored against all points**, never against a subset, which makes each candidate a valid zone.

* n ≤ 24: every point is in the pool, all faces and all disjoint edge pairs are scored. The result is the global minimum.
* n > 24: nested golden-section search of `f(a,b)/sqrt(1+a²+b²)` (the slab of the plane `z = ax+by+c` in the principal-axis frame, perpendicular distance) in a bracket that is proven to contain the optimum (`|tilt| ≤ 6·spread/footprint`), then repeatedly: take the 8 extreme points on each side of the current plane (a row of tied points is spread with a farthest-point pick), enumerate all candidate normals of those 16, score the best against all points (the pool value is a lower bound, so the full scoring stops as soon as it cannot win), move there; stop when no improvement. The golden-section plane is kept if it beats the enumeration.
* `first_order_optimal` is the KKT condition of the width: seen along `n`, the contact set of the upper plane and the contact set of the lower plane must overlap (the origin lies in the hull of the differences of their projections). It is necessary for a local minimum, not sufficient for a global one.
* Refused: points whose spread about the best-fit plane exceeds half their thinnest planar extent (`geometry.degenerate`), because the bracket and the meaning of flatness both fail there.

The slab is measured **perpendicular** to the planes (not along z), which matters for steep planes: test with a plane at 70° has the vertical range 2.9 times the perpendicular one.

### Circularity

Width of the annulus about a centre `c` is `max|p-c| - min|p-c|`; circularity is its minimum over `c`. The optimum is fixed by four points: 2 outer and 2 inner (centre = intersection of the two perpendicular bisectors), 3 on one circle (circumcentre), or the degenerate forms in which an outer and an inner point lie on one ray from the centre (a bisector and a line through the two points). The KKT condition is `sum(l_k e_k) = sum(m_j e_j)` with unit vectors `e` from the contact points to the centre, so 3 outer + 1 inner without a collinear pair is not stationary. All these centres are scored against all points.

* n ≤ 32: every point is both an outer and an inner candidate. Global minimum.
* n > 32: start at the geometric least-squares circle (Gauss-Newton from the algebraic fit) and at 8 ring points half a least-squares-zone away; from each, repeat: the 10 farthest and 10 nearest points → candidate centres → best scored on all points; then a last look at 24 random centres at three scales around the winner. KKT is checked on the contact points.
* x,y,z points are projected onto their least-squares plane first; `data.out_of_plane_spread_mm` is reported and a warning is raised when it exceeds 1% of the section size.

### Cylindricity

For a fixed axis direction the problem is circularity of the points projected along it (the axis has 4 parameters: 2 for its direction, searched below, and 2 for its position, which the circularity solver finds). The direction is searched by a compass search over two angles, starting from the 3 principal axes and 24 Fibonacci directions (so a short fat cylinder, whose principal axes are misleading, still gets the right axis), the best 2 refined. To stay linear in the number of points, the search runs on a seeded subset of 400 points; every point outside the zone found is added (60 worst per round) and the search resumes from the axis found, until none is outside (a constraint-generation loop: the true optimum is fixed by 6 points). The reported zone is evaluated on all points. A zone that holds all points can never be thinner than the true minimum, so the value is an upper bound; it is never claimed to be minimal. A fine step of 1e-9 rad is the stopping point.

Not done: an exact minimum-zone cylinder (a 6-point nonlinear problem; no robust closed algorithm without an LP/NLP solver, and scipy was not added).

### True position

`error = 2·|measured − nominal|`: the diameter of the circle (x,y) or sphere (x,y,z, SØ) around the nominal point that holds the measured point. Allowed = tolerance + bonus. Bonus (ISO 2692), never below 0:

| modifier | hole (MMC = smallest) | shaft (MMC = largest) |
|---|---|---|
| M | size − MMC | MMC − size |
| L | LMC − size | size − LMC |

A size beyond its material limit gets bonus 0 and a warning (it fails its size tolerance); a size outside `[mmc_size, lmc_size]` when both are given is flagged. The pass test is `error ≤ allowed·(1 + 1e-9) + 4 ulp(max coordinate)`: a deviation of 0.03 / 0.04 against a tolerance of 0.1 comes out one ulp over in binary floating point and must not fail, and at 1e6 mm the coordinates themselves are rounded at 1.2e-10.

## Accuracy claims and the limits measured

All checks are in the tests; independent oracles share no code with the module.

* **Closed forms, exact to rounding:** tilted-plane flatness with ±0.05 pattern = 0.10 (to 1e-12, also at origin offsets of 1e6 mm, where the data resolution is 1.2e-10), unit square with one corner lifted by h = h/√(4+2h²) (to 1e-12 relative), straightness (0,0)(10,0.3)(20,0) = 0.3, ideal circles (any centre, 5 to 300 points, partial arc) < 1e-9, six points at R±0.1 = 0.2, ellipse (10, 9.5) every degree = 0.5, circles r 10 / 10.3 at z 0 / 30 (cone) = 0.3 within 1e-8, perfect cylinder in an arbitrary pose < 1e-6 (measured 3e-8 on 200 points of L = 40), elliptical cylinder (10, 9.8) = 0.2 within 1e-6.
* **Brute force:** flatness vs scoring every face and every edge pair of the whole set (8 random sets of 5–10 points, 1e-10 relative); straightness vs every pair of points (10 sets); circularity vs an independent zoom grid search of the centre (12 random sets, within 1e-6 and never above the grid result); pooled search vs the exhaustive enumeration forced on larger sets: **flatness 0 mismatches in 60 random sets of 26–60 points; circularity 0 in 60 random sets of 36–100 points** (relative 1e-9), plus tie-heavy cases (a 400-point checkerboard, a 48-point 3-lobe circle).
* **Precision with large coordinates:** the answer is limited by the representation of the input, not by the algorithm. An ideal circle of radius 10 has circularity 3.6e-15 at the origin, 1.3e-13 at an offset of 1e3, 1.3e-10 at 1e6 (the spacing of doubles there is 1.2e-10) and 1.6e-8 at 1e8. A value within 10 spacings of the largest coordinate is reported with a warning "treat as zero".
* **Speed** (one core, a laptop; ±50% depending on load): flatness 1000 / 10⁴ / 10⁵ points 0.1 / 0.2 / 1.1 s; circularity 1000 / 10⁵ / 5·10⁵ points 1.2 / 3.8 / 24 s (the cap is 250 000); straightness 5·10⁵ points 1.3 s; cylindricity 500 / 5000 / 20 000 / 10⁵ points 3.7 / 5.7 / 7.8 / 11 s; the exhaustive sizes (≤ 24 / ≤ 32) take under a second.

Caps (`input.too_large`): flatness 100 000 distinct points, straightness 500 000, circularity 250 000, cylindricity 100 000; rows read from text 600 000; point file 64 MiB.

## Errors and warnings

| situation | family |
|---|---|
| unknown feature, unit with a number, ragged rows, wrong number of values per row, NaN / inf / text / bool value (row and column named), both or neither of `points` / `points_path` | `input.invalid` |
| empty list | `input.missing` |
| fewer distinct points than the feature needs (flatness 4, straightness 3, circularity 4, cylindricity 6; duplicates counted once and named) | `input.too_few_points` |
| collinear points; coplanar points for a cylinder; a cloud that is not a surface for flatness | `geometry.degenerate` |
| over the caps | `input.too_large` |
| unreadable path, wrong extension, path outside `ENG_TOOLKIT_READ_DIRS` | `input.file_not_found`, `input.unsupported_format`, `input.path_not_allowed` |
| unit problems, negative position tolerance, bad modifier / kind, size count mismatch | `units.*`, `input.invalid`, `input.missing` |

The minimum counts are those where the reference shape can fit the points exactly (3 points define a plane, a circle; a cylinder has 5 parameters) so the form error would be 0 by construction. Warnings: duplicates dropped, sparse points (fewer than 4× the minimum) under-estimate the surface between them, value at the resolution of the coordinates, 3-D section not planar, the upper-bound statement for unproven zones, bonus beyond material limit, sizes outside limits, ignored arguments.

## Decisions and alternatives

1. **Minimum zone, not least squares, as the headline.** ISO 1101 defines the zone as the minimum; least squares is a different reference and is widely mistaken for it. Both are returned; the least-squares zone is the wider.
2. **Vertex enumeration with full-set scoring instead of an LP solver.** The flatness LP (and the annulus problem, which is not an LP) would need scipy. Enumerating the candidates a 4-point basis can fix needs only NumPy, is exact for small sets, and each candidate is a valid zone, so a failure of the search can never produce a value below the true minimum. Alternative rejected: a Remez-style exchange for the vertical (z-direction) Chebyshev plane; it optimises the wrong quantity for steep planes (vertical instead of perpendicular distance).
3. **Perpendicular distance for flatness.** A plane tilted 70° has a vertical range 2.9× its flatness; most "flatness" scripts get this wrong.
4. **Cylindricity labelled an upper bound.** An exact minimum-zone cylinder needs a nonlinear solver; the axis search is cheap and always yields a legal zone, and the result states it is not minimal.
5. **Coordinates unit-less with one `unit` parameter** (like `check_scan`) instead of a unit on every number: lists of thousands of points must stay JSON/CSV.
6. **Zones, not verdicts on the drawing.** The tools compute zones and compare them with the number given; whether that number is the right requirement (envelope, datum, modifier) is the user's call, and a standing warning says so.

## What was not done

* Datum shift, datum reference frame construction, composite position, projected tolerance zone, position at RFS vs. size interaction beyond the MMC / LMC bonus, runout, profile, parallelism / perpendicularity / angularity.
* A cylindrical-zone straightness of a line in space, and straightness along the generatrix of a cylinder.
* Exact minimum-zone cylinder; exhaustive-certified flatness and circularity above 24 / 32 points (the KKT check and the 0 mismatches in 120 comparisons are evidence, not proof).
* Filtering (Gaussian / UPR for roundness), probe-radius compensation, outlier removal, measurement uncertainty.
* Ellipse-fitting and the other ISO 12181 references (maximum inscribed, minimum circumscribed circle).
* A real `claude -p --mcp-config` call was not made here; the stdio server was exercised with a hand-written JSON-RPC client (initialize, `tools/list`, two `tools/call`s).
