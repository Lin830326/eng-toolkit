# geometry and drawing domains: synthesized design

Produced with pstack's `architect` skill: Phase A grounding, Phase B arena with two runners (opus xhigh and the session model), a cross-judge, and this synthesis. No code has been written. The two candidate designs are kept beside this file as working papers because they hold the measured engine facts the implementation needs:

* [candidate-1-spec-and-brep.md](candidate-1-spec-and-brep.md) (base): declarative part spec, `eng_toolkit/brep/`, in-process OCCT.
* [candidate-2-code-and-workers.md](candidate-2-code-and-workers.md): restricted build123d code, `eng_toolkit/occt/`, one worker process per call.

## Problem

An agent needs to build a part, ask questions about any STEP file, get a dimensioned drawing from a part, read drawings other people made, and convert files. The project's design document names six tools for this (`geometry.make_from_code`, `geometry.measure`, `geometry.check_dfm`, `drawing.read`, `drawing.make_views`, `drawing.convert`). The shape is not obvious because of four constraints measured on this repository and this machine:

1. `metrology/scan.py` already owns STEP reading, so a second reader would split ownership.
2. `make_from_code` is arbitrary code execution inside a tool server, and a Windows Job Object does not stop network access or file writes (measured by both runners).
3. The permissive 2D toolchain is thin: DWG has no permissive reader, ezdxf's own PDF path needs AGPL PyMuPDF, and OCCT gives raw edges, not a dimensioned sheet.
4. The contract is fixed: string/bool/int parameters, unit strings, `ToolResult`, optional extras with `engine.unavailable`, an honest licence registry.

## Usage (caller's view)

```text
geometry_make_part(spec='{"params": {"L": "100 mm", "W": "50 mm", "T": "20 mm", "d": "10 mm"},
                          "features": [{"op": "box", "name": "body", "size": ["$L", "$W", "$T"]},
                                       {"op": "hole", "name": "bore", "at": [["$L / 4", "$W / 2", "$T"]],
                                        "axis": "-z", "diameter": "$d", "depth": "thru", "fit": "H7"},
                                       {"op": "fillet", "name": "corners", "edges": "|z & convex", "radius": "2 mm"}]}',
                   name="bracket", density="2700 kg/m^3")
  -> volume 98360.53 mm^3   (hand: 100*50*20 - pi*5^2*20 - 4*(2^2 - pi*2^2/4)*20)
     artifacts bracket.step, bracket.features.json, spec.json

geometry_measure(model_path=".../bracket.step", queries="distance F1 H1; diameter H1; thickness P(10 mm, 10 mm, 30 mm) -z")
geometry_check_dfm(model_path=".../bracket.step", process="cnc_3axis", direction="+z", limits='{"min_wall": "1 mm"}')
drawing_make_views(model_path=".../bracket.step", views="front,top,right", projection="third_angle", sheet="A4",
                   features_path=".../bracket.features.json", title="BRACKET")
  -> drawing.dxf (source), drawing.pdf, optional svg/png; hidden lines, overall and hole dimensions, ISO title block
drawing_read(path="C:/drawings/flange.dxf")      # DWG only if the user installed LibreDWG's dwg2dxf
drawing_convert(input_path="...dxf", output_format="pdf")
geometry_convert(input_path="...step", output_format="stl", mesh_deflection="0.01 mm")
```

Without an extra, the tools are still listed and answer `engine.unavailable` with the install command.

## Synthesis decision

**Base: candidate 1.** Both candidates and the judge agree on the shell: seven tools, one neutral owner of OpenCASCADE, the DXF as the single source of a drawing, a small PDF backend of our own, fact-first DFM, DWG only through a user-installed GPL program as a separate process. They differ on how model-authored geometry enters, and that decides the base.

### Convergence (ship as is)

