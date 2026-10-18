# geometry and drawing domains: candidate 1

Runner: candidate 1 (architect Phase B). Grounded on `main` at 873334a; engine facts probed in a scratch environment at `C:\Users\USER\ep-arena-1` (Windows 11, Python 3.12.12, uv 0.9.18). The probe scripts are in `candidate-1/probes/`. Facts I measured are marked **(measured)**. Everything else is either read from the repository or marked unverified.

## Problem

The toolkit needs a `geometry` domain (make a part, measure it, check whether it can be manufactured, convert it) and a `drawing` domain (make orthographic views with hidden lines and dimensions, read DXF/DWG/PDF, convert 2D files). The shape is not obvious for four reasons.

1. **The design document puts arbitrary code execution inside a tool server.** It calls for `geometry.make_from_code`: build123d source in, STEP out. A tool server that runs model-written Python gives every MCP client a shell. On Windows nothing in a clean install can confine that shell (see Q4). It also gets around the client's own per-command permission prompt.
2. **There is already one owner of STEP reading.** `metrology/scan.py` reads STEP with OCP, silences OCCT's stdout printer, counts roots and solids, and tessellates. The new domains need all of that plus HLR, booleans and writing. A second reader would split the ownership the brief forbids.
3. **The existing contract is strict.** Parameters must be strings, bools or ints. Every quantity is a unit string. Results stay small and large data goes to artifacts. Every result is `unreviewed`. Heavy engines are optional extras, the tool is registered anyway, and a missing engine gives `engine.unavailable` with the install command. The licence registry has to tell the truth about bundled LGPL code.
4. **What permissive tools can do in 2D is limited.** DWG has no permissive reader and no reliable writer. PMI and GD&T have no verified open path. OCCT HLR gives raw edges, so layout and dimensioning are rules we write ourselves.

Constraints from Phase A that this design honours:
- `ToolResult`, `run_tool` and `JobHandle` stay the integration point.
- `UnitsError(family, message, details)` is the structured error carrier.
- `licenses.KNOWN` keeps the `optional` and `bundled_license` fields.
- `DomainInfo` and `all_domains()` mount each domain, so a tool becomes `<domain>_<tool>`.
- The doctor fails on any GPL or AGPL distribution in the environment.
- check_scan's tests (stdout silence, non-ASCII paths, root counting) must keep passing unchanged.

## Usage (caller's view)

### README excerpt (what an agent or a user reads)

```text
geometry_make_part   build a part from a parametric JSON spec (no code is executed)  -> STEP (+GLB/STL), volume, mass, holes
geometry_measure     mass properties, bounding box, face and hole inventory of a STEP/IGES/BREP/STL; distance/angle/diameter queries
geometry_check_dfm   geometric facts for cnc_3axis | injection_molding | additive, compared with stated limits -> findings
geometry_convert     STEP/IGES/BREP/STL -> STEP | STL | GLB | BREP, with a loss report (volume before/after, chord error)
drawing_make_views   orthographic views with hidden lines, overall and hole dimensions, ISO title block -> DXF + PDF (+SVG/PNG)
drawing_read         layers, entities, dimensions, texts and title block of a DXF (DWG via LibreDWG if installed); text and vectors of a PDF
drawing_convert      DXF/DWG -> PDF | SVG | PNG | DXF (another version), true scale, with a loss report

Install: uv sync --extra cad --extra drawing  (scripts\install.ps1 installs every extra).
Without an extra the tools are still listed; a call returns engine.unavailable with the exact install command.
```

### Call site 1: an agent's make, verify and draw loop (MCP)

```text
geometry_make_part(
  spec='{"params": {"L": "100 mm", "W": "50 mm", "T": "20 mm", "d": "10 mm"},
         "features": [
           {"op": "box",    "name": "body",    "size": ["$L", "$W", "$T"]},
           {"op": "hole",   "name": "bore",    "at": [["$L / 4", "$W / 2", "$T"]], "axis": "-z",
                            "diameter": "$d", "depth": "thru", "fit": "H7"},
           {"op": "fillet", "name": "corners", "edges": "|z & convex", "radius": "2 mm"}]}',
  name="bracket", formats="step,glb", density="2700 kg/m^3")
-> ok; value volume = 98360.53 mm^3 (hand: 100*50*20 - pi*5^2*20 - 4*(2^2 - pi*2^2/4)*20)
   values: volume, surface_area 16395.70 mm^2, mass 0.26557 kg, size_x 100 mm, size_y 50 mm, size_z 20 mm
   data.features: [{index 1, name body, op box, volume_after 100000}, {2, bore, hole, 98429.20}, {3, corners, fillet, edges 4, 98360.53}]
   data.holes: [{id H1, diameter 10, axis -z, entry (25, 25, 20), depth 20, thru true, fit H7}]
   data.readback: {volume_written 98360.531085, volume_read 98360.531085, relative_difference 1e-14}
   artifacts: bracket.step (sha256 identical on every rebuild of the same spec), bracket.glb, bracket.features.json, spec.json

geometry_measure(model_path="ws/<job>/bracket.step", queries="distance F1 H1; diameter H1; angle F1 F2; thickness P(10 mm, 10 mm, 30 mm) -z")
-> values volume, surface_area, size_*, q1 = 20 mm, q2 = 10 mm, q3 = 90 deg, q4 = 20 mm
   data.faces_by_kind {plane 6, cylinder 5}, data.holes [H1 ...], artifacts inventory.json (every face with id, kind, area, normal/axis, radius)

geometry_check_dfm(model_path=".../bracket.step", process="cnc_3axis", direction="+z",
                   limits='{"min_internal_radius": "3 mm", "min_wall": "1 mm", "max_hole_depth_to_diameter": 4}')
-> value fail_count 0; findings: hole H1 depth/diameter 2.0 <= 4 (pass), min wall 20 mm >= 1 mm (pass);
   facts: internal vertical corners 0 found (a fact, not a pass); coverage lists what was not checked

drawing_make_views(model_path=".../bracket.step", title="BRACKET", part_number="BR-001", material="EN AW-5754",
                   general_tolerance="ISO 2768-m", features_path=".../bracket.features.json")
-> values scale 1 (1:1), view_count 3, dimension_count 8, sheet A4
   data.dimensions: [X 100 (front), Z 20 (front), Y 50 (top), "Ø10 H7 THRU" (top, H1), ordinates x 0/25, y 0/25 (top)]
   artifacts drawing.dxf, drawing.pdf (297 x 210 mm, true scale)
```

### Call site 2: third-party files

```text
drawing_read(path="C:/drawings/flange_rev3.dwg")
-> engine.unavailable "DWG needs LibreDWG's dwg2dxf (GPL-3.0, run as a separate process; not installed by eng-toolkit).
   Install it and set ENG_TOOLKIT_DWG2DXF to dwg2dxf.exe, or export DXF from the authoring CAD."   (when absent)
-> ok; units millimeter, layers 12, dimensions 31 (each with its displayed value = measurement x DIMLFAC),
   title_block {title "FLANGE", number "FL-200", revision "3"} source "attributes"   (when present)

drawing_convert(input_path="C:/drawings/flange_rev3.dxf", output_format="pdf", sheet="A3", scale="1:1")
-> artifacts flange_rev3.pdf; data.losses ["SHX font isocp.shx substituted with arial.ttf: text widths differ",
   "3 ACIS 3DSOLID entities not drawn"]

geometry_convert(input_path="C:/cad/housing.step", output_format="stl", mesh_deflection="0.01 mm")
-> values triangle_count, volume_in (exact), volume_out (mesh), volume_relative_change; data.losses ["exact surfaces replaced by triangles, chord error <= 0.01 mm", "names, colours, assembly structure not carried"]
```

### Call site 3: Python inside the toolkit

```python
# eng_toolkit/domains/metrology/scan.py after slice 1: the scan check becomes a consumer of brep.
def read_model_mesh(path: Path, deflection_mm: float) -> ModelMesh:
    o3d = _open3d()
    solid = brep.read_solid(path)                   # STEP read, root and solid counts, engine guard, stdout silenced
    faces = brep.triangulate(solid, deflection_mm)  # per-face triangles, outward winding, not welded
    mesh = o3d.geometry.TriangleMesh(o3d.utility.Vector3dVector(faces.vertices),
                                     o3d.utility.Vector3iVector(faces.triangles))
    mesh = mesh.merge_close_vertices(MERGE_EPS_MM)  # unchanged from today
    ...
    return ModelMesh(..., solids=solid.solids, roots_in_file=solid.roots_in_file,
                     roots_transferred=solid.roots_transferred)

# eng_toolkit/domains/drawing/tools.py, inside make_views' compute(job):
model_file = job.input_file(model_path, param="model_path", extensions=brep.SOLID_EXTENSIONS)  # validated, hashed into provenance
solid = brep.read_solid(model_file)
drawing = sheet.compose(solid, brep.inventory(solid), views=parsed_views, method=method, sheet=sheet_name,
                        scale=scale, hidden=hidden_lines, dimensions=dimensions, title=title_block, declared=declared)
dxf_art = job.write_bytes("drawing.dxf", dxf.write_dxf(drawing), fmt="dxf")
pdf_art = job.write_bytes("drawing.pdf", dxf.render(Path(dxf_art.path), "pdf", sheet=drawing.sheet, scale="1:1").data, fmt="pdf")

# tests/test_geometry_make_part.py: the hand calculation drives the test.
r = make_part.fn(spec=BRACKET_SPEC, name="bracket")
assert r.values["volume"].quantity == pytest.approx(100*50*20 - math.pi*25*20 - 4*(4 - math.pi)*20, abs=1e-6)
assert sha256(step_of(r)) == sha256(step_of(make_part.fn(spec=BRACKET_SPEC, name="bracket")))
```

## Shape

### Load-bearing decisions

1. **One owner for OpenCASCADE and 3D model files: `eng_toolkit/brep/`.** It sits at toolkit level beside `units.py` because three domains (geometry, drawing, metrology) use it. It reads STEP, IGES, BREP and STL. It writes STEP, BREP, STL and GLB. It tessellates, computes mass properties, builds the face and hole inventory, casts exact rays, runs HLR projection and the modelling ops. `metrology/scan.py` gives up its STEP code to it in slice 1. `tests/test_engine_ownership.py` parses every module under `eng_toolkit/` and fails the build when `OCP` is imported outside `eng_toolkit/brep/`, `ezdxf` outside `drawing/dxf.py`, `pdfplumber`/`pypdfium2` outside `drawing/pdf.py`, `open3d` outside `metrology/scan.py`, or when anything outside brep touches `eng_toolkit.brep._*` or `._handle`. This follows **encode-lessons-in-structure**: a later agent cannot copy scan.py's reader into a new module without the build failing.
2. **No code execution in the server.** `geometry.make_from_code` is replaced by `geometry_make_part`. It takes a declarative, parametric JSON spec in which every number is a unit string. The interpreter is ours and runs on OCP only. Capable agents can still write build123d in their own shell, where the client's permission prompt applies, and hand the STEP to every other tool. Q4 gives the full reasoning and the exact gated design to use if the owner overrides.
3. **The DXF is the single source of a drawing.** PDF, SVG and PNG are renders of that DXF through ezdxf's frontend. The PDF comes from a small backend of our own: true scale, deterministic bytes, no matplotlib. The views are never drawn twice by two writers that could drift apart.
4. **Feature intent travels as a file bound by hash.** `geometry_make_part` writes `<name>.features.json` (declared holes, threads, fits, parameters) with the STEP's sha256 inside. `drawing_make_views` uses it for callouts that geometry cannot carry (H7, M6x1-6H) only if the STEP it reads has that hash. Geometry is derived from the spec. The callout data is derived from the spec. Neither is copied into a second place by hand (**single source of truth**).
5. **Facts first, verdicts only against stated limits.** `geometry_check_dfm` always reports measured facts. It says pass or fail only against a limit that is either supplied by the caller or ships in `dfm_rules.json` with a checked source. Every other check is reported as `unverified`, never as pass.
6. **Extras follow capabilities, and install hints are derived from the registry.** The extras are `cad` (OCP), `drawing` (ezdxf, pdfplumber) and `scan` (= `cad` + open3d). `Engine` gains an `extra` field. A test fails when `pyproject.toml`'s optional-dependencies and `licenses.KNOWN` disagree. `engine.unavailable` messages are built from the registry, never written into a tool by hand.
7. **One structured error carrier.** `eng_toolkit/errors.py::ToolFailure(family, message, details)` is caught by `run_tool`. `UnitsError` becomes a subclass, so existing code is unchanged. Pure modules raise `ToolFailure` directly, and the per-tool `try/except` re-mapping that check_scan needs today is no longer needed (**boundary-discipline**: domain errors are typed where they arise, and the runner converts them once).

