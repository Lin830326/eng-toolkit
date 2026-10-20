# geometry_make_part round 2: pattern, mirror, shell, path profiles, taper, of: selectors, import

Slice S10, part one, of [design.md](design.md). Round 1 ([make-part-eval.md](make-part-eval.md)) ended with a list of things the spec language could not say; this round adds them. The optional PMI read of the same slice is not part of it. Nothing here evaluates anything beyond what the `Resolver` already allowed (unit strings and arithmetic on params), nothing executes code, and the only file read is the `import` path, opened through `JobHandle.input_file` (so `ENG_TOOLKIT_READ_DIRS`, the size cap, the copy and the sha256 in `provenance.input_files` all apply).

## What was added

| feature | where | fields |
| --- | --- | --- |
| `pattern` (modifier) | new op | `of`*, `count`*, `step` (linear) or `angle` (polar), `center`, `axis` |
| `mirror` (modifier) | new op | `plane`*, `offset`, `of` |
| `shell` (modifier) | new op | `faces`* (face selector), `thickness`* |
| `import` (body) | new op | `file`*, `at`, `mode` |
| path profile | new profile `{"path": {...}}` for `extrude` and `revolve` | `start`*, `segments`* of `{to}`, `{to, radius, turn}`, `{to, via}` |
| taper | new field of `extrude` | `taper` (0 to 45 deg) |
| `of:NAME` | new selector term, in edge selectors (`fillet`, `chamfer`) and face selectors (`shell`) | |

`MAX_PATTERN_COUNT` 256 instances per pattern, `MAX_PATTERN_COPIES` 500 copies per spec, `MAX_PATH_SEGMENTS` 64, hole positions still 1000 in all (a pattern of holes counts every copy). All are refused at parse time with `details.limit`.

### Grammar by example

```json
{"op": "hole", "name": "h", "at": [["10 mm", "10 mm", "8 mm"]], "diameter": "5 mm"}
{"op": "pattern", "of": "h", "count": 6, "step": ["20 mm", "0 mm", "0 mm"]}                              // linear: 6 holes in all
{"op": "pattern", "of": "h", "count": 8, "angle": "360 deg", "center": ["0 mm", "0 mm", "0 mm"], "axis": "+z"}   // polar, evenly round
{"op": "mirror", "plane": "yz", "offset": "0 mm"}                                                        // the whole part so far
{"op": "mirror", "plane": "yz", "offset": "50 mm", "of": "h"}                                            // only that feature
{"op": "shell", "faces": ">z", "thickness": "2 mm"}                                                      // open top, walls grow inward
{"op": "extrude", "profile": {"path": {"start": ["0 mm", "-15 mm"], "segments": [
    {"to": ["0 mm", "15 mm"], "radius": "15 mm", "turn": "right"}, {"to": ["0 mm", "-15 mm"]}]}}, "distance": "5 mm", "taper": "5 deg"}
{"op": "fillet", "edges": "of:boss & circle & >z", "radius": "3 mm"}
{"op": "import", "file": "C:/parts/block.step", "at": ["10 mm", "0 mm", "0 mm"], "mode": "cut"}
```

## Decisions, with what was rejected

**Pattern.**

* One op with `step` (linear) or `angle` (polar), exactly one of them, rather than `linear_pattern` and `polar_pattern`: one op costs about half the description characters and a model has one name to remember. The cost is a check that refuses neither or both (`/features/N/angle` or `/step`), and `center` or `axis` next to a `step`.
* `count` includes the original, so `count: 4` is a pattern of 4. A count below 2 is refused (`/count`), as is a whole-number-looking float such as `1e9` above the limit and a string.
* Polar: `angle` of 360 deg divides the full turn evenly (60 deg apart for 6); less than 360 deg runs from the first instance to the last (`count: 4, angle: 90 deg` gives 0, 30, 60, 90 deg). A single convention was chosen for the 360 case because a first-to-last reading would put the last instance on top of the first. The axis is one of the six axis directions through `center` (default the origin); arbitrary axes were not added.
* A pattern copies a body (mode `add` or `cut`) or a hole. Refused with a pointer at `/of`: a pattern of a fillet, chamfer, shell, pattern or mirror (a pattern of a pattern would compound count limits and hide the declared holes), a body with mode `intersect` (the meaning of intersecting with N copies is not "N intersections"), a name that does not exist, a name used by several features, a forward reference. A grid of holes is a hole feature with several entry points and one linear pattern of it.
* One boolean for all copies of a pattern, not one per copy. Measured on a 300 x 100 x 8 plate: 256 hole copies took 73 s with one boolean per copy, 6 s with one compound tool. Each copy is still checked on its own against the part as it was (`_changes`, one cheap intersection of the unchanged part with that copy): a copy that cuts or adds nothing is left out, named in a warning, and declares no hole, so `features.json` never lists a hole that is not in the STEP.
* Declared holes: a pattern of a hole declares `count x entries` holes, all under the pattern's name with the pattern's `feature_index`; positions are rounded to 1e-9 mm and directions to 1e-12 so the file carries no 1e-16 noise from the rotation. `thread` and `fit` text is copied.