* A neutral package owns all OCP use and the STEP reader; `scan.py` is migrated to it first, guarded by its existing tests (slice 1 in both).
* Seven tools: `geometry_make_part`, `geometry_measure`, `geometry_check_dfm`, `geometry_convert`, `drawing_make_views`, `drawing_read`, `drawing_convert`. The draft's `drawing.convert` is split because 3D export is geometry.
* DXF is the single source; PDF/SVG/PNG are derived renders. A ~140 to 300 line PDF backend of our own over ezdxf's frontend, because PyMuPDF is AGPL and matplotlib is heavy and wrong-sized.
* Exact OCCT HLR with the hidden-line coverage test (front view hidden length 320 mm down to 40.0000 mm, equal to the hand value).
* `check_dfm` reports measurements always and verdicts only against limits the caller supplies; the shipped default table stays empty until a limit has a citable source.
* DWG read via LibreDWG `dwg2dxf` as a separate GPL process, never bundled, never imported. DWG writing, drawn GD&T and PMI from vendor files are out of reach.
* Dimensions are `DIMENSION` entities with `DIMLFAC = 1/scale`; the reader reports measurement times DIMLFAC.

### Divergence and how it was settled

| Question | Candidate 1 | Candidate 2 | Decision |
| --- | --- | --- | --- |
| How model-authored geometry enters | declarative JSON spec, no code execution | restricted build123d code in a sandboxed worker, on by default | **Spec (owner decision 1, recommended).** Both runners measured that the Job Object does not block network or file writes and that audit hooks do not stop native OCP writes. The judge scored safety 5 vs 3. A code tool also bypasses the client's per-command permission prompt. A gated override design is documented in candidate 1 (Q4) and is not built. |
| Dependencies | OCP only (`cad`, ~169 MB) plus `drawing` (~70 MB) | adds a 252 MB `geometry-build` extra, a uv override, stubs for `bd_materials` | **Candidate 1.** build123d brings `ocpsvg` and `bd_materials` with no declared licence and a 2 to 4 s import. |
| Where heavy work runs | in the server process, face-count limit | one short-lived worker per call (0.7 to 2.6 s each) | **In process for v1.** Revisit in slice 4 with the determinism finding below. |
| Mesh welding | Open3D, inside `scan.py` | NumPy, in the shared package | **Graft candidate 2's NumPy weld** so geometry tools never need Open3D, but as its own slice with parity tests, not mixed into the pure move. |
| Error carrier | new `ToolFailure`, `UnitsError` becomes a subclass | reuse `UnitsError` | **Candidate 1**, keeping the `ValueError` base so nothing that catches it breaks. |
| Extras bookkeeping | `Engine.extra`, install hints derived from the registry, test that pyproject and the registry agree | hand-typed `INSTALL` dict | **Candidate 1.** |
| Executables | `ExternalTool` registry for dwg2dxf | `Engine.kind="executable"` | **Candidate 1's separate registry**, plus candidate 2's `run_executable` with Job Object memory/time/process limits for dwg2dxf (it parses untrusted binary C). Add a `forbidden` guard row for `pymupdf` (candidate 2) so an `ezdxf[draw]` install is explained by the doctor. |

### Grafts from candidate 2

1. **DXF determinism across processes.** Candidate 2 measured that `ezdxf.options.write_fixed_meta_data_for_testing = True` is not enough: the order of the `CLASSES` section depends on `PYTHONHASHSEED` (two byte streams over 12 seeds; seed 0 repeats). Candidate 1's claim of byte-identical DXF holds only inside one process. Slice 4 must either canonicalize the section order after `saveas` or render in a child with `PYTHONHASHSEED=0`, and its check runs subprocesses with seeds 1 to 12 and compares sha256.
2. **A toolkit-owned `ENG_ISO` dimstyle and metric linetypes** (`HIDDEN_MM`, `CENTER_MM`) in one conventions module read by both the DXF writer and the reader. ezdxf's default dimstyle renders as a dot on a millimetre drawing and the shipped `DASHED2` renders solid (both measured).
3. **PMI round trip** (a measured AP242 semantic dimension of 60 mm via XDE) as a late optional slice for `geometry_measure`.
4. **Fixture discipline**: every `RULES`-style table (here the part-spec `OPS`) generates the tool description and the tests, and an AST test fails the build on stray OCP imports.