### Tool list (Q1): exact parameters, returns, artifacts

Every parameter is a string, a bool or an int. Every quantity is a unit string. Every result carries `review_status="unreviewed"`, `data.job_id` and `job_dir`, the standing warnings, and `provenance.input_files` / `provenance.engine_versions` (see Q6). Lists in `data` are capped; full lists go to artifacts.

**`geometry_make_part`** (extra `cad`)

| Parameter | Type, default | Meaning |
| --- | --- | --- |
| `spec` | str (required) | Part spec JSON: `{"params": {...}, "features": [...]}`. The grammar is below; the tool description carries the op table and one example. At most 64 kB, 200 features, 1000 hole positions. |
| `name` | str = `"part"` | Product name in the STEP and stem of the artifact files. `[A-Za-z0-9_-]{1,64}`. |
| `formats` | str = `"step"` | Comma list of `step`, `glb`, `stl`, `brep`. STEP is always written (it is the read-back reference). |
| `mesh_deflection` | str = `"0.01 mm"` | Chord error for GLB/STL. |
| `density` | str = `""` | Optional, e.g. `"7850 kg/m^3"`; adds `mass`. |
| `output_unit` | str = `"mm"` | Length unit of the results (volume in unit^3, area in unit^2). |

Returns: primary `volume`; `surface_area`, `size_x/y/z`, optional `mass`. `data.features` gives per feature: index, name, op, volume after it, edges selected. `data.holes` lists declared holes resolved to positions in mm. `data.params` holds the resolved parameters in mm. `data.bbox`, `data.solids` (must be 1). `data.readback` compares the volume of the shape as written with the STEP as read back. `data.spec_sha256` is the hash of the canonical parsed spec. Artifacts: `<name>.step`, `<name>.features.json`, `spec.json` (canonical spec), and optional `<name>.glb`/`.stl`/`.brep`.

**`geometry_measure`** (extra `cad`)

| Parameter | Type, default | Meaning |
| --- | --- | --- |
| `model_path` | str | `.step .stp .iges .igs .brep` (exact) or `.stl` (mesh). |
| `queries` | str = `""` | `;`-separated: `distance A B`, `angle A B`, `diameter A`, `radius A`, `area A`, `thickness P(x, y, z) <axis>`. `A`/`B` = `F<n>` (face id from the inventory), `H<n>` (hole), `P(10 mm, 0 mm, 5 mm)` (point). At most 20 queries. |
| `density` | str = `""` | Optional; adds `mass`. |
| `inventory_limit` | int = 50 | Number of faces and holes listed in `data`, 0 to 500. `inventory.json` always has all of them. |
| `output_unit` | str = `"mm"` | |

Returns: primary `volume`; `surface_area`, `size_x/y/z`, `mass`, and `q1..qn` (lengths in `output_unit`, angles in deg). `data` holds `solids`, `faces_by_kind`, `holes`, `faces` (capped), `centroid`, `bbox`, `step_roots` (in file / transferred), `queries` (each parsed query echoed with what it resolved to), `face_ids_valid_for` (the input sha256). For STL: `watertight` and `triangles`. There is no inventory or query support on STL beyond `distance` between points and `thickness`; asking for more gives `geometry.needs_brep`. Artifact: `inventory.json`.

**`geometry_check_dfm`** (extra `cad`)

| Parameter | Type, default | Meaning |
| --- | --- | --- |
| `model_path` | str | STEP/IGES/BREP; STL only for `additive`. |
| `process` | str | `cnc_3axis`, `injection_molding` or `additive`. |
| `direction` | str = `"+z"` | Axis pointing from the part towards the tool (cnc; a comma list gives one entry per setup, e.g. `"+z,-z"`), the opening direction of the moving mould half (molding), or up the build (additive). |
| `limits` | str = `""` | JSON object overriding or supplying limits, e.g. `{"min_wall": "1.5 mm", "min_draft": "1 deg", "max_hole_depth_to_diameter": 4}`. Ratios are plain JSON numbers: a ratio is not a physical quantity. |
| `sample_spacing` | str = `""` | Spacing of the ray samples for wall thickness and undercuts; empty = bbox diagonal / 100, at least 0.2 mm. |
| `max_findings` | int = 50 | Findings listed in `data`, 1 to 500; `findings.json` has all. |
| `output_unit` | str = `"mm"` | |

Returns: primary `fail_count`; `review_count`, `unverified_count`. The process facts are values too: `min_wall_thickness`, `min_draft_angle`, `undercut_area`, `overhang_area`, `min_internal_corner_radius`, `max_hole_depth_to_diameter`. `data.findings` entries are `{rule, status: pass|fail|review|unverified, measured, limit, unit, where_mm, face_ids, source, note}`. `data.limits` gives each limit with its value, source and whether it was overridden. `data.coverage` lists checks performed and checks not performed, with the reason. Artifacts: `findings.json`, `wall_samples.csv`.

**`geometry_convert`** (extra `cad`)

| Parameter | Type, default | Meaning |
| --- | --- | --- |
| `input_path` | str | STEP/IGES/BREP/STL. |
| `output_format` | str | `step`, `stl`, `glb` or `brep`. STL as input can only go to `glb`. |
| `mesh_deflection` | str = `"0.01 mm"` | For STL/GLB output. |
| `name` | str = `""` | Output stem and product name; empty = input stem. |

Returns: primary `volume_out`; `volume_in`, `volume_relative_change`, `triangle_count` (mesh outputs). `data.losses` lists what was lost, for example exact surfaces become triangles (chord ≤ deflection), "GLB is metres and +Y up: converted", names, colours and assembly structure dropped. Artifact: the converted file.

**`drawing_make_views`** (extras `cad`, `drawing`)

| Parameter | Type, default | Meaning |
| --- | --- | --- |
| `model_path` | str | STEP/IGES/BREP. |
| `views` | str = `"front,top,right"` | Comma list from `front back top bottom left right iso`. |
| `projection` | str = `"first_angle"` | `first_angle` (ISO 128 / ISO 5456-2) or `third_angle`. |
| `sheet` | str = `"auto"` | `auto`, `A4`, `A3`, `A2`, `A1`, `A0` (landscape, ISO 5457). |
| `scale` | str = `"auto"` | `auto` or an ISO 5455 scale such as `1:2` or `2:1`. |
| `hidden_lines` | bool = true | |
| `dimensions` | str = `"auto"` | `auto` (overall size + holes), `overall`, or `none`. |
| `features_path` | str = `""` | `features.json` from `geometry_make_part`; adds fit and thread callouts when its STEP sha256 matches. |
| `title` | str = `""` | Title block. |
| `part_number` | str = `""` | Title block. |
| `material` | str = `""` | Title block. |
| `revision` | str = `"A"` | Title block. |
| `general_tolerance` | str = `""` | Printed as note 1, e.g. `ISO 2768-m`; text only. |
| `notes` | str = `""` | `;`-separated numbered notes. |
| `formats` | str = `"dxf,pdf"` | Any of `dxf`, `pdf`, `svg`, `png`. DXF is always written; it is the source of the others. |

Returns: primary `scale` (dimensionless, 0.5 = 1:2); `view_count`, `dimension_count`, `sheet_width`, `sheet_height`. `data.sheet` gives size, scale and method. `data.views` gives per view: name, direction, sheet origin, bbox, visible and hidden edge counts, dropped coincident / tangent / seam counts, and method exact|polygonal. `data.dimensions` gives kind, view, value (true size, mm), text and source (`bbox:x`, `hole:H1`). `data.callouts` and `data.layout_warnings` complete it. Artifacts: `drawing.dxf`, `drawing.pdf`, optional `drawing.svg` and `drawing.png`.

The title block always prints `UNREVIEWED - generated by eng-toolkit, not engineering approved`. The contract's `review_status` is thereby on the paper too.

**`drawing_read`** (extra `drawing`; DWG also needs the external `dwg2dxf`)

| Parameter | Type, default | Meaning |
| --- | --- | --- |
| `path` | str | `.dxf`, `.dwg` or `.pdf`. |
| `pages` | str = `"all"` | PDF only: `all`, `3` or `1-4`; at most 20 pages. |
| `max_items` | int = 100 | Texts, dimensions and words listed in `data`, 0 to 1000; the artifact has everything. |
| `output_unit` | str = `"mm"` | For extents and dimension values when the file declares its units. |

Returns for DXF/DWG: values `entity_count`, `layer_count`, `dimension_count`, `text_count`, `extents_width`, `extents_height`. `data` holds: `dxf_version`; `units` (from `$INSUNITS`; unitless warns); `layers` (name, entity count, linetype, on/frozen); `counts_by_type`; `dimensions` (kind, displayed value = measurement × effective DIMLFAC, text override, layer, position); `texts`; `title_block` (fields, source `attributes` | `region` | `none`); `layouts`; `unsupported` (ACIS solids, proxies, images); `recovered` (true when `ezdxf.recover` had to repair the file); for DWG, `converted_by`. Artifact `entities.json`.

Returns for PDF: values `page_count`, `word_count`, `vector_count`. `data.pages` gives size in mm, ISO sheet guess, words with positions, vector counts (lines, curves, rects), image count, `dimension_tokens` (regex: `Ø12`, `R3`, `M6x1`, `25 ±0.1`, `Ø10 H7`, `30°`) and `title_block_text` (words in the bottom-right region). `data.text_layer` is false with a warning when a page has vectors or images but no words. Artifact `words.json`.

**`drawing_convert`** (extra `drawing`; DWG input needs `dwg2dxf`)

| Parameter | Type, default | Meaning |
| --- | --- | --- |
| `input_path` | str | `.dxf` or `.dwg`. |
| `output_format` | str | `pdf`, `svg`, `png` or `dxf`. |
| `sheet` | str = `"auto"` | Page for pdf/svg/png; auto = the drawing extents plus 10 mm. |
| `scale` | str = `"fit"` | `fit` or `1:1`, `1:2`, ... |
| `layout` | str = `"Model"` | Modelspace or a paperspace layout name. |
| `dxf_version` | str = `"R2018"` | For DXF output: R12, R2000, R2004, R2007, R2010, R2013, R2018. |
| `dpi` | int = 150 | PNG only, 50 to 600. |