**Mirror.**

* `plane` reuses the sketch plane names (`xy` holds z fixed, `xz` y, `yz` x) and `offset` moves it along the normal, so no new type was added to the description. Mirror in an arbitrary plane was not added.
* Without `of`, the whole part so far is reflected and fused; every declared hole is reflected too, skipping a reflection that lands on an existing hole with the same direction and diameter. A part that is already symmetric about the plane adds no material and warns. With `of`, only that body (its `mode` is kept) or hole is reflected.
* Kernel: `gp_Trsf.SetMirror` through `BRepBuilderAPI_Transform`; the result passes the same validity and volume checks as every primitive. `transform` still refuses a mirror matrix; reflection has its own function.

**Shell.**

* Kernel: `BRepOffsetAPI_MakeThickSolid.MakeThickSolidByJoin` with an intersection join, so the cavity corners are sharp and a box has the closed-form cavity `(a - 2t)(b - 2t)(c - t)` for one removed face. The arc join was rejected because it rounds the corners.
* Face selectors: `all >x <x >y <y >z <z plane cylinder of:NAME`, terms joined by `&` like edge selectors (`>z` is the top face of the bounding box). No face may be selected by index or by a free-form phrase.
* Measured kernel behaviour that the checks answer: on a 40 x 30 x 20 box with the top removed, a 16 mm wall returned a valid-looking solid of the unchanged volume 24000, a 15 mm wall an invalid one, a 25 mm wall an invalid one. So the op refuses before and after: before, a wall that cannot fit (per axis the room is the part's size, or half of it when no removed face lies on either side of that axis) is `input.invalid` at `/thickness`; after, the result must be one valid solid with a volume below the original, otherwise `geometry.build_failed`. For box-like parts the pre-check is exact. For other shapes the post-checks are what protect the result; they are not a proof that every wall has the requested thickness.
* A shell with no removed face (a closed hollow) is refused as an empty selection; walls growing outward were not added. Holes declared before a shell keep their declared size and position while the kernel offsets their walls; this is a warning, drill after the shell when the size matters.

**Path profiles.**

* Segments are `{to}` (a line), `{to, radius, turn}` (an arc of that radius, the shorter way round, turning `left` = counter-clockwise or `right` along the direction of travel) and `{to, via}` (an arc through a point, any size). A half circle is `radius` equal to half the chord; anything larger than a half turn is a `via` arc or two arcs.
* The outline must close: the last `to` equals `start` within 1e-6 mm. Auto-closing with a straight segment was rejected: a forgotten last arc would then silently become a chord, which is the wrong part with no error.
* Validity is checked before the kernel sees the outline, as pure Python in `eng_toolkit/brep/_path.py`, with the pointer of the offending segment: zero-length segment, radius smaller than half the chord (`/radius`), `via` on a line with the end points or on top of one (`/via`), radius together with `via`, `turn` without `radius`, not closed (`/segments/N/to`), self-crossing or folding back (the later segment of the pair), zero area. Self-crossing is tested on a polyline whose arcs turn at most 5 degrees per piece, so a legitimate feature thinner than about 0.1 % of an arc radius could be refused; the kernel's own `BRepCheck` still validates the solid built from it.
* Rejected: SVG path strings (a text parser is a larger attack and error surface and a worse JSON-pointer story), bulge factors (a model has to compute them), ellipse and spline segments.
* `revolve` accepts the same profile; the existing rule that the profile stays on one side of the axis still applies and is enforced by the kernel's validity check.

**Taper.**

* `taper` leans every side wall inward along the extrusion, 0 to 45 deg, exact: a 20 mm square 10 mm high at 10 deg has the frustum volume. Kernel: `LocOpe_DPrism`. Three measured facts shaped the code: its height runs along the sloping wall, so it is given `distance / cos(taper)`; its direction follows the face's own normal, which is reversed for a profile drawn clockwise, so the sign is chosen from the face; and when the taper closes the profile to a point before the distance is reached it silently stops at the apex, so the bounding box is compared with the distance and the op is refused (`/taper`) when they differ.
* Widening (a negative taper) was not offered: the kernel rounds the corners of a widening draft (a 20 mm square widened by 10 deg over 10 mm measured 4737.87 mm^3 against the frustum 4746.76), so the result would not be what the caller asked for. To widen, extrude the other way (negative `distance`) from the wider profile.

**`of:NAME`.**

* The rule is geometric, applied when the named feature is built: an edge (or face) of the part right after the feature belongs to it when all its sample points lie on the boundary of the feature's tool shape and not all of them lie on an edge (or face) the part already had. The first body therefore owns all its edges; a hole owns its two rim circles and its seam; a boss owns its top circle, base circle and seam. Later features that split or trim an edge leave pieces that still lie on the recorded edge, so they still match; edges a later fillet or boolean replaces no longer match.
* Rejected: the kernel's boolean history (`Generated` and `Modified`), which would have to be composed across the unify pass, fillets and shells and fails silently when one link is missing.
* Only features that have a tool shape can be named: bodies (including `import`), holes, patterns and mirrors. A pattern's copies are recorded under the pattern's name and also under the source's name, so `of:h` is the hole and all its copies and `of:row` is the copies only. Naming a fillet, chamfer or shell, a name that does not exist (or is only defined later), or a name used by several features is `input.invalid` at the selector's pointer. The tool shape is recorded only for names an `of:` term refers to, because the sampling costs time.
* Faces: a face that merged into an older face (coplanar with it, so the unify pass made one face) is not "created". That is documented behaviour, not a bug: use `>z` style terms for such faces.

**Import.**

* `file` is opened through `JobHandle.input_file` with the extensions `.step .stp .iges .igs .brep` and read by the same `read_solid` as `geometry_measure`: STEP and IGES carry their unit and arrive in millimetres, BREP numbers are taken as millimetres. A real inch STEP (1 x 2 x 0.5 inch) imports as 25.4 x 50.8 x 12.7 mm.
* The file must hold exactly one closed solid with positive volume and a valid shape; shells and loose faces, several solids, an incomplete transfer and a file the kernel cannot read are `input.invalid` at `/features/N/file`. `at` moves the solid; rotation and scaling were not added.
* Determinism: the same file bytes give the same STEP bytes. `spec_sha256` covers the spec text only, so the file bytes are recorded beside it: `data.imports` and `<name>.features.json` list `{feature, feature_index, file, sha256}` for every import, and `provenance.input_files` records the real path, size and hash.
* Holes and other features inside an imported solid are not declared (the kernel could find them with `geometry_measure`); a warning says so.

### `or` between selector groups

Edge selectors (`fillet`, `chamfer`) and face selectors (`shell`) accept up to eight non-empty groups joined by lowercase, whitespace-delimited `or`. `&` binds tighter: `of:boss & circle & >z or of:boss & line`. There are no parentheses. `>z or <z` removes both end faces; `>x or <x or >z` opens three sides. The union counts each edge or face once. A valid group may match nothing; only an empty whole union raises `geometry.empty_selection`.

Every group must parse on its own. `>z or`, `or >z`, `>z or or <z`, uppercase `OR`, `||` and commas are `input.invalid`, with `details.pointer` at the selector field and `details.allowed` listing its terms. More than eight groups also reports `details.limit: 8`. Existing input limits still apply, including the 64 kB spec limit. A feature named `or` remains valid: `of:or` is a term, not a standalone operator.

The shell pre-check uses the whole union. Removing either or both ends of an axis gives that axis the full part size as its room; retaining both walls gives half the size. On a 40 x 30 x 20 box opened at both z ends, the rooms are 20, 15 and 20 mm: 14 mm fits, while 15 and 16 mm fail. With both x ends open, the rooms are 40, 15 and 10 mm: 9 mm fits, while 10 and 14 mm fail. The checks for a valid single solid and smaller volume remain in place. Tests derive the multi-face shell and edge-union fillet volumes by hand, including the overlapping strips at box corners.

## What is exact and what is not

Exact against closed forms, measured at 1e-12 relative or better in `tests/test_make_part_round2.py`: pattern volumes (4 holes remove exactly 4 times one), polar positions, mirrored volumes, the shell cavity of a box and of a cylinder, a stadium and a D shape extruded, a torus and a half disc revolved (Pappus), square, circle and stadium tapers, an `of:` fillet against the Pappus volume of the removed ring and against the explicit edge list, imported blocks.

Not exact or not proven:

* The self-crossing test of path profiles sees arcs as 5-degree polylines (above).
* The `of:` sets use a 1e-5 mm sampling tolerance and seven points per edge, nine interior points per face; an edge shorter than that tolerance region could be misattributed.
* The shell is checked, not proven, for parts that are not box-like (above).
* The idle-copy check judges a copy against the part before the pattern, so a copy that only overlaps an earlier copy is not reported.
* Import is limited to what OCCT reads; an IGES file written as surfaces has no solid and is refused.

## The description budget

The tool description is generated from `OPS`; it is what a small model reads. It was 3737 characters for nine ops (limit 3900). With seven more features it is 5108; the limit in `tests/test_part_spec.py` is now 5200. Compression applied: one `pattern` op instead of two; `mirror` reuses the `PLANE` type; `SEGS` carries the whole path grammar in one type entry; summaries are one clause each. Dropped: nothing from round 1, and no second example (the single example still parses and is the one in the README). The cost of the extra 1370 characters is about 350 tokens per session; if that matters for a very small context, the first things to remove are the `import` op line and the face-selector sentence, in that order, since both are the least likely to be needed in a first call.

The `or` syntax adds 82 characters, bringing the description to 5190, below the unchanged 5200 limit. `tests/eval_data/tool_as_seen.json` is regenerated from the gateway with `tests/eval_data/run_round2.py seen`; the round 1 description and all pinned model-written specs remain unchanged.

## Not done

Loft, sweep, threads (round 1 gaps that remain), polar patterns about an arbitrary axis, patterns of fillets or chamfers or of other patterns, mirror in an arbitrary plane, widening taper, outward or per-face shell thickness, rotated or scaled import, ellipse and spline path segments, the AP242 PMI read of slice S10.