### Corrections the cross-judge found (apply in the slices)

* Candidate 1's "same drawing gives same bytes" for DXF is only true in one process (graft 1).
* Candidate 2's "the timestamp is the only STEP difference" came from build123d's exporter. The plain `STEPControl_Writer` carries a per-process counter in the product name. The XCAF writer with a fixed header (candidate 1, measured) is the writer to use.
* Candidate 2's install sizes understate `scan` (about 406 MB by its own numbers) and its memory-bomb claim (6 GB at 2 GB) differs from the saved probe (3 GB at 1 GB).
* Candidate 1's probe used the default dimstyle that renders as a dot (graft 2).

## Shape

```text
eng_toolkit/
  errors.py            ToolFailure (UnitsError subclasses it; run_tool catches it)
  contract.py          Provenance += input_files, engine_versions (optional; owner decision 3)
  runner.py            JobHandle.input_file / reserve / record
  licenses.py          Engine.extra, install_hint(), ExternalTool + EXTERNAL (dwg2dxf), forbidden guard row (pymupdf)
  brep/                the one owner of OCP: read_solid, read_mesh, triangulate, NumPy weld, write_step (XCAF, fixed header),
                       write_mesh (STL, GLB), mass_properties, inventory (faces, holes), distance/angle/wall_thickness/ray_hits,
                       project (exact HLR + cleaning), modelling ops used only by part.py
  domains/geometry/    tools.py (4 thin tools) · part.py (spec language: OPS registry, Resolver, edge selectors, build)
                       · dfm.py (pure rules) · data/dfm_rules.json (sourced limits only; empty at first)
  domains/drawing/     tools.py (3 thin tools) · sheet.py (pure: sheet, scale, view placement, dimension rules, title block)
                       · dxf.py (the one owner of ezdxf; deterministic write; our PDF/SVG render) · pdf.py (pdfplumber/pypdfium2)
                       · dwg.py (dwg2dxf as a separate process via run_executable) · conventions.py (layers, dimstyle, linetypes)
  domains/metrology/scan.py   STEP code moves to brep (slice 1); Open3D stays here until slice 2
tests/  test_engine_ownership.py (AST import rules, private-name rules) · test_extras_registry.py · hand-calculation tests per tool
```

Extras: `cad` = OCP (~169 MB, lgpl-dynamic tier as already declared), `drawing` = ezdxf and pdfminer.six (S5 chose pdfminer.six alone over pdfplumber and pypdfium2: [drawing-read.md](drawing-read.md)), `scan` = `cad` + open3d. `scripts/install.ps1` installs every extra. Call chains stay at three files or fewer.

### Part spec language (v1)

Parameters are unit strings with `$name` references, evaluated by the existing unit gatekeeper (pint's parser, not `eval`). Ops: `box`, `cylinder`, `extrude`, `revolve` (bodies, `mode` add/cut/intersect), `hole` (counterbore, countersink, thread and fit callouts as text), `fillet`, `chamfer`. Edge selectors are terms joined by `&`: `all`, `|x |y |z`, `>x <x ...`, `convex`, `concave`, `line`, `circle`. Round 2 adds patterns, mirror, shell, path profiles, tapered extrude and `import` (start from a STEP so an agent's own body can receive spec holes). Feature intent travels as `<name>.features.json` bound to the STEP by sha256, so drawings can print `Ø10 H7 THRU` without guessing.

## Delivery plan (ordered, each slice ends in a check)