Returns: primary `entity_count_drawn`; `entity_count_skipped`, `page_width`, `page_height`, `scale`. `data.losses` covers fonts substituted, linetypes approximated, entities skipped by type, and entities dropped by an older DXF version. Artifact: the converted file.

**Changes to the draft's six tools, and why.** `make_from_code` becomes `make_part` (Q4). `geometry.convert` is added and `drawing.convert` is restricted to 2D. The draft's `drawing.convert` mixed OCCT, ezdxf, gdstk and lib3mf, four kinds of knowledge behind one name, and 3D export belongs with the 3D owner. GDS, Gerber and 3MF are left to their own domains. Seven tools in total.

### Module map

```
eng_toolkit/
  errors.py                 NEW      ToolFailure: the one structured error carrier (UnitsError subclasses it)
  runner.py                 CHANGED  catches ToolFailure; JobHandle.input_file / reserve / record; provenance input_files + engine_versions
  contract.py               CHANGED  FileRef; Provenance.input_files, Provenance.engine_versions (optional, default empty)
  licenses.py               CHANGED  Engine.extra; install_hint(dist); ExternalTool registry (dwg2dxf); KNOWN += ezdxf, pdfplumber, pypdfium2
  brep/                     NEW      the one owner of OCP and of 3D model files (no OCP import at module import time)
    __init__.py             public types and functions (re-exports); nothing else is importable from outside
    _occ.py                 import guard -> EngineUnavailable, OCCT messenger silenced, OCP 8 naming shims, translator lock
    _files.py               read_solid (STEP/IGES/BREP), read_mesh (STL), write_step (XCAF, fixed header), write_brep, write_mesh (STL/GLB), triangulate
    _query.py               mass_properties, inventory (faces, holes), distance, angle, wall_thickness, ray_hits, validity
    _project.py             project(): exact HLR -> 2D primitives, coincident-hidden removal, tangent and seam filtering, polygonal fallback
    _model.py               make_box/cylinder/prism/revolution/hole_tool, combine, fillet, chamfer, edge selectors
  domains/
    __init__.py             CHANGED  all_domains() += geometry, drawing
    geometry/
      __init__.py           DOMAIN (engines: pint, numpy, cadquery-ocp-novtk)
      tools.py              make_part, measure, check_dfm, convert (parse strings -> call -> shape ToolResult)
      part.py               the part spec language: OPS registry, parse_spec (units via gatekeeper), build (calls brep), features document
      dfm.py                pure rule evaluation: facts from brep -> findings; one checker per process
      data/dfm_rules.json   limits per process, each with value, unit, source, accessed date (no number without a checked source)
    drawing/
      __init__.py           DOMAIN (engines: cadquery-ocp-novtk, ezdxf, pdfplumber, pypdfium2)
      tools.py              make_views, read, convert
      sheet.py              pure: sheet and scale choice, view placement (first/third angle), dimension rules, title block -> Drawing
      dxf.py                the one owner of ezdxf: write_dxf (deterministic), read_dxf, render (PDF backend of ours, SVG), convert_version
      pdf.py                the one owner of pdfplumber/pypdfium2: read_pdf, rasterize (PNG)
      dwg.py                LibreDWG dwg2dxf as a separate process (GPL, never imported, never installed by us)
    metrology/scan.py       CHANGED  read_model_mesh uses brep.read_solid + brep.triangulate; its OCP code is deleted
tests/
  test_engine_ownership.py  NEW      AST import rules above; private-name rules
  test_extras_registry.py   NEW      pyproject optional-dependencies == {extra: dists} derived from licenses.KNOWN
  test_brep.py, test_geometry_*.py, test_drawing_*.py   NEW  hand-calculation tests (Q6)
```

Call chains stay at three files or fewer: `tools.py -> part.py -> brep`; `tools.py -> sheet.py -> brep`; `tools.py -> dxf.py`. Parsing and building the spec live in one module because they protect the same knowledge, the spec language (avoiding **temporal decomposition**). Layout and dimensioning live in one module because dimension rows decide the layout margins.

### Type sketch

```python
# ---------------------------------------------------------------- eng_toolkit/errors.py
class ToolFailure(Exception):
    """A failure the caller can act on. run_tool converts it into ok=False with this family, message and details.

    Families are stable strings: input.*, units.*, geometry.*, drawing.*, engine.*.
    """

    def __init__(self, family: str, message: str, details: dict[str, Any] | None = None) -> None:
        raise NotImplementedError

# units.py: `class UnitsError(ToolFailure)` (constructor and attributes unchanged).
# runner.py: `except ToolFailure as exc:` replaces `except UnitsError as exc:`.

# ---------------------------------------------------------------- eng_toolkit/contract.py (additions)
class FileRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str
    bytes: int
    sha256: str

class Provenance(BaseModel):          # existing fields unchanged
    ...
    input_files: list[FileRef] = Field(default_factory=list, description="Every file the call read, hashed")
    engine_versions: dict[str, str] = Field(default_factory=dict, description="Installed version of each engine used")

# ---------------------------------------------------------------- eng_toolkit/runner.py (additions)
class JobHandle:
    def input_file(self, raw: str, *, param: str, extensions: tuple[str, ...]) -> Path:
        """Validate a caller path (input.file_not_found / input.unsupported_format) and record it for provenance.input_files."""
        raise NotImplementedError

    def reserve(self, filename: str) -> Path:
        """A path inside the job dir for an engine that can only write to a path (OCCT writers)."""
        raise NotImplementedError

    def record(self, filename: str, *, fmt: str) -> Artifact:
        """Hash a file an engine wrote at reserve(filename) and list it as an artifact."""
        raise NotImplementedError

def run_tool(*, tool_name, engine, version, inputs, compute, workspace=None, standing_warnings=(),
             engines: tuple[str, ...] = ()) -> ToolResult:
    """As today, plus: provenance.engine_versions from importlib.metadata for `engines`, provenance.input_files from the handle."""
    raise NotImplementedError

# ---------------------------------------------------------------- eng_toolkit/licenses.py (additions)
@dataclass(frozen=True)
class Engine:                          # existing fields unchanged
    ...
    extra: str = ""                    # pyproject extra that installs it; "" = core dependency

def install_hint(dist: str) -> str:
    """'uv sync --extra cad' for an optional engine. engine.unavailable messages are built from this, never typed in a tool."""
    raise NotImplementedError

@dataclass(frozen=True)
class ExternalTool:
    """A GPL program the toolkit may run as a separate process. It is never imported and never installed by the toolkit."""
    name: str                          # "dwg2dxf"
    project: str                       # "LibreDWG"
    license: str                       # "GPL-3.0-or-later"
    tier: str                          # TIER_GPL
    role: str
    env_var: str                       # "ENG_TOOLKIT_DWG2DXF"; else looked up on PATH
    domains: tuple[str, ...]

EXTERNAL: dict[str, ExternalTool]
def locate(tool: ExternalTool) -> Path | None: raise NotImplementedError   # doctor lists it, informational

# ---------------------------------------------------------------- eng_toolkit/brep/__init__.py
"""The one owner of OpenCASCADE (OCP, optional extra `cad`) and of 3D model files.

Lengths are millimetres, angles radians. OCP is imported inside functions; a missing wheel raises
EngineUnavailable. OCCT translator calls (STEP/IGES read and write) hold one process-wide lock because
OCCT's Interface_Static parameters are global and FastMCP runs sync tools on a thread pool.
"""

Vec2 = tuple[float, float]
Vec3 = tuple[float, float, float]
AxisName = Literal["+x", "-x", "+y", "-y", "+z", "-z"]
ViewName = Literal["front", "back", "top", "bottom", "left", "right", "iso"]
SOLID_EXTENSIONS: tuple[str, ...] = (".step", ".stp", ".iges", ".igs", ".brep")
MESH_EXTENSIONS: tuple[str, ...] = (".stl",)

class EngineUnavailable(ToolFailure):
    """family 'engine.unavailable'; details {module: 'OCP', dist: 'cadquery-ocp-novtk', install: install_hint(...)}."""

@dataclass(frozen=True, eq=False)
class Solid:
    """A B-rep in millimetres. `_handle` is the OCP TopoDS_Shape; it never leaves this package."""
    _handle: object = field(repr=False)
    solids: int
    faces: int
    roots_in_file: int = 1             # STEP/IGES: root entities offered for transfer
    roots_transferred: int = 1         # roots that produced at least one face

@dataclass(frozen=True, eq=False)
class Mesh:
    vertices: np.ndarray               # (n, 3) float64, mm
    triangles: np.ndarray              # (m, 3) int32, counter-clockwise seen from outside
    face_ids: np.ndarray               # (m,) int32: 1-based B-rep face of each triangle; 0 = read from a mesh file
    deflection_mm: float | None        # chord error bound of a tessellation; None for a mesh file

# files
def read_solid(path: Path) -> Solid: raise NotImplementedError
def read_mesh(path: Path) -> Mesh: raise NotImplementedError                  # binary and ASCII STL, numpy only
def write_step(solid: Solid, path: Path, *, product_name: str) -> None:
    """XCAF writer (product named by a TDataStd_Name) and a fixed FILE_NAME header: the same shape gives the same bytes."""
    raise NotImplementedError
def write_brep(solid: Solid, path: Path) -> None: raise NotImplementedError
def write_mesh(mesh: Mesh, path: Path, *, fmt: Literal["stl", "glb"], name: str) -> None:
    """GLB in metres with +Y up (glTF), converted from mm and +Z up."""
    raise NotImplementedError
def triangulate(solid: Solid, deflection_mm: float) -> Mesh: raise NotImplementedError   # per-face nodes, not welded

# queries
@dataclass(frozen=True)
class MassProperties:
    volume_mm3: float
    area_mm2: float
    centroid_mm: Vec3
    bbox_min_mm: Vec3
    bbox_max_mm: Vec3

def mass_properties(model: Solid | Mesh) -> MassProperties: raise NotImplementedError

FaceKind = Literal["plane", "cylinder", "cone", "sphere", "torus", "bspline", "other"]

@dataclass(frozen=True)
class FaceInfo:
    id: int                            # 1-based TopExp_Explorer order: stable for one file and one OCCT version
    kind: FaceKind
    area_mm2: float
    sense: Literal["flat", "convex", "concave", "mixed"]   # cylinder: outward normal vs radial direction
    normal: Vec3 | None                # plane: outward unit normal
    axis: Vec3 | None                  # cylinder, cone, torus
    origin_mm: Vec3 | None             # a point on the plane or on the axis
    radius_mm: float | None
    bbox_min_mm: Vec3
    bbox_max_mm: Vec3

@dataclass(frozen=True)
class HoleInfo:
    id: str                            # "H1"...: ordered by axis, diameter, entry point
    face_ids: tuple[int, ...]
    diameter_mm: float
    axis: Vec3                         # drilling direction, into the material
    entry_mm: Vec3
    depth_mm: float                    # cylindrical depth, without a drill-point cone
    thru: bool
    counterbore: tuple[float, float] | None   # (diameter, depth)
    countersink: tuple[float, float] | None   # (diameter, included angle)

@dataclass(frozen=True)
class Inventory:
    faces: tuple[FaceInfo, ...]
    holes: tuple[HoleInfo, ...]
    edges: int
    vertices: int

def inventory(solid: Solid) -> Inventory: raise NotImplementedError

@dataclass(frozen=True)
class FaceRef: id: int
@dataclass(frozen=True)
class HoleRef: id: str
@dataclass(frozen=True)
class PointRef: xyz_mm: Vec3
Entity = FaceRef | HoleRef | PointRef

def distance(solid: Solid, a: Entity, b: Entity) -> float: raise NotImplementedError      # BRepExtrema, mm
def angle(solid: Solid, a: Entity, b: Entity) -> float: raise NotImplementedError         # normals/axes, rad
def wall_thickness(solid: Solid, at_mm: Vec3, direction: Vec3) -> float | None: raise NotImplementedError
def ray_hits(solid: Solid, origins: np.ndarray, direction: Vec3) -> list[np.ndarray]:
    """Sorted exact hit parameters per ray (IntCurvesFace_ShapeIntersector)."""
    raise NotImplementedError
def validity(solid: Solid) -> tuple[str, ...]: raise NotImplementedError                 # BRepCheck problems; () = valid

# projection
@dataclass(frozen=True)
class Line2: a: Vec2; b: Vec2
@dataclass(frozen=True)
class Arc2: center: Vec2; radius: float; start_rad: float; end_rad: float              # a full circle spans 2*pi
@dataclass(frozen=True)
class Ellipse2: center: Vec2; major_axis: Vec2; ratio: float; start_rad: float; end_rad: float
@dataclass(frozen=True)
class Poly2: points: tuple[Vec2, ...]                                                  # B-splines, sampled at 0.005 mm
Prim2 = Line2 | Arc2 | Ellipse2 | Poly2

@dataclass(frozen=True)
class Projection:
    view: ViewName
    direction: Vec3                    # from the eye towards the part
    x_axis: Vec3                       # model direction drawn as view +x
    y_axis: Vec3                       # model direction drawn as view +y
    visible: tuple[Prim2, ...]         # sharp edges and silhouettes
    hidden: tuple[Prim2, ...]          # with every piece that coincides with a visible one removed
    bbox_min: Vec2                     # model mm in view coordinates
    bbox_max: Vec2
    dropped: dict[str, int]            # {"hidden_coincident": n, "tangent": n, "seam": n}
    method: Literal["exact", "polygonal"]

def project(solid: Solid, view: ViewName, *, hidden: bool, exact_face_limit: int = 5000) -> Projection:
    # TODO: HLRBRep_Algo with HLRAlgo_Projector(gp_Ax2(origin, -direction, x_axis)).
    #   visible = VCompound + OutLineVCompound; hidden = HCompound + OutLineHCompound.
    #   Drop a hidden piece when every sample (and segment midpoint) lies within 1e-3 mm of a visible polyline.
    #   Drop smooth (Rg1) tangent edges. Drop a seam edge unless it lies on a silhouette
    #   (surface normal at the seam perpendicular to the view direction): a silhouette can come back as the seam.
    #   More than exact_face_limit faces: HLRBRep_PolyAlgo on a tessellation, method="polygonal", and a warning.
    raise NotImplementedError

# modelling (part.py is the only caller)
@dataclass(frozen=True)
class Rect2: size: Vec2; at: Vec2; centered: bool
@dataclass(frozen=True)
class Circle2: diameter: float; at: Vec2
@dataclass(frozen=True)
class Polygon2: points: tuple[Vec2, ...]
@dataclass(frozen=True)
class Slot2: length: float; width: float; at: Vec2; angle: float
Profile2 = Rect2 | Circle2 | Polygon2 | Slot2

@dataclass(frozen=True)
class SketchPlane:
    name: Literal["xy", "xz", "yz"]    # (u, v) = (x, y) | (x, z) | (y, z); positive distance along +z | +y | +x
    offset: float

@dataclass(frozen=True)
class EdgeSelector:
    text: str                          # as the caller wrote it, echoed in errors
    terms: tuple[str, ...]             # validated terms, intersected

def parse_edge_selector(text: str) -> EdgeSelector: raise NotImplementedError   # pure; input.invalid on bad terms
def make_box(size: Vec3, at: Vec3) -> Solid: raise NotImplementedError
def make_cylinder(diameter: float, height: float, at: Vec3, axis: Vec3) -> Solid: raise NotImplementedError
def make_prism(profile: Profile2, plane: SketchPlane, distance: float) -> Solid: raise NotImplementedError
def make_revolution(profile: Profile2, plane: SketchPlane, axis: Literal["u", "v"], angle: float) -> Solid:
    raise NotImplementedError
def make_hole_tool(entry: Vec3, axis: Vec3, diameter: float, depth: float | None,
                   counterbore: tuple[float, float] | None, countersink: tuple[float, float] | None,
                   reach_mm: float) -> Solid: raise NotImplementedError        # depth None = through: reach past the part
def combine(base: Solid, tool: Solid, mode: Literal["add", "cut", "intersect"]) -> Solid: raise NotImplementedError
def fillet(solid: Solid, edges: EdgeSelector, radius: float) -> tuple[Solid, int]: raise NotImplementedError  # (result, edges used)
def chamfer(solid: Solid, edges: EdgeSelector, distance: float) -> tuple[Solid, int]: raise NotImplementedError
# Kernel failures raise ToolFailure('geometry.build_failed'); an empty selection raises
# ToolFailure('geometry.empty_selection', details={'selector', 'matches_per_term'}).

# ---------------------------------------------------------------- eng_toolkit/domains/geometry/part.py
"""The part spec language: one module owns its grammar, its validation and its interpretation."""

Mode = Literal["add", "cut", "intersect"]

@dataclass(frozen=True)
class Box: name: str; size: Vec3; at: Vec3; mode: Mode
@dataclass(frozen=True)
class Cylinder: name: str; diameter: float; height: float; at: Vec3; axis: AxisName; mode: Mode
@dataclass(frozen=True)
class Extrude: name: str; plane: SketchPlane; profile: Profile2; distance: float; mode: Mode
@dataclass(frozen=True)
class Revolve: name: str; plane: SketchPlane; profile: Profile2; axis: Literal["u", "v"]; angle: float; mode: Mode
@dataclass(frozen=True)
class Hole:
    name: str
    at: tuple[Vec3, ...]               # entry points
    axis: AxisName                     # drilling direction
    diameter: float
    depth: float | None                # None = "thru"
    counterbore: tuple[float, float] | None
    countersink: tuple[float, float] | None
    thread: str                        # e.g. "M6x1-6H": a callout, not modelled geometry
    fit: str                           # e.g. "H7": a callout
@dataclass(frozen=True)
class Fillet: name: str; edges: EdgeSelector; radius: float
@dataclass(frozen=True)
class Chamfer: name: str; edges: EdgeSelector; distance: float
Feature = Box | Cylinder | Extrude | Revolve | Hole | Fillet | Chamfer

@dataclass(frozen=True)
class OpDef:
    name: str
    role: Literal["body", "feature", "modifier"]
    fields: dict[str, str]             # field -> "length" | "angle" | "length[3]" | "profile" | "selector" | ...
    summary: str
    parse: Callable[[dict[str, Any], "Resolver", str], Feature]   # str = JSON pointer of the feature

OPS: dict[str, OpDef]                  # the one list: the tool description, errors and docs are generated from it

class Resolver:
    """Resolves '$name' parameters, then evaluates the quantity with units.parse_quantity (pint, no eval)."""
    def length(self, raw: Any, pointer: str) -> float: raise NotImplementedError   # mm
    def angle(self, raw: Any, pointer: str) -> float: raise NotImplementedError    # rad

@dataclass(frozen=True)
class PartSpec:
    features: tuple[Feature, ...]      # mm and rad, parameters substituted
    params_mm: dict[str, float]
    canonical: str                     # canonical JSON of the parsed spec; sha256 = spec hash

def parse_spec(text: str) -> PartSpec:
    """input.invalid with details.pointer ('/features/2/diameter') and the allowed fields of that op."""
    raise NotImplementedError

@dataclass(frozen=True)
class FeatureRecord: index: int; name: str; op: str; volume_after_mm3: float; edges_used: int | None

@dataclass(frozen=True)
class BuiltPart:
    solid: Solid
    records: tuple[FeatureRecord, ...]
    holes: tuple[Hole, ...]

def build(spec: PartSpec) -> BuiltPart:
    """First feature must be a body in mode add. geometry.build_failed / geometry.empty_selection carry
    {feature_index, feature_name, op}; the final shape must be one valid solid (geometry.invalid_result)."""
    raise NotImplementedError

def features_document(spec: PartSpec, built: BuiltPart, step_sha256: str) -> dict[str, Any]:
    """features.json: declared holes (positions, diameter, depth, cbore/csk, thread, fit) bound to the STEP by hash."""
    raise NotImplementedError

# ---------------------------------------------------------------- eng_toolkit/domains/geometry/dfm.py
PROCESSES: tuple[str, ...] = ("cnc_3axis", "injection_molding", "additive")
Status = Literal["pass", "fail", "review", "unverified"]

@dataclass(frozen=True)
class Limit: key: str; value: float; unit: Literal["mm", "rad", "ratio", "mm2"]; source: str; overridden: bool

@dataclass(frozen=True)
class Finding:
    rule: str
    status: Status
    measured: float | None
    limit: float | None
    unit: str
    where_mm: Vec3 | None
    face_ids: tuple[int, ...]
    source: str
    note: str

@dataclass(frozen=True)
class DfmReport:
    process: str
    directions: tuple[Vec3, ...]
    findings: tuple[Finding, ...]
    facts: dict[str, float]            # min_wall_thickness, min_draft_angle, overhang_area, ...
    coverage: dict[str, str]           # check -> "performed" | reason it was not
    limits: tuple[Limit, ...]

def limits_for(process: str, overrides: dict[str, Any]) -> dict[str, Limit]: raise NotImplementedError
def check(model: Solid | Mesh, process: str, directions: tuple[Vec3, ...], limits: dict[str, Limit],
          spacing_mm: float) -> DfmReport:
    """Pass/fail only where a limit exists; a check without a limit is 'unverified' with the measured fact."""
    raise NotImplementedError

# ---------------------------------------------------------------- eng_toolkit/domains/drawing/sheet.py
SHEETS: dict[str, Vec2] = {"A4": (297.0, 210.0), "A3": (420.0, 297.0), "A2": (594.0, 420.0),
                           "A1": (841.0, 594.0), "A0": (1189.0, 841.0)}             # ISO 5457, landscape
SCALES: tuple[tuple[int, int], ...] = ((1, 1), (1, 2), (1, 5), (1, 10), (1, 20), (1, 50),
                                       (2, 1), (5, 1), (10, 1))                    # ISO 5455
Method = Literal["first_angle", "third_angle"]

@dataclass(frozen=True)
class TitleBlock:
    title: str
    part_number: str
    material: str
    revision: str
    general_tolerance: str
    notes: tuple[str, ...]
    status: str = "UNREVIEWED - generated by eng-toolkit, not engineering approved"

@dataclass(frozen=True)
class PlacedView: projection: Projection; origin: Vec2; label: str      # origin: sheet mm of view (0, 0)

@dataclass(frozen=True)
class Dim:
    kind: Literal["linear", "diameter", "radius", "ordinate"]
    view: str
    p1: Vec2                           # sheet mm, definition points on the geometry
    p2: Vec2
    line_at: Vec2                      # sheet mm, position of the dimension line or text
    angle_deg: float
    value_mm: float                    # true size
    text: str                          # "<>" = measured value; callouts such as "Ø10 H7 THRU"
    source: str                        # "bbox:x" | "hole:H1"

@dataclass(frozen=True)
class Drawing:
    sheet: str
    size_mm: Vec2
    scale: tuple[int, int]
    method: Method
    views: tuple[PlacedView, ...]
    dims: tuple[Dim, ...]
    title: TitleBlock
    warnings: tuple[str, ...]

def compose(solid: Solid, inv: Inventory, *, views: tuple[ViewName, ...], method: Method, sheet: str, scale: str,
            hidden: bool, dimensions: Literal["auto", "overall", "none"], title: TitleBlock,
            declared: tuple[dict[str, Any], ...] = ()) -> Drawing:
    """drawing.layout_failed when no standard scale fits the requested sheet (details name the scale that would)."""
    raise NotImplementedError

# ---------------------------------------------------------------- eng_toolkit/domains/drawing/dxf.py
DXF_VERSIONS: tuple[str, ...] = ("R12", "R2000", "R2004", "R2007", "R2010", "R2013", "R2018")

def write_dxf(drawing: Drawing) -> bytes:
    """Layers FRAME TITLE VISIBLE HIDDEN(DASHED) CENTER DIM NOTES; sheet mm, $INSUNITS mm, DIMLFAC = 1/scale;
    real DIMENSION entities, rendered blocks; fixed header and metadata so the same drawing gives the same bytes."""
    raise NotImplementedError

@dataclass(frozen=True)
class Render:
    data: bytes
    page_mm: Vec2
    scale: float
    drawn: int
    skipped: dict[str, int]
    approximations: tuple[str, ...]

def render(dxf_path: Path, fmt: Literal["pdf", "svg"], *, sheet: str, scale: str, layout: str = "Model") -> Render:
    """ezdxf Frontend -> our PDF backend (or ezdxf's SVGBackend) on a layout.Page: true scale, black ink."""
    raise NotImplementedError

@dataclass(frozen=True)
class DimInfo: kind: str; value: float; measurement: float; dimlfac: float; text: str; layer: str; at: Vec2
@dataclass(frozen=True)
class TextInfo: text: str; height: float; at: Vec2; layer: str; block: str
@dataclass(frozen=True)
class LayerInfo: name: str; entities: int; linetype: str; on: bool; frozen: bool

@dataclass(frozen=True)
class DxfSummary:
    version: str
    units: str | None                  # pint unit name from $INSUNITS; None = unitless
    extents: tuple[Vec2, Vec2] | None
    layers: tuple[LayerInfo, ...]
    counts: dict[str, int]
    dims: tuple[DimInfo, ...]
    texts: tuple[TextInfo, ...]
    title_block: dict[str, str]
    title_block_source: Literal["attributes", "region", "none"]
    layouts: tuple[str, ...]
    unsupported: dict[str, int]
    recovered: bool

def read_dxf(path: Path) -> DxfSummary: raise NotImplementedError
def convert_version(src: Path, version: str) -> tuple[bytes, tuple[str, ...]]: raise NotImplementedError

# ---------------------------------------------------------------- eng_toolkit/domains/drawing/pdf.py
@dataclass(frozen=True)
class Word: text: str; x_mm: float; y_mm: float; height_mm: float
@dataclass(frozen=True)
class PdfPage:
    number: int
    size_mm: Vec2
    sheet_guess: str | None
    words: tuple[Word, ...]
    vectors: dict[str, int]            # lines, curves, rects
    images: int
    dimension_tokens: tuple[str, ...]
    title_block_text: tuple[str, ...]
@dataclass(frozen=True)
class PdfSummary: pages: tuple[PdfPage, ...]; page_count: int; text_layer: bool

def read_pdf(path: Path, *, pages: str) -> PdfSummary: raise NotImplementedError
def rasterize(pdf_path: Path, *, dpi: int, page: int = 1) -> bytes: raise NotImplementedError   # PNG via pypdfium2

# ---------------------------------------------------------------- eng_toolkit/domains/drawing/dwg.py
@dataclass(frozen=True)
class DwgConversion: dxf_path: Path; tool_path: str; tool_version: str; stderr_tail: str

def to_dxf(dwg_path: Path, out_dir: Path, *, timeout_s: float = 120.0) -> DwgConversion:
    """Run dwg2dxf (licenses.EXTERNAL['dwg2dxf']) as a separate process: stdin closed, stdout and stderr piped
    (never inherited: our stdout is the MCP channel), killed at the timeout. engine.unavailable when not located."""
    raise NotImplementedError
```