| # | Slice | Ends with this check |
| --- | --- | --- |
| **S1** | **Move STEP reading into `brep` (first slice).** `ToolFailure`; `brep` with `read_solid`, `triangulate`; `scan.read_model_mesh` calls them; `Engine.extra`, `install_hint`; the `cad` extra and `scan = [cad, open3d]`. A pure move, no behaviour change, Open3D weld stays. | The whole `test_metrology_check_scan*.py` suite passes unchanged; `test_brep` box volume 100000 and the two-roots file counted 2/1; the ownership test fails on a deliberate extra `import OCP`; a default `uv sync` still passes the default suite and doctor. |
| S1b | NumPy weld and closed-shell test in `brep`; `scan` stops using Open3D for welding. | Parity with the Open3D weld on box, cylinder at 0.001 mm, two boxes, open shell, and each fixture under 20 random rigid transforms; two boxes touching at one vertex checked. |
| S2 | `geometry_measure` (STEP/IGES/BREP): mass properties, inventory, queries; `JobHandle.input_file`; `Provenance` fields if approved. | volume 98360.531085, area 16395.699, mass 0.265573 kg, distance 20, angle 90 deg, thickness 20, hole H1 thru diameter 10; `engine.unavailable` without `cad`. |
| S3 | `geometry_make_part` v1 (`OPS`, Resolver, selectors, XCAF STEP with fixed header, read-back, `features.json`). **Gated by a small-model eval** (see below). | per-op volume hand calculations, parameter-change test, identical STEP sha256 on two builds, errors carry JSON pointers, description generated from `OPS`. |
| S4 | `drawing_make_views` part 1: exact HLR with cleaning, layout, `dxf.write_dxf`, our PDF backend, the `drawing` extra, `ENG_ISO` dimstyle and metric linetypes. **Probe first and fix DXF cross-process determinism.** | top visible 327.9823, front hidden 40.0000, dimensions 100/20/50 read back with ezdxf, PDF page 841.89 x 595.28 pt, DXF and PDF sha256 equal across `PYTHONHASHSEED` 1 to 12. |
| S5 | `drawing_read` (DXF, PDF). | DIMLFAC 2 reads 200 from a 100 mm measurement; attribute title block; hand-made PDF words; round trip on slice 4's DXF. |
| S6 | `drawing_make_views` part 2: hole grouping and callouts, ordinates, `features_path` with hash binding, SVG and PNG. | `Ø10 THRU` with ordinates; with a matching features.json `Ø10 H7 THRU`; a mismatching hash warns and prints no fit; `4× Ø6.6 THRU`. |
| S7 | `geometry_check_dfm` (cnc_3axis, injection_molding, additive), measurements always, verdicts only against limits. | four sharp internal corners fail against 3 mm, R3 passes; draft 0 deg fails against 1 deg; min wall 3.000; overhang area 800 mm^2. |
| S8 | `geometry_convert` and `drawing_convert`. | STL volume 100000 and the cylinder chord bound 18.85 mm^3; GLB 0.1 m on +Y = 0.02; PDF line 283.465 pt; R12 losses listed. |
| S9 | DWG: `ExternalTool`, `run_executable` with limits, doctor row, `drawing/dwg.py`. | a fake `dwg2dxf` tests success, non-zero exit, timeout and absence; the real binary test is skipped unless located. |
| S10 | `make_part` round 2 and optional PMI read. | per-op hand calculations; AP242 60 mm dimension round trip. |

**S5 as built** ([drawing-read.md](drawing-read.md)): `drawing_read` reads DXF and PDF in a child process with a hard time and memory limit (`eng_toolkit/isolated.py`), through `dxf_read.py` (a second ezdxf owner beside the writer `dxf.py`) and `pdf_read.py` (pdfminer.six). It answers the "file reads over HTTP" risk with the optional `ENG_TOOLKIT_READ_DIRS` allow-list, enforced for every tool in `JobHandle.input_file`. The gateway now lists twelve tools.