### The part spec language

```json
{
  "params":   {"L": "100 mm", "W": "50 mm", "T": "20 mm", "pitch": "$L / 2"},
  "features": [ {"op": "...", "name": "...", ...}, ... ]
}
```

- **Parameters.** Every value is a quantity string. `$name` refers to a parameter defined earlier in `params` (a cycle or a forward reference is `input.invalid`). The substituted text, e.g. `(100 mm) / 4`, is evaluated by the existing gatekeeper `units.parse_quantity`, which is pint's own expression parser and not Python `eval`. **(measured)**: `(100 mm) / 2 - 5 mm` gives 45 mm; `__import__("os")...` and `sqrt(4)` are rejected; `(10 mm) + (2 s)` is a dimension error; `1e400 mm` becomes inf, which the gatekeeper already rejects. Expressions are at most 200 characters.
- **Model.** There is one current solid. The first feature must be a body. A later body has `mode` `add` (default), `cut` or `intersect`. A hole cuts. Fillet and chamfer modify the current solid.

| op | role | fields (lengths and angles are unit strings) |
| --- | --- | --- |
| `box` | body | `size` [x, y, z], `at` [x, y, z] min corner (default origin), `mode` |
| `cylinder` | body | `diameter`, `height`, `at` (base centre), `axis` (`+z` default), `mode` |
| `extrude` | body | `plane` (`xy`, `xz`, `yz`), `offset`, `profile`, `distance` (signed), `mode` |
| `revolve` | body | `plane`, `profile`, `axis` (`u` or `v` of the plane, through its origin), `angle` (`360 deg` default), `mode` |
| `hole` | feature | `at` (list of entry points [x, y, z]), `axis` (drilling direction, `-z` default), `diameter`, `depth` (length or `"thru"`), `counterbore` {diameter, depth}, `countersink` {diameter, angle}, `thread` (callout text), `fit` (callout text) |
| `fillet` | modifier | `edges` (selector), `radius` |
| `chamfer` | modifier | `edges` (selector), `distance` |

Profiles: `{"rect": {"size": [u, v], "at": [u, v], "centered": true}}`, `{"circle": {"diameter": d, "at": [u, v]}}`, `{"polygon": {"points": [[u, v], ...]}}`, `{"slot": {"length", "width", "at", "angle"}}`.

Edge selectors are terms joined by `&` (intersection): `all`; `|x` `|y` `|z` (straight edges parallel to the axis); `>x` `<x` `>y` `<y` `>z` `<z` (edges lying in the extreme plane of the part's bounding box); `convex` (outside corner) and `concave` (inside corner), from the dihedral angle of the two faces. A smooth junction (dihedral 180°, such as a cylinder's seam line) is neither, so `|z & convex` does not catch the seam line of a vertical hole, which `|z` alone would. `line` and `circle` complete the set. **(measured, with an equivalent hand-written predicate)**: on the probe block, filleting the 4 outer vertical edges gives a volume that matches the hand calculation to 1.5e-11 mm³. An empty selection is an error that lists how many edges each term matched, so the agent can repair the selector.

Round 2 adds `pattern_linear`, `pattern_circular`, `mirror` (each re-applies earlier features' tool solids, transformed), `shell`, `path` profiles with arcs, `of:<feature>` selectors (through OCCT's boolean history), tapered extrude (draft), and `import` (start from an existing STEP so an agent's own build123d body can receive spec holes).

### Q4: why the server does not execute code, and the exact design if the owner overrides

**Decision.** The toolkit ships `geometry_make_part(spec)` and no code-execution tool.

1. **Not protectable on a clean Windows install. (measured)** A Windows Job Object enforces a memory limit (MemoryError at 512 MB), blocks child processes (WinError 1816 for a grandchild; `os.system` returns -1), and lets a wall-clock kill work (an infinite loop terminated at 3.01 s). It does **not** block network (a TCP connect to a local listener succeeded) and does **not** confine files (a write outside the working directory succeeded). Python audit hooks and AST allow-lists add speed bumps for Python-level `open`, `socket` and `subprocess`. PEP 578 says audit hooks are not a sandbox, and native code bypasses them: OCP's own STEP reader and writer open any path, and so does ctypes. Real confinement needs an AppContainer or restricted token, a separate low-privilege account, a container or a VM. None of these is part of a clean Windows install driven by `uv sync`.
2. **It launders permissions.** Claude Code, Codex and Cursor ask before each shell command. Once a user allows `geometry_make_from_code`, every later code string runs unreviewed under the user's rights, including over `--transport http`, where any local process can call it. A capable agent gains no capability, because it already has a shell, but it gains a way around review.
3. **Small local models**, an explicit target, fill a seven-op JSON schema with per-field error pointers more reliably than they write working build123d.
4. **Units end to end.** build123d code is unit-less floats. The spec keeps every quantity a unit string through to the kernel.
5. **Licence hygiene.** The spec runs on OCP alone. build123d would add `ocpsvg` and `bd_materials`, which declare no licence (grounding). The doctor's environment scan reports unknown licences as warnings.
6. **Determinism and provenance.** The canonical spec is the input. The interpreter is ours and versioned. STEP bytes repeat **(measured)**.
7. **Intent.** The spec knows a hole is a hole (diameter, depth, counterbore, thread, fit). Drawings and DFM use that intent instead of guessing it back from faces.

**What this costs.** We maintain a small CAD language, and v1 has no lofts, sweeps, splines or thread geometry. The design document's "the agent edits Python source" becomes "the agent edits the spec's `params` and `features`". Agents with a shell keep the Python route outside the server: they write build123d, run it in their own sandbox, and call `geometry_measure`, `geometry_check_dfm` and `drawing_make_views` on the STEP. Every downstream tool accepts any STEP.

**If the owner overrides**, build exactly this and nothing looser:
- `geometry_make_from_code(code, params, timeout, memory)` is registered only when the server starts with `ENG_TOOLKIT_ALLOW_CODE=1`, which a tool parameter cannot set, and never under `--transport http`. Its description states "executes Python with your user rights".
- One fresh child per call: `python -I`, cwd = the job dir, an environment reduced to `SYSTEMROOT`, `TEMP` and `PATH`, stdin closed, stdout and stderr to files in the job dir (never inherited, because our stdout is the MCP channel).
- A Job Object with `ProcessMemoryLimit` (default 2 GB), `ActiveProcessLimit = 2` and `KILL_ON_JOB_CLOSE`, plus a wall-clock timeout via `TerminateJobObject` (default 60 s). The limit is 2 because the uv venv's `python.exe` is a launcher that starts the base interpreter as a child **(measured)**: with a limit of 1 the child fails with "Unable to create process".
- A bootstrap that imports build123d first, then installs an audit hook refusing `subprocess.Popen`, `os.system`, `socket.connect`, `ctypes.dlopen` and `open` for writing outside the job dir.
- The script must bind `result` to a shape. The child exports STEP with brep, and the parent re-reads and measures it.
- Honestly not protected: reading any file the user can read, network from native code, writes by native code, and anything a determined author does to get around the audit hook.
- Cost: a cold child with OCP imported is 0.64 s **(measured)**. build123d's import time is not measured.

### Q5: how drawings are produced and read

**Projection.** `brep.project` runs exact `HLRBRep_Algo` per view. **(measured)**: on the probe part, 1 to 2 ms per view; on a 198-face plate with 96 holes and 192 fillet edges, 0.05 to 0.55 s per view. The top view's visible length is 327.9823 mm, equal to the hand value of 284 + 4π + 10π. OCCT returns every back edge that coincides with a front edge as hidden: in the top view all 327.98 mm of hidden output is coincident. Exact-key matching leaves 80 mm in the front view. A geometric coverage test (all samples within 1e-3 mm of a visible polyline) leaves 40.0000 mm, the two hole silhouettes at x = 20 and x = 30, equal to the hand value. One of those silhouettes comes back as the cylinder's seam edge, so seams are kept when they lie on a silhouette and dropped otherwise. Tangent (Rg1) edges are not drawn, as is usual in ISO practice. Output curves are lines, circles, ellipses (iso view) and B-splines (fillets seen edge-on). They become LINE, CIRCLE/ARC, ELLIPSE and LWPOLYLINE. Parts with more than `exact_face_limit` faces fall back to `HLRBRep_PolyAlgo` (only lines; 327.967 mm in the top view because of chord error) with a warning.

**Layout.** First or third angle around the front view. ISO 5457 frame (20 mm filing margin, 10 mm elsewhere). ISO 7200 title block, 180 mm wide, bottom right. The sheet and scale are the first pair that fits from: smallest sheet at 1:1; else the largest ISO 5455 reduction on A3; enlargement (2:1, 5:1, 10:1) when the views fill less than a quarter of A4 at 1:1. Each view reserves margins for its dimension rows (7 mm per row). If nothing fits: `drawing.layout_failed`, with the scale that would fit.

**Dimensioning rules (v1, deterministic).**
1. Each model axis is dimensioned once, in the first listed view where it lies in the view plane. With front, top, right: X and Z on the front view, Y on the top view.
2. Holes are grouped by diameter, axis, depth or thru, counterbore and countersink. Each group gets one callout (`4× Ø6.6 THRU`, `Ø5 DEEP 12`, `Ø11 CBORE DEEP 6.5`, with `H7` or `M6x1-6H` appended from `features.json` when its hash matches), placed in the view that looks along the hole axis. Hole positions are given as ordinate dimensions from the view's datum corner (bounding-box minimum).
3. Features on inclined faces are not dimensioned, and this is warned.
4. Values are measured, never typed: `DIMENSION` entities with `DIMLFAC = 1/scale`, so the displayed value is true size. **(measured)**: ezdxf's `get_measurement()` returns the raw geometric distance (50 for a 50 mm sheet distance), and with DIMLFAC 2 the rendered text says 100. `drawing_read` therefore reports measurement × effective DIMLFAC.
5. Remaining text overlaps are warned, not fixed.

**Outputs.**
- DXF R2018 through ezdxf. **(measured)**: by default the bytes differ per run (`$TDCREATE`, `$FINGERPRINTGUID`, `$VERSIONGUID`, ezdxf metadata timestamps); with `ezdxf.options.write_fixed_meta_data_for_testing = True` they are identical. The option's name says "testing", so it is pinned and guarded by a byte-determinism test.
- PDF from our own ezdxf backend. The frontend hands backends only lines, paths, filled paths and polygons: text arrives as filled paths **(measured)**, so the backend needs no font handling. A `layout.Page(297, 210, mm)` gives true scale, as ezdxf's SVG backend does (`width="297mm"`, deterministic **(measured)**). The backend is about 300 lines modelled on `ezdxf/addons/drawing/svg.py` (not yet written).
- SVG through ezdxf, black ink configured (the default was white on dark).
- PNG by rasterising the PDF with pypdfium2.
- ISO symbols: Ø, ± and ° render in Arial **(measured)**. ↧ ⌴ ⌵ are not available, so callouts use `DEEP`, `CBORE` and `CSK`.

**Reading.**
- DXF: `ezdxf.readfile`, falling back to `ezdxf.recover` with `recovered = true`. Title blocks come from block attributes (tags such as TITLE, DWG_NO, PART_NO, MATERIAL, REV, SCALE), otherwise from text in the bottom-right region of the extents, with the source named.
- DWG: LibreDWG `dwg2dxf` as a separate GPL process, then the DXF reader.
- PDF: pdfplumber words, vectors and images. **(measured)**: a PDF whose text is vector paths (ours, or SHX text from CAD) has 0 words; this is reported as `text_layer: false`.

**Honestly out of reach with permissive tools.**
- DWG writing: none. DXF is the output and the user converts with their own CAD.
- DWG reading without GPL: none. The ODA File Converter is proprietary freeware and is not integrated.
- PMI: OCP ships `XCAFDimTolObjects` and `STEPCAFControl` **(measured import only)**, but reading and writing AP242 semantic PMI is unverified. v1 neither reads nor writes PMI.
- Full GD&T (datum reference frames, MMC/LMC, zone evaluation): out of reach, as the design document says.
- Section, detail and auxiliary views: later; a section is a boolean with a half-space plus hatching.
- Complete automatic dimensioning: impossible without design intent. v1 dimensions size and holes.
- OCR of scanned drawings, and vector PDF to DXF: out of reach.
- SHX fonts: substituted with Arial (`isocp.shx` resolves to `arial.ttf` **(measured)**) and reported as a loss.

### Q3: engines, extras, licence tiers, installed size

All sizes are installed sizes measured in the scratch venv on Windows.

| Engine (dist, version) | Licence (metadata) | Tier | Extra | Installed size | Used by |
| --- | --- | --- | --- | --- | --- |
| cadquery-ocp-novtk 8.0.1.1.0 | Apache-2.0 wrapper; bundles OCCT LGPL-2.1 + exception | lgpl-dynamic (`bundled_license` as today) | `cad` | 169 MB (OCP 104 + .libs 65); wheel ~108 MB | brep: geometry_*, drawing_make_views, metrology_check_scan |
| cadquery-ocp-proxy 8.0.1.1.0 (new dependency of ocp-novtk) | Apache-2.0 | permissive (transitive) | `cad` | < 0.1 MB, empty proxy | none |
| ezdxf 1.4.5 (+ fonttools 4.67 MIT, pyparsing 3.3 MIT; numpy is core) | MIT (classifier) | permissive | `drawing` | 28.5 MB (16 + 12 + 0.5) | drawing/dxf.py |
| pdfplumber 0.11.10 (+ pdfminer.six MIT, Pillow MIT-CMU, cryptography Apache-2.0 OR BSD-3, cffi MIT-0, pycparser BSD-3, charset-normalizer MIT) | MIT | permissive | `drawing` | +42 MB together with pypdfium2 | drawing/pdf.py |
| pypdfium2 5.14.0 | "BSD-3-Clause, Apache-2.0, dependency licenses"; bundles PDFium and its third-party libraries | permissive, `bundled_license` noted (FreeType dual FTL/GPL, used under FTL) | `drawing` | 7.5 MB (in the 42) | PNG previews |
| open3d 0.20 | MIT | permissive | `scan` | 231 MB (grounding) | metrology only |
| LibreDWG dwg2dxf | GPL-3.0-or-later | gpl-isolated: `ExternalTool`, separate process, never installed or imported | none (user installs) | n/a | drawing_read / drawing_convert for DWG |

`pyproject.toml` extras:
```toml
cad = ["cadquery-ocp-novtk>=8.0.1.1.0"]
drawing = ["ezdxf>=1.4.5", "pdfplumber>=0.11", "pypdfium2>=5"]
scan = ["eng-toolkit[cad]", "open3d>=0.20.0"]
```
A full install is about 470 MB; `scripts/install.ps1` uses `--all-extras`. The `drawing` extra is about 70 MB. `drawing_make_views` needs `cad` and `drawing`.

Deliberately not used:
- build123d: no-licence dependencies, and a code-first API.
- CadQuery: pins `cadquery-ocp` with VTK, which conflicts with the novtk wheel; I recall LGPL casadi and nlopt dependencies but did not verify them.
- matplotlib: 26 MB, 3.3 s for two renders, and the page came out at 293.5 × 345.6 pt instead of A4 **(measured)**.
- PyMuPDF: AGPL, the only ready-made ezdxf PDF backend.
- PyQt: GPL.
- FreeCAD TechDraw: GUI-bound.
- text-to-cad's `cadgen.eng_drawing`: script interface, so code execution again; its skill runs it under Python 3.13 with telemetry on by default.

### Q6: errors, determinism, regression tests

**Error families.**
- Existing: `units.bare_number`, `units.dimension_mismatch`, `units.parse_error`, `input.missing`, `input.invalid`, `input.file_not_found`, `input.unsupported_format`, `engine.unavailable`, `engine.failure`.
- New:
  - `geometry.build_failed`: an OCCT operation failed. **(measured)**: an impossible fillet gives `IsDone() == False`, and `Shape()` raises `StdFail_NotDone`. Details: feature_index, feature_name, op, occt_error.
  - `geometry.empty_selection`: a selector matched nothing; details give matches per term.
  - `geometry.invalid_result`: BRepCheck problems, or not exactly one solid.
  - `geometry.unknown_entity`: a query names F42 when the model has 11 faces.
  - `geometry.needs_brep`: a mesh was given to a B-rep-only operation.
  - `drawing.layout_failed`: the views do not fit.
  - `engine.timeout`: only where an operation can really be cancelled (the dwg2dxf process; HLR only if OCCT's user break works, see below).
- Spec errors are `input.invalid` with `details.pointer` (JSON pointer) and `details.allowed` (fields of that op, generated from `OPS`).

**Determinism.** Same inputs and the same engine versions give the same values and the same artifact bytes.
- STEP: XCAF writer with the product named by `TDataStd_Name` and a fixed `FILE_NAME` header. **(measured)**: plain `STEPControl_Writer` differs per write (timestamp, and the product name "Open CASCADE STEP translator 8.0 N" with a per-process counter); the `write.step.product.name` static did not take effect; the XCAF route gives identical bytes for three writes in one process and across two processes.
- GLB: `RWGltf_CafWriter` gives identical bytes **(measured)**, but writes mm and +Z up by default, so we set `SetInputLengthUnit(0.001)` and a Z-up input system (API present; effect not verified).
- DXF and SVG: as in Q5. PDF: deterministic by construction (no dates, ordered resources).
- Booleans run serial, because `SetRunParallel` may reorder faces, and face ids are part of the contract (unverified).
- Sampling in DFM is UV grids, never random.

**Provenance.** `inputs_hash` covers the parameters (unchanged). New `input_files` lists the sha256 of every file read. New `engine_versions` records OCP, ezdxf and so on. With the existing manifest's artifact hashes this gives the design document's "engine version, input hash, output SHA". A cache key can be derived from these fields; it is not stored separately (**single source of truth**). Face and hole ids are valid only for the file whose sha256 `geometry_measure` reports.

**Regression tests with hand calculations.** The probe part is the 100 × 50 × 20 mm block with a Ø10 through hole at (25, 25) and R2 vertical edges.

| Tool | Fixture and hand calculation |
| --- | --- |
| make_part | Volume 100000 − 25π·20 − 4·(4 − π)·20 = 98360.531085 mm³ (OCCT 98360.531085492 **(measured)**); area 16000 − 50π + 200π − 4·(80 − 20π) − 8·(4 − π) = 16395.699 mm² **(measured)**. Revolve a 10 × 30 rectangle about v: π·10²·30 = 9424.778. Counterbore Ø11 × 6.5 over Ø6.6 thru in a 20 mm plate: π/4·(11²·6.5 + 6.6²·13.5). Changing `L` by 10 mm changes the volume by exactly 10·50·20. Two builds give the same STEP sha256. An impossible fillet gives `geometry.build_failed` with feature_index 3; an unknown op gives `input.invalid` with pointer `/features/0/op`. |
| measure | Volume and area as above; `mass` at 2700 kg/m³ = 0.265573 kg. The inventory has 1 hole (Ø10, thru, depth 20) and 4 convex cylinders R2 **(measured classification)**. `distance <x0 face> H1` = 25 − 5 = 20; `angle <x0 face> <top face>` = 90 deg; `area <hole face>` = π·10·20 = 628.3185; `thickness P(10 mm, 10 mm, 30 mm) -z` = 20 (exact ray hits at 10 and 30 **(measured)**). Ids are taken from the first call's inventory, not hard-coded. |
| check_dfm | cnc: a 40 × 20 × 10 pocket with sharp corners gives 4 fails "internal radius 0 mm < 3 mm"; with `concave & |z` fillet R3, 4 passes measuring 3.000. Hole depth/diameter 20/10 = 2.0. A side hole along x with access `+z` only gives `review`. Molding, pull `+z`: the four side faces have draft 0° and fail against 1°; top and bottom pass. Wall: a 3 mm plate gives min wall 3.000. Additive: a 20 × 20 × 20 post with a 60 × 20 × 5 arm on top gives overhang area 2·(20·20) = 800 mm². |
| convert | Box to STL: 12 triangles, mesh volume exactly 100000. Cylinder Ø20 × 30 at 0.01 mm: 0 < V_exact − V_mesh ≤ 2π·10·0.01·30 = 18.85 mm³. GLB of the box: accessor max 0.1 (metres), height 20 mm on glTF +Y = 0.02. |
| make_views | Top view visible length 327.9823 **(measured)**; front view hidden length 40.0000 **(measured after dedup)**. DXF read back: 100 (X), 20 (Z), 50 (Y), Ø10 THRU, ordinates x 0/25 and y 0/25. PDF page 841.89 × 595.28 pt (A4). Two runs give identical DXF and PDF bytes. Without the `cad` extra: `engine.unavailable` naming `uv sync --extra cad`. |
| read | DXF built in the test: rectangle on VISIBLE; a linear dim of 100 mm with DIMLFAC 2 reads value 200 and measurement 100 (hand 100 × 2); a diameter dim of radius 5 reads 10; attributes TITLE=BRACKET, DWG_NO=BR-001 give title_block source `attributes`; `$INSUNITS 4` gives millimetre; `$INSUNITS 0` gives a warning. PDF written by hand in the test (Helvetica "Ø10 H7" at a known position plus one 283.465 pt line) gives words [Ø10, H7], token `Ø10 H7`, 1 line, sheet guess A4. DWG: a fake `dwg2dxf` script tests the wrapper; a real conversion test is skipped unless the tool is located. |
| drawing_convert | A 100 mm LINE to PDF at 1:1: pdfplumber line length 100/25.4·72 = 283.465 pt ± 0.01. SVG root `width="297mm"`. DXF to R12: entities that R12 lacks are listed in losses. |

Every tool also gets the stdout-silence test check_scan has (`capfd.readouterr().out == ""` across success and failure) and a non-ASCII path test.

### Interface depth

Seven tools with flat string, bool and int parameters hide:
- B-rep booleans and fillets with history-free geometric selectors;
- an XCAF writer with a fixed header;
- exact HLR with coincidence, tangent and seam cleanup;
- ISO sheet, scale and view arrangement;
- dimension rules with true-size DIMLFAC;
- a PDF backend;
- exact ray casting for walls, undercuts and overhangs;
- DWG conversion through a GPL process boundary.

The caller learns two small grammars: the part spec, which is the domain itself, and the measure queries, about six verbs. No tool exposes an engine stage: there is no "project then layout then render" sequence for the caller to coordinate. The modelling API inside `brep` is wide, about 30 names, but it is internal: one caller (`part.py`), and every function hides real OCP knowledge (OCP 8 renames, translator globals, failure modes).

## Synthesis decision

*Placeholder: filled in by arena.*

## Tradeoffs accepted

- We accept owning a small CAD language and its expressiveness ceiling, in exchange for no code execution, unit strings end to end, an OCP-only licence footprint, byte-reproducible STEP, and declared feature intent that drawings and DFM can use.
- We accept that agents who want build123d run it in their own shell, in exchange for keeping the client's permission prompt in force.
- We accept our own ~300-line PDF backend, in exchange for no matplotlib (26 MB, slow import, wrong page size by default) and no AGPL PyMuPDF. PDF text is vector paths, not searchable; the DXF carries the semantics.
- We accept rule-generated dimensions that cover overall size and holes only, in exchange for drawings that are always derived from geometry and reproducible. They are not complete manufacturing drawings, and the title block says UNREVIEWED.
- We accept that exact HLR and booleans run in the server's worker thread without a proven cancel, guarded by a face-count limit and a polygonal fallback, in exchange for no subprocess round trip per view (0.64 s cold).
- We accept that DFM ships with an empty default limit table until each value has a checked source, in exchange for never presenting an invented number as a rule. The facts and caller-supplied limits work from the first slice.
- We accept face ids that are valid only for one file and one OCCT version, bound by sha256, in exchange for a query grammar small models can use.
- We accept DWG as read-only through a user-installed GPL program, in exchange for staying inside the licence policy.
- We accept relying on an ezdxf option named `write_fixed_meta_data_for_testing`, pinned and covered by a determinism test, in exchange for byte-identical DXF.
- We accept three extras, in exchange for a default install that stays as small as today. Install hints are derived from the registry, so the extra a tool needs is never typed by hand.
- We accept a small contract change (`Provenance.input_files`, `engine_versions`, optional with defaults), in exchange for provenance that covers file contents and engines in one place instead of per-tool `data.input_files`.

## Alternatives considered

1. **`geometry_make_from_code` in a sandboxed child process.** This is the design document's shape, and Q4 specifies it. Its interface is the smallest possible (one string of code). It hides nothing, though: the caller must know build123d, units disappear, and errors come back as tracebacks. The real complexity it exposes is security, which it cannot hide. A Job Object leaves network and files open **(measured)**, and the tool gets around the client's permission prompt. It lost on safety and on depth, not on simplicity.
2. **build123d (or CadQuery) as the kernel behind the spec.** This means less of our own OCP code. But it brings no-licence dependencies (build123d) or a VTK and OCP conflict (CadQuery), plus two kernel wrappers to keep in step. Caller-visible depth is the same. It lost on licences and dependency weight.
3. **cadgen (text-to-cad) for drawings.** It has good output (sheets, callouts, deterministic PDF). Its interface is a Python script with a decorator, so using it needs code execution or code generation, a Python 3.13 runtime, and telemetry off. We borrow its ideas (an edge drawn once, no hidden tangent lines, dimensions measured from geometry, no creation date in the PDF) and reimplement them on OCP and ezdxf.
4. **A neutral 2D model written separately to DXF and to PDF.** This gives simpler PDF code. But a dimension drawn twice by two writers drifts, and the DXF loses real `DIMENSION` entities if we flatten them. It lost on single source of truth.
5. **brep inside the geometry domain, with metrology importing `domains.geometry`.** This means one fewer top-level package. But domains would depend on each other, and the B-rep owner would sit with one of its three consumers. It lost on layering, because brep is infrastructure like `units.py`.
6. **One `convert` tool for 2D and 3D, as the draft's `drawing.convert`.** This means one fewer tool. But one name would front four kinds of knowledge and a format matrix the caller must learn anyway. It lost on cohesion.
7. **matplotlib as the PDF renderer.** It needs no backend code. But it costs 26 MB, 3.3 s for two renders including import, and true scale is not the default. It is kept as the fallback if our backend stalls.

## Open questions and risks

- Do you accept that the server never executes model-written code, and that "the agent edits Python" becomes "the agent edits the spec", with build123d staying in the agent's own shell? If not, is the gated design in Q4 (operator env flag, stdio only, Job Object, honest residual risk) acceptable?
- Should the default projection be `first_angle` (ISO) or `third_angle` (JIS and ASME practice; common in Japanese and US supply chains)?
- May `Provenance` grow two optional fields (`input_files`, `engine_versions`)? It touches every tool's output schema. Should check_scan's `data.input_files` then be removed in the same change?
- Which sources are acceptable for default DFM limits (supplier design guides with access dates, textbooks, nothing)? Until you decide, the table ships empty and checks without a caller limit report `unverified`.
- Should ezdxf (28.5 MB, pure Python) be a core dependency so `drawing_read` works on DXF in the default install?
- Should LibreDWG be mentioned at all while the product's positioning (personal, company-internal, commercial) is open? It is GPL, even as a separate process.
- Risk: exact HLR on parts with thousands of faces is untested; the `exact_face_limit` of 5000 is a guess.
- Risk: OCCT operations cannot be interrupted from Python unless a `Message_ProgressIndicator` user break works through OCP (unverified). A pathological boolean or HLR blocks one FastMCP worker thread until it ends.
- Risk: the hole recogniser (thru, blind, drill point, counterbore, countersink) and the seam-on-silhouette rule are designed, not proven. They need fixtures beyond the probe part.
- Risk: OCCT's translator statics are global. The brep lock covers reads and writes, and check_scan's existing reader joins it in slice 1.

## Next implementation step

Create `eng_toolkit/brep/` with `read_solid` and `triangulate` moved out of `metrology/scan.py`, plus `errors.ToolFailure`. Switch `scan.read_model_mesh` to them. Add `tests/test_engine_ownership.py` and `tests/test_brep.py` (box volume 100000, two roots counted). Run the check_scan suite unchanged.

## Engine facts I measured

Environment: `uv venv --python 3.12 C:\Users\USER\ep-arena-1`, then `uv pip install --python ...\Scripts\python.exe ezdxf`, `cadquery-ocp-novtk`, `pdfplumber`, `matplotlib`, `pint`. Scripts ran with `python -I <probes>\pN_*.py` from `C:\Users\USER\ep-arena-1\work`.

| # | Command (probe) | Result |
| --- | --- | --- |
| 1 | `du` after each install | ezdxf 16 MB + fontTools 12 MB + pyparsing 0.5 MB (numpy 43 MB already core). cadquery-ocp-novtk 8.0.1.1.0: OCP 104 MB + `cadquery_ocp_novtk.libs` 65 MB, and it now pulls `cadquery-ocp-proxy` 8.0.1.1.0 (Apache-2.0, empty). pdfplumber stack +42 MB (pdfminer 8.5, cryptography 11, PIL 15, pypdfium2_raw 7.3). matplotlib stack +26 MB. |
| 2 | `importlib.metadata` licence fields | ezdxf: MIT classifier, no expression. pdfplumber: MIT classifier. pdfminer.six: MIT. pypdfium2: "BSD-3-Clause, Apache-2.0, dependency licenses". Pillow: MIT-CMU. cryptography: Apache-2.0 OR BSD-3-Clause. cffi: MIT-0. matplotlib and kiwisolver: long text, so the repo's classifier fallback applies (PSF / BSD). All classify as permissive with `licenses.classify_license`. |
| 3 | `p1_ocp_kernel.py` | OCP import 0.46 s. Box − cylinder boolean 5 ms. Volume after fillets 98360.531085492 vs hand 98360.531085492 (−1.5e-11). Area 16395.699052. Faces: 6 planes, 5 cylinders. RWGltf, RWStl, StlAPI, HLRBRep, IntCurvesFace, BRepOffsetAPI, STEPCAFControl, XCAFDoc, XCAFDimTolObjects, IGESControl and BRepExtrema all import. |
| 4 | `p1`/`p1b` STEP writes | `STEPControl_Writer`: lines differ per write (FILE_NAME timestamp; `PRODUCT('Open CASCADE STEP translator 8.0 N')` with a per-process counter). `APIHeaderSection_MakeHeader` fixes the header. `Interface_Static write.step.product.name` had no effect. The first write in each fresh process is identical across processes (sha 0fb3c3c5...). Read-back volume 98360.531085493. |
| 5 | `p1c_xcaf_step_glb.py` | `STEPCAFControl_Writer` + `TDataStd_Name`: `PRODUCT('bracket','bracket',...)`, three writes in one process identical. XCAF STEP reads back with the same volume and `BRepCheck_Analyzer` valid. `RWGltf_CafWriter`: 12.7 kB, 8 to 18 ms, two writes identical, coordinates written in mm and Z-up unchanged; `RWMesh_CoordinateSystemConverter.SetInputLengthUnit` and `SetInputCoordinateSystem` exist. |
| 6 | OCP 8 naming | `TopTools_ListOfShape` is gone (use `OCP.OCP.collections.List_TopoDS_Shape`). `TColStd_IndexedDataMapOfStringString` is gone (use `IndexedDataMap_TCollection_AsciiString_TCollection_AsciiString`). `Bnd_Box.Get()` raises "Unregistered type Bnd_Box::Limits" (use `CornerMin`/`CornerMax`). `TopoDS.Edge(...)` and `TopoDS.Face(...)` take no suffix. |
| 7 | `p2_hlr.py` | Exact HLR 1 to 2 ms per view. Top: visible sharp 327.9823 (4 lines, 5 circles), hidden sharp also 327.9823 (all coincident). Front: visible 240, hidden sharp 300 + outline 20, Rg1 40 + 40. Iso: ellipses. PolyAlgo: lines only, top 327.967, 4 to 7 ms. |
| 8 | `p2b_hlr_frame_and_scale.py` | With `gp_Ax2(origin, -direction, x)`: front spans X 0..100, Z 0..20; top X 0..100, Y 0..50; right Y 0..50, Z 0..20. 96-hole plate: one boolean 0.05 s; 192 fillet edges 0.08 s; 198 faces; exact HLR top 0.05 s, front 0.55 s, iso 0.21 s. |
| 9 | `p3_dxf_views.py` | DXF built in 9 ms. `get_measurement()` returns 100, 20, 100 (linear) and 10 (diameter). Header GUIDs and dates vary. SVG: 100 ms, `width="297mm"`, 0 `<text>` elements. matplotlib PDF: identical bytes with `CreationDate=None`, but 3.27 s for two renders and a 293.5 × 345.6 pt page. Backend calls seen: line, path, filled_paths (text), filled_polygon, solid_lines. |
| 10 | `p3b_determinism_dedup.py` + inline | Coverage dedup gives front hidden 40.0000 (x = 20 and x = 30 lines). The DXF still differs with fixed header vars (a GUID and the "1.4.5 @ timestamp" metadata); `ezdxf.options.write_fixed_meta_data_for_testing = True` makes it identical. SVG renders identical. pdfplumber on the matplotlib PDF: 0 words, 119 lines, 50 curves, 4 rects. |
| 11 | inline ezdxf | `add_linear_dim(..., override={"dimlfac": 2})` over 50 mm: `get_measurement()` 50, rendered text "100", `actual_measurement` None. `fonts.resolve_font_face("isocp.shx")` gives arial.ttf. Ø ± ° produce glyph paths; ↧ ⌴ ⌵ do not render as real glyphs (29 path commands for 3 symbols plus spaces, consistent with fallback boxes). No stdout output during font work. |
| 12 | `p5_measure_dfm.py` | Cylinder sense via outward normal vs radial direction: hole r=5 concave (360°, span 20); 4 fillets r=2 convex (90°). `IntCurvesFace_ShapeIntersector`: ray down at (10, 10) hits 10 and 30 (wall 20); ray along +y at x=25 hits 10, 30, 40, 60; 70,900 rays/s. |
| 13 | inline fillet | An impossible fillet on a 10 mm cube: `IsDone()` False, `Shape()` raises `StdFail_NotDone` "BRep_API: command not done". |
| 14 | `p4_jobobject.py` (ctypes) | Cold child + OCP import + box 0.64 s wall. 2 GB allocation under 512 MB: MemoryError. `ActiveProcessLimit=1`: "Unable to create process" (the uv venv `python.exe` is a launcher); with 2 the child runs. Grandchild: WinError 1816. `os.system`: -1. TCP connect to a local listener: succeeded. Write outside cwd: succeeded. Infinite loop: killed at 3.01 s by `TerminateJobObject`. The console code page is cp950, so child output must be decoded explicitly. |
| 15 | inline pint 0.26.1 | `(100 mm) / 2 - 5 mm` = 45 mm; `(100 mm) * 2 + (0.5 inch)` = 212.7 mm; `sqrt(4) * mm` gives UndefinedUnitError; `__import__("os")...` gives AssertionError; `(10 mm) + (2 s)` gives DimensionalityError; `1e400 mm` = inf. |

**Must be probed before implementation** (I could not verify these):
1. LibreDWG `dwg2dxf` on Windows: build availability, versions read, exit codes, stdout chatter, speed. Not downloaded; executing downloaded binaries was out of bounds for this probe.
2. The GLB unit and axis converter actually producing metres and +Y up.
3. The seam-on-silhouette rule and tangent-edge suppression on cylinders, cones and fillets beyond the probe part.
4. Exact HLR time at 1k to 10k faces.
5. An OCCT user break via a `Message_ProgressIndicator` subclass in OCP.
6. Face-order stability of booleans with `SetRunParallel`.
7. Tapered extrude (`BRepFeat_MakeDPrism` or `BRepOffsetAPI_DraftAngle`) for molding fixtures.
8. Our ezdxf PDF backend: dashed linetypes as `draw_solid_lines` segments, line weights, page transform.
9. pypdfium2 PNG determinism.
10. pdfplumber on real CAD PDFs with SHX text.
11. uv self-referencing extras (`scan = ["eng-toolkit[cad]", ...]`) with this repo's lock.
12. IGES read quality.
13. AP242 PMI read through `STEPCAFControl_Reader` (GDT mode).
14. cadgen's `requires-python` and CadQuery 2.8's dependency licences (stated from memory or from the skill's command line).
15. `ezdxf.recover` behaviour on damaged files.
16. Thread safety of concurrent OCP calls on independent shapes beyond the translator lock.

## Delivery plan

Each slice is independently verifiable and ends in a check. Each slice also updates the README tool section and adds a `docs/<tool>.md` rationale, as the repository does today.

| # | Slice | Ends with this check |
| --- | --- | --- |
| **1** | **brep extraction (first slice).** `errors.ToolFailure` (UnitsError subclasses it; the runner catches it). `eng_toolkit/brep/` with `_occ` (guard, messenger, lock), `read_solid` (STEP), `triangulate`. `scan.read_model_mesh` uses them and its OCP code is deleted. Extras `cad` and `scan = [eng-toolkit[cad], open3d]`, with `Engine.extra` and `install_hint`. `test_engine_ownership.py`, `test_extras_registry.py`, `test_brep.py`. | The whole `test_metrology_check_scan*.py` suite passes unchanged with `--extra scan` (stdout silence, non-ASCII paths, two-roots counting). `test_brep`: box volume 100000.000 and the two-roots file counted 2/1. The ownership test fails if `import OCP` is added anywhere else (checked by a deliberate local edit). `uv sync` without extras still passes the default suite, and the doctor is ok. If uv rejects the self-referencing extra, list OCP in both extras; the registry test then checks both lists. |
| 2 | `geometry_measure` on STEP/IGES/BREP: mass properties, density, inventory (faces, holes), queries; `JobHandle.input_file`; `Provenance.input_files` and `engine_versions`. | Hand-calc tests in Q6 (98360.531085, 16395.699, mass, distance 20, angle 90 deg, thickness 20, hole H1 thru Ø10). `engine.unavailable` without `cad`. The gateway lists `geometry_measure`. Doctor ok. |
| 3 | `geometry_make_part` v1: `OPS` registry (box, cylinder, extrude, revolve, hole, fillet, chamfer), Resolver, edge selectors, XCAF STEP with fixed header, read-back, `features.json`, optional GLB/STL. | Per-op volume hand calcs, the parameter-change test, identical STEP sha256 on two builds, error-family tests with JSON pointers, the description generated from `OPS` (test: every op appears). |
| 4 | `drawing_make_views` part 1: `brep.project` (exact HLR, dedup, tangent and seam rules, polygonal fallback), `sheet.compose` (layout, overall dims, title block), `dxf.write_dxf`, our PDF backend, `drawing` extra. | Top visible 327.9823, front hidden 40.0000, dims 100/20/50 read back with ezdxf, PDF page 841.89 × 595.28 pt, identical DXF and PDF bytes on two runs, stdout silent. |
| 5 | `drawing_read` (DXF, PDF). | DXF fixture values (DIMLFAC 200/100, Ø10, attribute title block, units warning); hand-written PDF fixture words and tokens; round trip on slice 4's DXF returns the dimensions it wrote. |
| 6 | make_views part 2: hole grouping and callouts, ordinates, `features_path` with hash binding (fit and thread), SVG and PNG. | Probe part callout "Ø10 THRU" with ordinates x 0/25 and y 0/25; with features.json "Ø10 H7 THRU"; a features.json whose sha256 does not match gives a warning and no fit text; a 4-hole plate gives "4× Ø6.6 THRU". |
| 7 | `geometry_check_dfm` (cnc_3axis, injection_molding, additive) with `dfm_rules.json` holding only sourced values. | Q6 fixtures: 4 sharp internal corners fail, R3 passes; draft 0° fails against 1°; min wall 3.000; overhang 800 mm². Checks without limits are `unverified`. |
| 8 | `geometry_convert` (STEP/IGES/BREP/STL to STEP/STL/GLB/BREP; STL reader) and `drawing_convert` (DXF to PDF/SVG/PNG/DXF version). | STL volume 100000 and the cylinder bound 18.85 mm³; GLB max 0.1 m on +Y = 0.02; PDF line 283.465 pt; R12 losses listed. |
| 9 | DWG: `licenses.ExternalTool` + doctor row; `drawing/dwg.py`; DWG input to read and convert. | A fake `dwg2dxf` script tests success, non-zero exit (`engine.failure` with stderr tail), timeout (`engine.timeout`) and absence (`engine.unavailable` naming `ENG_TOOLKIT_DWG2DXF`); a real conversion test is skipped unless the tool is located; the doctor shows the tool as gpl-isolated and informational. |
| 10 | make_part round 2: pattern, mirror, shell, path profiles, `of:` selectors, tapered extrude, `import`. Optionally move check_scan's welding to a numpy weld in brep and drop one Open3D use. | Per-op hand calcs; a pattern of 4 holes gives 4 × the single-hole volume change. |

**Why slice 1 first.** It makes the hard constraint (one owner of STEP reading) true before any second consumer exists. The moment geometry code is written against an unmoved `scan.py`, the shortest path for the next agent is to copy its reader. It changes no behaviour, so the existing 984-line check_scan suite is the verification. It also lands the two structural tests (import ownership, extras registry) that keep every later slice honest. Everything after it depends on `brep`.