Why S1 first: it makes the one-owner rule true while there is a single consumer and its safety net (984 lines of scan tests) already exists. The cross-judge confirmed it as the right first slice, and it kept the weld swap out of it.

## Risks the cross-judge added (neither candidate covered them)

* **The central bet is not evaluated.** Neither runner tried a small local model on the spec language against writing build123d; both fluency claims are assertions. Before S3, run a small eval (a dozen part descriptions, a local small model, success rate of valid specs) and let it confirm or overturn decision 1's trade-off.
* **Unit-string expression cost.** `ureg.parse_expression` on something like `"9**9**9**9 mm"` may compute a huge integer on a server thread. The spec language widens that surface. Probe it and add a length and exponent guard in the gatekeeper before S3.
* **Untrusted STEP parsed in the server process.** An OCCT crash on a malformed file takes the MCP server down. Decide in S2 whether reading runs in a child (candidate 2's worker idea, applied narrowly).
* **No limit on parallel heavy calls.** Define a small semaphore for OCCT and HLR tasks.
* **Long jobs.** Map HLR and DFM runs that can exceed the 30 s target to `toolkit.job_status`.
* **OCCT LGPL release checklist.** Ship the licence text and a source offer (NOTICE file) before distributing a build.
* **File reads over HTTP.** Under `--transport http`, `drawing_read` and `geometry_measure` read any path the user can read. Gate or document it.

## Tradeoffs accepted

* We own a small CAD language and its expressiveness ceiling, in exchange for no code execution, unit strings end to end, an OCP-only licence footprint and declared feature intent. Agents that want build123d run it in their own shell and hand the STEP to every other tool.
* Our own PDF backend, in exchange for no AGPL or matplotlib. Our PDFs have no text layer (text is glyph outlines); the DXF carries the semantics.
* Rule-generated dimensions cover overall size and holes only, in exchange for drawings that are derived from geometry and reproducible. The title block says UNREVIEWED.
* In-process OCCT with a face-count limit and no proven cancellation, in exchange for no per-call process start-up.
* An empty default DFM limit table until each value has a source.

## Owner decisions (recorded 2026-10-18)

The project owner accepted the recommendation on all seven questions below. They are decided, not open: (1) no server-side code execution, declarative spec only; (2) default projection is third angle, first angle selectable; (3) `Provenance` gains optional `input_files` and `engine_versions`, and `check_scan`'s `data.input_files` is removed in the same change; (4) no shipped DFM defaults, caller-supplied limits only; (5) ezdxf stays in the `drawing` extra; (6) LibreDWG is documented as a manual install only; (7) no worker process per call in v1. The text of each question and its recommendation follows.

## The seven questions and their recommendations

1. **Server-side code execution.** Accept the declarative spec and no code tool? (Recommended: yes. If not: the gated design in candidate 1, Q4, and a decision on default on/off and on HTTP.)
2. **Default projection.** First angle (ISO) or third angle (common in Taiwan, Japan and the US)? Candidate 1 chose first, candidate 2 third. (Recommended: third angle as the default, first angle selectable.)
3. **Contract change.** May `Provenance` gain optional `input_files` and `engine_versions`, and should `check_scan`'s `data.input_files` then be removed? (Recommended: yes.)
4. **DFM limit sources.** Which sources are acceptable for defaults? (Recommended: none for now, caller-supplied limits only.)
5. **ezdxf as a core dependency** so `drawing_read` works on DXF in a default install? (Recommended: no, keep it in the `drawing` extra.)
6. **Positioning and LibreDWG.** Personal, company-internal or commercial use decides whether LibreDWG is mentioned at all and how users obtain it. (Recommended: documented manual install only.)
7. **Process-per-call workers.** Not in v1; revisit after slice 4's determinism finding and the crash-isolation decision in slice 2.

## Next implementation step

Slice S1, the pure move of STEP reading from `metrology/scan.py` into `eng_toolkit/brep/`, with the existing check_scan suite as the guard. No owner decision blocks it.
