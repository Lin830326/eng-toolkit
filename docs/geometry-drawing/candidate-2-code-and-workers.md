# geometry and drawing domains: design (candidate 2)

Scope: the `geometry` and `drawing` domains of eng-toolkit, on top of the shipped `calc` and `metrology` domains (main at 873334a).
Everything marked "measured" was run on this machine (Windows 11, uv 0.9.18, Python 3.12.12) in a scratch venv at a short path; commands and results are in "Engine facts I measured". Probe scripts are in `probes/` next to this file. Nothing in the repository was touched.

## Problem

An agent needs to (1) produce a part from parameters, (2) ask questions about any STEP file, (3) get a dimensioned drawing out of a part, (4) read drawings that other people made, and (5) have every step verified by reading the artifact back. The design document names six tools for this. The shape is not obvious because of five constraints that came out of reading the repository and probing the engines:

* **One owner of STEP reading.** `metrology/scan.py` already owns `STEPControl_Reader`, tessellation, welding and the closed-shell test, and its welding uses Open3D (231 MB). Geometry must work without Open3D, and may not grow a second reader. The owner has to move to a neutral place before a second consumer exists.
* **Running LLM-written Python inside a tool server is arbitrary code execution**, and the MCP client's permission prompt shows a "CAD tool", not "run code". The measured protection layers are uneven (Job Object: spawn, memory, kill; Python audit hook: files and network for Python code only; native OCP writes bypass the hook; network is not blocked by the OS at all).
* **`build123d` is a 250 to 340 MB dependency closure with a 2.3 to 4.5 s import** and contains `bd_materials`, which ships no licence in its wheel. It must not be paid by users who only want to measure a STEP file or make a drawing.
* **Output must be reproducible and small.** STEP files carry a timestamp, ezdxf writes objects in an order that depends on `PYTHONHASHSEED` (measured: two different DXF byte streams), and ezdxf's PDF path is PyMuPDF (AGPL, forbidden).
* **The contract is fixed**: `ToolResult` via `run_tool`, string/bool/int parameters, unit strings at the boundary, optional extras with `engine.unavailable`, the licence registry and the doctor.

## Usage (caller's view)

The spec. Types in "Shape" are derived from these calls.

```text
# 1. build a part from code, with parameters that cross the unit gatekeeper
geometry_make_from_code(
    code="""
from build123d import *
with BuildPart() as p:
    Box(P.width, P.depth, P.thickness, align=(Align.MIN, Align.MIN, Align.MIN))
    Box(P.thickness, P.depth, P.height, align=(Align.MIN, Align.MIN, Align.MIN))
    with Locations((P.width / 2, P.depth / 4, P.thickness), (P.width / 2, 3 * P.depth / 4, P.thickness)):
        Hole(radius=P.hole_d / 2)
result = p.part
""",
    params='{"width":"60 mm","depth":"40 mm","height":"30 mm","thickness":"5 mm","hole_d":"8 mm"}',
    name="l_bracket", density="7.85 g/cm3")
  -> value volume 16497.345 mm^3 (hand: 12000 + 5*40*25 - 2*pi*4^2*5)
     values: surface_area, size_x/y/z = 60/40/30 mm, mass 0.1295 kg
     data: solids 1, faces 10, valid true, step_roundtrip {volume_delta: 0.0}, script_sha256, params (normalised to mm),
           sandbox {os_limits [...], python_limits [...], unprotected [...]}, log (print output, 2 kB cap)
     artifacts: l_bracket.step (header normalised), l_bracket.glb, script.py, build.log

# 2. ask questions of any STEP file (ids come from the inventory, stable for the same file bytes)
geometry_measure(model_path=".../l_bracket.step", kind="inventory")
  -> data.holes [{id:"F9", diameter 8 mm, depth 5 mm, axis (0,0,-1), at (30,10,5), through true}, ...]   (ids illustrative: take them from the inventory)
geometry_measure(model_path=".../l_bracket.step", kind="distance", a="F2", b="F7")                     (the two end faces x=0 and x=60 of the inventory)
  -> value distance 60 mm, data.points {a: ..., b: ...}

# 3. manufacturability facts (fact-first: no limits given = measurements only)
geometry_check_dfm(model_path=".../l_bracket.step", process="cnc", axis="y",
                   limits='{"min_internal_radius":"3 mm","max_hole_depth_ratio":"6:1","min_wall":"1.5 mm"}')
  -> values min_internal_radius 0 mm (the inner bend of the L is a sharp concave edge parallel to Y), max_hole_depth_ratio 0.625 (5 / 8), min_wall_thickness 5 mm
     data.findings [{rule:"cnc.internal_radius", measured:"0 mm", limit:"3 mm", limit_source:"caller", faces:["F3","F4"] (ids illustrative), at:[...]}]
     data.not_checked [...]  (tool access, undercuts, threads, stock, fixturing)

# 4. a dimensioned sheet, derived from the model, DXF first and PDF derived from the DXF
drawing_make_views(model_path=".../l_bracket.step", views="front,top,right", scale="1:1",
                   sheet="A3", projection="third", title="L BRACKET", general_tolerance="ISO 2768-m")
  -> values view_count 3, dimension_count 7 (front: width, height; top: depth; callout 2X diameter 8 THRU; hole positions x=30, y=10, y=30), scale 1.0, sheet 420 x 297 mm
     data.dimensions [{kind:"diameter", text:"2X <> THRU", measured:"8 mm", view:"top", source:"hole"}, ...]
     artifacts: drawing.dxf, drawing.pdf, drawing.png (preview for vision models)

# 5. read what somebody else drew
drawing_read(path="C:/drawings/bracket.dwg")           # DWG goes through LibreDWG dwg2dxf, a separate GPL process
  -> data.layers, data.entities {LINE: 120, CIRCLE: 4, DIMENSION: 9, ...}, data.dimensions [{measurement 60, dimlfac 2, displayed 120, text "<>"}],
     data.title_block {method:"heuristic", fields:{...}}, artifact drawing.json (everything, uncapped)
drawing_convert(source_path="C:/drawings/bracket.dxf", target_format="pdf")
  -> artifact bracket.pdf, data.loss {approximated: {SPLINE: 3}, skipped: {ACAD_PROXY_ENTITY: 1}, text: "outlined"}
```

The loop an agent runs is: `make_from_code` (writes STEP and re-reads it) -> `measure` (inventory, distances) -> `check_dfm` -> `make_views` -> `drawing_read` on its own output as a self-check. Each call returns small JSON; files go to the job directory.

## Shape

### 1. Decisions at a glance

| # | Decision | Why (measured fact or principle) |
| --- | --- | --- |
| D1 | Seven tools: `geometry_make_from_code`, `geometry_measure`, `geometry_check_dfm`, `geometry_convert_model`, `drawing_read`, `drawing_make_views`, `drawing_convert` | the draft's six, plus one split: STEP to GLB/STL/3MF conversions are geometry, not drawing |
| D2 | A neutral package `eng_toolkit/occt/` owns STEP reading, topology ids, tessellation, mass properties, hole/corner/wall facts. `scan.py` is migrated to it first | one owner per invariant; Open3D must not be needed to read a STEP |
| D3 | **Every OCCT, build123d, ezdxf or PDF task runs in a short-lived worker process** started by one function, `occt.host.run_task`. The MCP server process imports none of those libraries | crash/hang containment, memory cap, `PYTHONHASHSEED=0` for byte-reproducible DXF, stdout hygiene, one execution model for all tools |
| D4 | `make_from_code` takes **code**, not a declarative spec: a restricted dialect of build123d (AST allow-list, curated builtins and namespace) in the worker, under a Windows Job Object (spawn blocked, memory capped, kill on close) plus a Python audit hook, with the residual risk printed in every result | measured: each layer blocks what it blocks and nothing more (table in section 5) |
| D5 | Four extras: `scan` (unchanged), `geometry` (OCP), `geometry-build` (+ build123d), `drawing` (+ ezdxf, pdfplumber, pypdfium2). `bd-materials` and `threejs-materials` are dropped from the lock with a uv `override-dependencies` marker and stubbed in the worker | measured sizes and licence gaps (section 6) |
| D6 | The DXF is the single source of truth of a drawing. PDF, SVG and PNG are derived from the DXF by one renderer (about 140 lines over ezdxf's `Recorder`, measured) | derive instead of sync; PyMuPDF is AGPL |
| D7 | `check_dfm` is fact-first: measurements always, verdicts only against limits the caller supplies (or the labelled `starter` set). First processes: `cnc`, `molding` | the text-to-cad `dfm` skill itself is "fact-only guidance", not a rule engine (read; section 8) |
| D8 | Reproducibility is a per-artifact contract (section 9): STEP header timestamp normalised, DXF fixed metadata and fixed hash seed, GLB/PDF/STL written by our own deterministic writers, PNG explicitly excluded | measured |
| D9 | PMI: semantic AP242 dimensions are readable with OCCT XDE (measured round trip); drawn GD&T, tolerance intent, DWG writing and PDF to DXF reconstruction are out | section 7 |

### 2. Tools: exact parameters, returns, artifacts

All parameters are `str`, `bool` or `int`. A physical quantity is a unit string checked by `units.parse_quantity` (bare numbers rejected). Lengths come back in `output_unit` (default `mm`). Every result is a `ToolResult` with `review_status="unreviewed"`. The primary `value` is the quantity a caller most likely wants; counts are dimensionless (precedent: `candidate_count`, `points_evaluated`).

```python
# geometry domain (eng_toolkit/domains/geometry/tools.py)
def geometry_make_from_code(
    code: str,                       # build123d script in the restricted dialect; assign the final solid to `result`; parameters arrive as `P`; model unit is mm
    params: str = "",                # JSON object. "40 mm" / "30 deg" / "7.85 g/cm3" strings are unit-checked and converted to mm / deg / kg/m3; integers and booleans pass through; a JSON float is rejected as units.bare_number
    name: str = "part",              # base file name, [A-Za-z0-9_-]{1,40}
    density: str = "",               # e.g. "7.85 g/cm3"; "" = no mass
    formats: str = "step,glb",       # comma list from step, glb, stl
    mesh_deflection: str = "0.05 mm",# for glb/stl only
    output_unit: str = "mm",
    timeout_seconds: int = 60,       # 5..300
) -> ToolResult: ...
#  value: volume. values: volume, surface_area, size_x, size_y, size_z, (mass), (center_of_mass_x/y/z)
#  data: solids, shells, faces, edges, valid, step_roundtrip, script_sha256, params, sandbox, engines, log
#  artifacts: <name>.step, <name>.glb, <name>.stl (as requested), script.py, build.log

def geometry_measure(
    model_path: str,                 # .step or .stp
    kind: str = "summary",           # summary | inventory | distance | diameter | angle | area | length | pmi (last slice)
    a: str = "", b: str = "",        # entity ids from inventory ("F12", "E7", "V3") or a point "10 mm, 20 mm, 0 mm"
    output_unit: str = "mm",
) -> ToolResult: ...
#  summary:   values volume (absent for a shell), surface_area, size_x/y/z, center_of_mass_*; data counts, closed_shell, solids, declared_unit, input_files
#  inventory: data.holes, data.cylinders, data.planes (first 200 each, rest in topology.json), faces/edges ids with kind, area, axis, radius
#  distance:  value distance; data.points {a, b} (closest points)       angle: values normal_angle (0..180 deg), plane_angle (0..90 deg)
#  diameter / area / length: value of the one entity

def geometry_check_dfm(
    model_path: str,
    process: str = "cnc",            # cnc | molding   (enum grows; unsupported values are input.unsupported_value)
    limits: str = "",                # "" = measurements only | "starter" = built-in labelled starter limits | JSON {"min_internal_radius":"3 mm","max_hole_depth_ratio":"6:1","min_wall":"1.5 mm","min_draft":"1 deg"}
    axis: str = "z",                 # tool axis (cnc) or pull direction (molding): x | y | z
    output_unit: str = "mm",
) -> ToolResult: ...
#  value: finding_count (dimensionless). values: min_internal_radius, max_hole_depth_ratio, min_wall_thickness (cnc); min_draft_angle (molding)
#  data: findings [{rule, severity: "violation"|"measured", measured, limit, limit_source: "caller"|"starter"|null, faces, at}], not_checked [...], measurements (full tables, capped; rest in dfm.json)

def geometry_convert_model(
    model_path: str,                 # .step or .stp
    target_format: str = "glb",      # glb | stl | 3mf (3mf in a later slice)
    mesh_deflection: str = "0.05 mm",
    name: str = "",                  # default: input stem
) -> ToolResult: ...
#  value: triangle_count. data: closed_shell, solids, deflection_mm, input_files. artifact: <name>.<ext>

# drawing domain (eng_toolkit/domains/drawing/tools.py)
def drawing_read(
    path: str,                       # .dxf | .dwg | .pdf   (.dwg needs the external dwg2dxf)
    page: int = 0,                   # pdf only: 0 = all pages (max 20), n = that page
    detail: str = "summary",         # summary | full  (full: more rows in `data`; the complete model is always in drawing.json)
) -> ToolResult: ...
#  value: entity_count. data (dxf): version, units, extents, layers, entities (histogram), blocks, dimensions, texts, title_block, recovered
#        data (pdf): pages [{size_mm, chars, words (head), lines, rects, curves, images, tables (head), text_layer: bool}], scanned_hint
#  artifacts: drawing.json (+ converted .dxf when the source was .dwg)

def drawing_make_views(
    model_path: str,                 # .step or .stp
    views: str = "front,top,right",  # comma list from front, back, top, bottom, left, right, iso
    scale: str = "auto",             # "auto" or an ISO 5455 ratio "1:2", "2:1", "1:1"...
    sheet: str = "A3",               # A4 | A3 | A2 | A1 | A0 (ISO 5457 landscape)
    projection: str = "third",       # third | first
    dimensions: bool = True,
    hidden_lines: bool = True,
    title: str = "",
    notes: str = "",                 # ';'-separated lines
    general_tolerance: str = "",     # free text printed as given ("ISO 2768-m"); "" prints "TOLERANCES: NOT SPECIFIED". The tool never invents tolerances
    formats: str = "dxf,pdf,png",    # dxf is always written; pdf, svg, png are derived from it
    timeout_seconds: int = 120,
) -> ToolResult: ...
#  value: view_count. values: view_count, dimension_count, scale (ratio as a number, dimensionless), sheet_width, sheet_height
#  data: views [{name, origin_mm, extent_mm, visible_edges, hidden_edges, approximated_curves}], dimensions [{id, kind, view, measured, text, source}], dimensions_skipped, layout, title_block, scale_ratio
#  artifacts: drawing.dxf, drawing.pdf, drawing.svg, drawing.png

def drawing_convert(
    source_path: str,                # .dxf | .dwg | .pdf (pdf only to png)
    target_format: str,              # dxf | pdf | svg | png
    page: str = "auto",              # "auto" (extents + 10 mm margin) or a sheet name A4..A0
    timeout_seconds: int = 120,
) -> ToolResult: ...
#  value: output_bytes. data.loss {entities_total, rendered, approximated {type: n}, skipped {type: n}, text: "outlined"|"substituted font", notes [...]}
#  unsupported (error drawing.unsupported_conversion with the reason): pdf -> dxf, raster -> vector, dxf -> dwg, anything -> dwg
```

Why seven and not six or twenty: `check_dfm` and `measure` stay separate because their callers differ (a verdict against a limit versus a fact), `convert_model` exists because the draft hid 3D conversions in `drawing.convert` (OCCT, lib3mf), where nobody would look for them; IC layouts (gdstk) belong to a future domain, not here.

### 3. Module map

```text
eng_toolkit/
  occt/                         NEW. Neutral engine adapter. The only importer of OCP outside worker-side task modules.
    __init__.py                 EngineUnavailable, require_ocp(extra), InputError            (replaces scan's _ocp / ScanEngineUnavailable / ScanInputError)
    step.py                     read_step -> StepModel, write_step, normalize_step_header     (the ONE STEPControl_Reader / Writer)
    topology.py                 index(StepModel) -> Topology with stable ids F1.., E1.., V1..
    features.py                 holes, internal corners, wall pairs, draft angles: pure over Topology (+ BRepExtrema for walls)
    mesh.py                     ModelMesh, tessellate, NumPy weld, closed-shell test           (the ONE BRepMesh_IncrementalMesh)
    props.py                    mass_props: volume, area, centroid, exact bbox
    host.py                     run_task, run_executable, Limits: server side, imports no engine
    worker.py                   child entry point: handshake, stdout hygiene, dispatch dotted task path
    sandbox_win.py / sandbox_posix.py   Job Object / rlimits behind one `confine(process, limits)` function
  domains/
    geometry/
      __init__.py               DOMAIN
      tools.py                  FastMCP server: 4 thin tools (parse strings, call host.run_task, shape ToolResult)
      dialect.py                PURE: RULES table, validate(source) -> Script. No engine imports; runs in the host (fast reject) and in the worker
      tasks.py                  worker-side: build, measure, dfm, convert
      meshio.py                 GLB, STL, 3MF writers (NumPy + struct + zipfile), deterministic
      data/dfm_starter.json     starter limits, each with a source column (empty until a citable source exists)
    drawing/
      __init__.py               DOMAIN
      tools.py                  FastMCP server: 3 thin tools
      conventions.py            layer names, dimstyle, linetypes, sheet sizes, title block geometry: written by sheet.py, read by reader.py
      views.py                  HLR projection, edge cleaning -> ViewGeometry                  (worker side)
      dimension.py              PURE planner: plan_dimensions(Topology, features, ViewGeometry) -> Dimension list
      sheet.py                  layout + DXF assembly (ezdxf)
      render.py                 DXF -> PDF (PdfRender), SVG (ezdxf SVGBackend), PNG (pypdfium2)
      reader.py                 DXF / PDF -> DrawingSummary
      dwg.py                    dwg2dxf adapter over host.run_executable
      tasks.py                  worker-side: make_views, read, convert
```

Dependency rules, enforced by `tests/test_architecture.py` (AST scan, fails the build, per the red-flag "importable internals"):
1. `tools.py`, `dialect.py`, `dimension.py` (pure) and everything under `occt/host.py`, `occt/sandbox_*.py` never import `OCP`, `build123d`, `ezdxf`, `pdfplumber`, `pypdfium2`.
2. `STEPControl_Reader` appears in `occt/step.py` only; `BRepMesh_IncrementalMesh` in `occt/mesh.py` only; `import OCP` only under `occt/` and in `domains/*/tasks.py` and `domains/drawing/views.py`.
3. A task path string passed to `run_task` must resolve to a function in a `domains.*.tasks` module (the test collects every string literal passed to `run_task` and imports it).
4. Every `family` string a tool can raise appears in that domain's `ERROR_FAMILIES` constant (the test greps raises and fails on a stray one).

Call chain for any tool: `tools.py` -> `host.run_task` -> worker -> `tasks.py` -> `occt/*` or `drawing/*`. Four hops, three files for a reader to open (tools, tasks, the pure module).

### 4. Core data types (sketch; bodies not implemented)

```python
# ---------------------------------------------------------------- eng_toolkit/occt/__init__.py
INSTALL: dict[str, str] = {extra: f"uv sync --extra {extra}" for extra in ("scan", "geometry", "geometry-build", "drawing")}

class EngineUnavailable(RuntimeError):
    """OCP (or build123d, ezdxf...) cannot be imported. Message names the module, the dist, the install command,
    and on Windows the venv path length (OCP failed to load from a very deep path, measured earlier)."""
    def __init__(self, module: str, dist: str, extra: str, cause: BaseException): ...

class InputError(ValueError):
    """A file or value the caller can fix. ``family`` is a contract error family."""
    def __init__(self, family: str, message: str, details: dict[str, Any] | None = None): ...

def require_ocp(extra: str = "geometry") -> None:
    """Import OCP, remove OCCT's stdout printer (it would corrupt the MCP stdio channel). Raises EngineUnavailable."""
    raise NotImplementedError

# ---------------------------------------------------------------- occt/step.py   (the one STEP owner)
@dataclass(frozen=True)
class StepSource:
    path: Path
    sha256: str
    size_bytes: int
    declared_unit: str           # parsed from the file text ("mm", "inch", ...); geometry is always millimetres after read
    roots_in_file: int
    roots_transferred: int       # roots that produced faces, not merely counted (scan.py's existing, tested rule)

@dataclass(frozen=True)
class StepModel:
    source: StepSource
    shape: Any                   # TopoDS_Shape. Never crosses the worker boundary.
    solids: int
    shells: int

def read_step(path: Path, *, param: str = "model_path") -> StepModel:
    """Reader status, TransferRoots, roots accounting, null/empty checks: the logic now in scan.read_model_mesh."""
    raise NotImplementedError

def write_step(shape: Any, path: Path) -> None:
    """STEPControl_Writer AP214 (default). Then normalize_step_header, so equal shapes give equal bytes."""
    raise NotImplementedError

def normalize_step_header(text: str) -> str:
    """Replace the FILE_NAME timestamp with 1970-01-01T00:00:00. Measured: it is the only difference between two builds."""
    raise NotImplementedError

# ---------------------------------------------------------------- occt/topology.py   (the one id scheme)
FaceKind = Literal["plane", "cylinder", "cone", "sphere", "torus", "bspline", "other"]
EdgeKind = Literal["line", "circle", "ellipse", "bspline", "other"]
Vec = tuple[float, float, float]

@dataclass(frozen=True)
class Face:
    id: str                      # "F1".. in IndexedMap_TopoDS_Shape order of the shape as read (equal for equal file bytes, measured)
    kind: FaceKind
    area_mm2: float
    centroid_mm: Vec
    bbox_mm: tuple[float, float, float, float, float, float]
    normal: Vec | None           # outward unit normal, planes only
    axis: Vec | None             # cylinder, cone, torus
    axis_point_mm: Vec | None
    radius_mm: float | None
    material_inside: bool | None # cylinder/sphere: True = boss (material inside the surface), False = hole
    edge_ids: tuple[str, ...]

@dataclass(frozen=True)
class Edge:
    id: str
    kind: EdgeKind
    length_mm: float
    start_mm: Vec
    end_mm: Vec
    radius_mm: float | None
    face_ids: tuple[str, ...]
    convex: bool | None          # dihedral: True convex, False concave, None for seam or free edges

@dataclass(frozen=True)
class Topology:
    faces: tuple[Face, ...]
    edges: tuple[Edge, ...]
    def face(self, ref: str) -> Face: ...      # raises InputError("geometry.ref_not_found", ...)
    def edge(self, ref: str) -> Edge: ...
    def shape_of(self, ref: str) -> Any: ...   # OCP object, valid only in the building process

def index(model: StepModel) -> Topology: raise NotImplementedError

# ---------------------------------------------------------------- occt/features.py   (pure over Topology)
@dataclass(frozen=True)
class Hole:
    face_id: str
    diameter_mm: float
    depth_mm: float
    axis: Vec
    position_mm: Vec             # axis point at mid depth
    through: bool | None         # None = could not decide

@dataclass(frozen=True)
class InternalCorner:
    edge_ids: tuple[str, ...]
    radius_mm: float             # 0.0 = sharp (two planes, concave edge)

@dataclass(frozen=True)
class WallPair:
    face_ids: tuple[str, str]
    thickness_mm: float
    overlap_area_mm2: float

@dataclass(frozen=True)
class FaceDraft:
    face_id: str
    draft_deg: float             # angle between the face and the pull axis; 0 = parallel (no draft)

def holes(topo: Topology) -> tuple[Hole, ...]: raise NotImplementedError
def internal_corners(topo: Topology, axis: Vec) -> tuple[InternalCorner, ...]: raise NotImplementedError
def wall_pairs(topo: Topology, max_thickness_mm: float) -> tuple[WallPair, ...]: raise NotImplementedError
def draft_angles(topo: Topology, pull_axis: Vec) -> tuple[FaceDraft, ...]: raise NotImplementedError

# ---------------------------------------------------------------- occt/mesh.py   (the one tessellator)
@dataclass(frozen=True)
class ModelMesh:                 # fields unchanged from scan.py so scan's 68 tests keep their meaning
    vertices: np.ndarray         # (n, 3) float64, mm
    triangles: np.ndarray        # (m, 3) int32
    watertight: bool
    deflection_mm: float
    solids: int = 1
    roots_in_file: int = 1
    roots_transferred: int = 1
    @property
    def signed(self) -> bool: return self.watertight and self.solids <= 1

def tessellate(model: StepModel, deflection_mm: float) -> ModelMesh:
    """BRepMesh_IncrementalMesh(parallel=False), face triangles, weld(), is_closed_oriented()."""
    raise NotImplementedError
def weld(vertices: np.ndarray, triangles: np.ndarray, eps_mm: float = 1e-6) -> tuple[np.ndarray, np.ndarray]:
    """Rounded-key lexsort weld, degenerate triangles dropped. Retries on a half-cell shifted grid when the first pass is not closed (a node pair straddling a cell boundary)."""
    raise NotImplementedError
def is_closed_oriented(triangles: np.ndarray) -> bool:
    """Every directed edge once and its reverse once. Measured correct on box, two boxes, cylinder, sphere, open shell."""
    raise NotImplementedError

# ---------------------------------------------------------------- occt/props.py
@dataclass(frozen=True)
class MassProps:
    volume_mm3: float | None     # None when the shape is not a closed solid
    area_mm2: float
    centroid_mm: Vec
    bbox_min_mm: Vec
    bbox_max_mm: Vec             # BRepBndLib.AddOptimal_s, not Add_s (the loose box carries a gap)
def mass_props(shape: Any) -> MassProps: raise NotImplementedError

# ---------------------------------------------------------------- occt/host.py   (server process; imports no engine)
@dataclass(frozen=True)
class Limits:
    timeout_s: int = 60
    memory_mb: int = 2048        # measured peak of a build123d run: 262 MB; a 1 GB limit broke OpenBLAS at import with the default thread count, so the limit is 2 GB and the child sets OPENBLAS_NUM_THREADS=1 (1 GB with one thread was not re-tested)
    max_output_mb: int = 64      # checked after exit; a disk quota would need NTFS quotas, so a script can still write faster than this check

Trust = Literal["trusted", "script"]   # "script": the payload carries LLM-written code -> dialect re-check + audit hook in the child

def run_task(task: str, payload: dict[str, Any], *, job: JobHandle, limits: Limits = Limits(), trust: Trust = "trusted") -> dict[str, Any]:
    """Spawn a worker for the dotted task path, hand it payload, return its JSON result.
    Raises UnitsError(family, ...) (the repository's existing structured error carrier) for task errors,
    engine.timeout, engine.crash (exit status, stderr tail), engine.unavailable (install hint), code.resource."""
    raise NotImplementedError

def run_executable(argv: list[str], *, job: JobHandle, limits: Limits, cwd: Path) -> "ExecResult":
    """Same confinement for an external GPL executable (dwg2dxf). No handshake needed; stdin closed."""
    raise NotImplementedError

# ---------------------------------------------------------------- domains/geometry/dialect.py   (PURE)
@dataclass(frozen=True)
class Rule:
    id: str                      # "R01"
    summary: str                 # shown in the tool description and docs: derived, never retyped
    hint: str                    # shown in the error: what to write instead

RULES: dict[str, Rule] = {...}   # R01 imports, R02 dunder/underscore attribute, R03 denied names (getattr, open, eval, type...), R04 str.format,
                                 # R05 IO names (export_*, import_*, Mesher, show*), R06 classes (no bases but script classes, only __init__ dunder),
                                 # R07 .wrapped and frame attributes, R08 size and depth limits, R09 no `result`, R10 result type

class DialectViolation(ValueError):
    rule: str; line: int; col: int; snippet: str; hint: str

@dataclass(frozen=True)
class Script:
    source: str
    sha256: str
    tree: ast.Module

def validate(source: str) -> Script: raise NotImplementedError   # raises DialectViolation

# ---------------------------------------------------------------- domains/drawing (types the planner and sheet share)
ViewName = Literal["front", "back", "top", "bottom", "left", "right", "iso"]
VIEW_AXES: dict[ViewName, tuple[Vec, Vec]] = {   # (normal toward the viewer, screen-right); gp_Ax2's THIRD argument is the X direction (measured pitfall)
    "front": ((0, -1, 0), (1, 0, 0)), "back": ((0, 1, 0), (-1, 0, 0)), "top": ((0, 0, 1), (1, 0, 0)),
    "bottom": ((0, 0, -1), (1, 0, 0)), "right": ((1, 0, 0), (0, 1, 0)), "left": ((-1, 0, 0), (0, -1, 0)),
    "iso": ((0.577, -0.577, 0.577), (0.707, 0.707, 0)),
}

@dataclass(frozen=True)
class Prim:                      # one projected edge, view coordinates, mm, before scaling
    kind: Literal["line", "circle", "arc", "poly"]
    visible: bool
    data: tuple                  # line: (x0,y0,x1,y1); circle: (cx,cy,r); arc: (cx,cy,r,a0,a1) ccw degrees; poly: ((x,y),...)

@dataclass(frozen=True)
class ViewGeometry:
    name: ViewName
    prims: tuple[Prim, ...]      # after cleaning (section 7)
    outline_bbox_mm: tuple[float, float, float, float]   # visible geometry only
    approximated: int            # curves flattened to polylines (tolerance 0.01 mm)

@dataclass(frozen=True)
class Dimension:
    kind: Literal["linear_h", "linear_v", "diameter", "radius"]
    view: ViewName
    p1: tuple[float, float]; p2: tuple[float, float] | None    # view mm
    text: str                    # "<>" = measured; or an override such as "2X <> THRU"
    source: Literal["overall", "hole", "position", "radius"]
    value_mm: float

def plan_dimensions(topo: Topology, holes_: tuple[Hole, ...], views: tuple[ViewGeometry, ...], *, cap: int = 40) -> tuple[tuple[Dimension, ...], tuple[str, ...]]:
    """PURE. Returns (dimensions, skipped reasons). Rules in section 7."""
    raise NotImplementedError

@dataclass(frozen=True)
class DrawingSummary:            # what drawing_read returns, for DXF and PDF alike (JSON-able, no engine types)
    format: Literal["dxf", "pdf"]
    ...
```

Invariants in types: `StepModel.shape` is `Any` and never serialised, so a tool cannot return it; `Topology` ids are the only way to refer to an entity across tools; `Limits` is frozen; `Script` can only be built by `validate`; `DialectViolation.rule` is a key of `RULES` (test).

### 5. `geometry_make_from_code`: how the code runs, and what is not protected

Decision: **code, not a declarative spec**, executed as follows.

```text
tools.make_from_code                       host (MCP server process; imports no engine)
  1 parse params (unit gatekeeper) -> P
  2 dialect.validate(code)                  ms; forbidden code never costs a 2.6 s worker
  3 host.run_task("...geometry.tasks:build", payload, trust="script")
        spawn  [sys._base_executable, -I, -X utf8, bootstrap]     base interpreter, not the venv launcher (see below)
        env    SystemRoot, PYTHONHASHSEED=0, HOME/USERPROFILE/APPDATA/LOCALAPPDATA/TEMP/TMP = <job>/.sandbox, OPENBLAS_NUM_THREADS=1, PYTHONUTF8=1
               (nothing else: API keys and proxy variables of the server are not inherited)
        cwd    <job dir>
        child  imports build123d (with bd_materials / threejs_materials stubbed), builds the namespace, then BLOCKS on stdin
        host   assigns the child to a Job Object, THEN writes the request line (user code cannot start before the limits exist)
        child  re-validates the script, installs the audit hook, exec()s with P, takes `result`, checks it (BRepCheck, volume > 0),
               write_step -> read_step -> mass_props (round-trip), meshio, returns one JSON line
        host   timeout: TerminateJobObject. exit without result: engine.crash. output over quota: code.resource
  4 shape ToolResult
```

**The restricted dialect** (`dialect.py`, one `RULES` table feeds validator, error hints, tool description and tests): allowed imports `build123d` and `math` only, and both resolve to proxy namespaces that carry the allowed names, so `bd.os` is not a path to `os`; the namespace is `build123d.__all__` minus 16 IO names (measured: `Export2D ExportDXF ExportSVG Mesher export_* import_*`, `Part.export_step` no longer exists in 0.13, IO is module-level and therefore enumerable); builtins are a curated list (no `open getattr setattr eval exec compile vars globals dir type object super id hash __import__ input breakpoint`); no attribute that starts with `_` or `gi_ cr_ ag_ f_ tb_ co_ func_`, no `.format` / `.format_map` (format strings can walk attributes), no `.wrapped` (it hands out raw OCP); classes allowed but with no bases except script classes and the only dunder `__init__`; size, depth and node-count caps. A forbidden construct is `code.forbidden` with the rule id, line, column and the hint ("the tool writes STEP and GLB itself: delete `export_step(...)` and `show(...)`, and assign the shape to `result`"). Measured on five typical LLM-style scripts: two passed (a flange with a helper function, loop and f-string; a parameterised plate with lambda and try/except), one failed inside OCCT (`Standard_NoSuchObject` from a fillet after an extrude: a script error, reported as `code.runtime`), one on `export_step` (a bare `NameError`, now a hint), one on a class definition (now allowed).

**Parameters** reach the script as `P`, a read-only namespace in mm and degrees. This is the only place a unit gatekeeper can act on code: literals inside the script are bare numbers in mm by the documented convention of build123d, and the result says so (`data.model_unit: "mm"`).

**What each layer does, measured** (Windows 11; `probes/sbx_parent.py`, cases T1 to T15):

| Layer | Blocks | Does not block (measured) |
| --- | --- | --- |
| Dialect (AST allow-list, curated builtins and namespace) | `import os`, dunder walks (`().__class__.__bases__[0].__subclasses__()`), `getattr`, `str.format` attribute walks, classes with dunders, IO names: T2 to T5 return `code.forbidden` after 0 ms of validation in the worker (the host-side pre-check skips the 2.7 s import altogether) | anything that escapes the denylist idea; denylists have a history of bypasses |
| Job Object `ActiveProcessLimit=1` | `subprocess.run(['cmd','/c','echo pwned'])` with no Python hook at all: `WinError 1816` (T12) | network (T13: the socket reached the network stack), file access |
| Job Object `ProcessMemoryLimit` | a 6 GB list allocation: `MemoryError` at the 2 GB limit (T7) | disk space |
| `KILL_ON_JOB_CLOSE` + host timeout | `while True: pass` killed after exactly the 6 s given (T6); the process tree dies with the job | |
| Python audit hook (`socket.*`, `subprocess.Popen`, `os.system`, `open` for write outside the job dir, read outside Python/site/job) | T9, T10, T11 for Python-level code | **native code**: `StlAPI_Writer.Write(shape, r"C:\Users\...\leak.stl")` succeeded with the hook installed (T15); OCP's own file readers and writers never pass through Python's `open` |
| Scrubbed environment, HOME inside the job dir | secrets in env vars, caches written to the user profile (build123d needs a HOME: without one it raised `Could not determine home directory`) | |

**Honestly not protectable** (printed machine-readably in `data.sandbox.unprotected`, and in the tool description):
1. A deliberate author who finds a dialect escape runs with the server user's rights. After an escape the OS still blocks process creation and caps memory, nothing else.
2. Network exfiltration: no OS-level control on Windows without AppContainer or a firewall rule; only the Python-level hook.
3. Native file reads and writes through OCP objects, if any name that reaches them slips past the IO list.
4. Disk filling: the output quota is checked after exit.
5. Anything on a POSIX host: `resource.setrlimit` and a process group kill are best effort (no per-process spawn block); Windows is the verified path.
For untrusted callers the answer is outside this tool: run the whole server in Windows Sandbox or a container, or set `ENG_TOOLKIT_CODE_EXEC=off` (the tool then returns `code.disabled`). The default is on, because the toolkit's users are individual engineers running a local stdio server (open question 1).

**Why the worker starts the base interpreter, not `sys.executable`** (measured): in a uv venv `Scripts\python.exe` is a trampoline that spawns the real interpreter as a child. The first probe run put the trampoline in a job with `ActiveProcessLimit=1` and every case died in 10 ms with exit 101 and "Unable to create process using '...\python.exe" -I ...sbx_worker.py'". The host therefore runs `sys._base_executable` with `-I` and passes the importable directories (`sys.path` entries that are directories) in the bootstrap command line, since `-I` ignores `PYTHONPATH`.

**Why not a declarative spec.** A JSON feature tree would be safe by construction and would put every length through the unit gatekeeper, and `P` partly does that already. It loses on three measured or structural points: the selector language for fillets and chamfers is a CadQuery-selector-sized project; LLMs write build123d better than an invented tree; and a second authoring path doubles the surface ("two ways to do one task"). If a spec is added later it must compile to a dialect script, never to a second kernel path.

### 6. Where each engine runs and what it costs

| Extra | Installs | Licence tier (declared in `licenses.KNOWN`) | Installed size (measured) |
| --- | --- | --- | --- |
| `scan` (exists) | open3d, cadquery-ocp-novtk | permissive, lgpl-dynamic (OCCT in the wheel) | about 340 MB |
| `geometry` | cadquery-ocp-novtk (numpy is already core) | `cadquery-ocp-novtk`: Apache-2.0 wrapper declared `lgpl-dynamic`, bundled LGPL-2.1 + OCCT exception, unmodified | +175 MB (OCP 175.2 MB on disk, 108 MB wheel) |
| `geometry-build` | `geometry` + `build123d~=0.13.0` and its closure (scipy 108 MB, scikit-learn 27, sympy 27, IPython, requests, lib3mf, ezdxf, pillow, ocpsvg, ocp-gordon ...) | build123d Apache-2.0 permissive; closure all permissive or MPL (certifi); two metadata gaps below | +342 MB as resolved; **+252 MB** with `bd-materials` and `threejs-materials` (90.1 MB) excluded |
| `drawing` | `geometry` + `ezdxf>=1.4.5,<1.5`, `pdfplumber>=0.11`, `pypdfium2>=5` | ezdxf MIT; pdfplumber MIT; pdfminer.six MIT; pypdfium2 BSD-3 + Apache-2.0 (bundles PDFium); cryptography Apache-2.0 or BSD-3; Pillow MIT-CMU | +72 MB (ezdxf 15.3, fonttools 12.1, Pillow 14.9, pdf stack 28.1, rest 1.2) |
| external `dwg2dxf` (not an extra) | LibreDWG 0.14 `libredwg-0.14-win64.zip`, 12.1 MB, from the project's GitHub release | GPL-3.0, `gpl-isolated`, separate process only, never bundled | 12 MB, user-installed |

Install lines: `uv sync --extra geometry`, `--extra drawing`, `--extra geometry-build`. `scripts/install.ps1` is changed to install all four; the README states per extra which tools need it. A tool is always registered; a missing engine gives `engine.unavailable` with the extra to install, exactly as `check_scan` does.

`pyproject.toml` additions:

```toml
[project.optional-dependencies]
geometry = ["cadquery-ocp-novtk>=8.0.1.1.0"]
geometry-build = ["eng-toolkit[geometry]", "build123d~=0.13.0"]
drawing = ["eng-toolkit[geometry]", "ezdxf>=1.4.5,<1.5", "pdfplumber>=0.11", "pypdfium2>=5"]

[tool.uv]
# build123d imports bd_materials (no licence in its wheel) and threejs-materials (90 MB) for material finishes we never use.
# The marker drops both from the lock and from `uv sync`; the worker stubs them (see occt/worker.py).
override-dependencies = ["bd-materials ; sys_platform == 'never'", "threejs-materials ; sys_platform == 'never'"]
```

Measured: `uv lock` records both with that marker and `uv export` lists them as `; sys_platform == 'never'`; `import build123d` with the two modules stubbed in `sys.modules` works in 2.28 s (4.1 s with them), `Box - Cylinder` gives the exact volume 874.336 and `export_step` and `export_stl` work. The stub is pinned to `build123d~=0.13.0` and guarded by a test that imports build123d in a worker; a build123d bump that needs more of `bd_materials` fails that test, not a user.

`licenses.py` changes (the registry is the one list; the doctor derives from it):

```python
@dataclass(frozen=True)
class Engine:
    ...                                   # existing fields
    kind: Literal["python", "executable"] = "python"
    executable: str = ""                  # "dwg2dxf"; found via ENG_TOOLKIT_DWG2DXF or PATH
    metadata_gap: str = ""                # "wheel carries no licence; upstream repo Apache-2.0 (verified 2026-10-18)": explains an UNKNOWN detected tier
                                          # for a permissive declaration, and nothing else (mirror of bundled_license)
```

New `KNOWN` rows: `build123d` (Apache-2.0, permissive, optional, domains geometry); `ezdxf` (MIT), `pdfplumber` (MIT), `pypdfium2` (BSD-3-Clause/Apache-2.0), all permissive and optional; `bd-materials` and `ocpsvg` with `metadata_gap` (only matter when someone installs them without the override); `libredwg` (`kind="executable"`, GPL-3.0, `gpl-isolated`, optional); and a guard row `pymupdf` as `forbidden` so an `ezdxf[draw]` install is explained in the doctor, which already hard-fails on AGPL metadata. `violates_policy` applies to Python distributions installed in the environment only: a GPL *executable* run as a separate process is exactly the policy. The doctor reports executable engines via `shutil.which` and `--version` run under `run_executable`.

### 7. Drawings: how they are produced, read and converted

**Produced** (`drawing_make_views`, all in one worker; measured pipeline in `probes/p6_pipeline.py`):

1. `read_step`, `index`, `features.holes` (the same facts `geometry_measure` and `check_dfm` use).
2. HLR per view with OCCT's exact `HLRBRep_Algo` (1 to 5 ms for the bracket and a filleted box) using `VIEW_AXES`. Take `VCompound`, `OutLineVCompound` (visible), `HCompound`, `OutLineHCompound` (hidden). Seam edges are not emitted (a cylinder rotated 0, 30, 90 degrees about Z shows no mid-face seam line), but silhouettes live in the outline compounds, so those are required (a cylinder at 0 degrees: the left silhouette is in the outline compound and the right one, which coincides with the seam, in the sharp compound; at 30 and 90 degrees both come from the outline compound).
3. Edge conversion by `BRepAdaptor_Curve`: line to LINE; circle to CIRCLE or ARC (the arc's direction comes from the circle axis sign: a circle seen from behind runs clockwise); everything else, including the edge-on circles that HLR returns as degenerate BSplines, is sampled at 0.01 mm deflection and replaced by a LINE when all samples are within 0.01 mm of the chord, else an LWPOLYLINE (counted in `approximated_curves`).
4. **Cleaning rules, each measured to be needed**: drop exact duplicates by a direction-agnostic quantised key (bracket front view: 32 raw edges, 24 kept; top 22, 20; right 30, 30); drop a hidden segment that is collinear with and covered by a visible one (an edge-on circle's far half lies exactly under the visible near half: cylinder front view returns hidden top and bottom lines under the visible ones); merge collinear touching segments of equal visibility (a cylinder turned 30 degrees: the seam vertex splits a hidden line at x = 12.99). No hidden-smooth edges are drawn.
5. Layout (`sheet.py`): ISO 5457 sheet sizes, margins and a title block of fixed geometry; third-angle puts top above front and right to the right of front, first-angle mirrors them; `scale="auto"` takes the largest ISO 5455 ratio whose views plus their dimension rows (8 mm per row, outward) fit without overlap; the DXF is drawn **in sheet millimetres** at that scale and every dimension carries `DIMLFAC = 1/scale`, so a 30 mm drawn length at 1:2 reads "60" (measured: `get_measurement()` returns the raw 30.0 and `dimlfac` 2.0 separately, and the rendered text is 60).
6. **Auto dimensions** (`dimension.py`, pure, capped at 40, the rest listed in `dimensions_skipped`): (a) overall width and height per view, each size dimensioned once across views (ISO 129 "dimension once"); (b) diameter callouts for holes whose axis is parallel to the view normal, grouped by diameter with a text override such as `2X <> THRU` (through or blind from `features.holes`; blind adds the depth); (c) hole positions as baseline dimensions from the view's left and bottom visible edges, equal coordinates merged; (d) radius dimensions for visible fillet arcs. Rows stack outward from the view. Everything else stays undimensioned and the result says so.
7. DXF assembly with a toolkit-owned `ENG_ISO` dimstyle (`dimtxt 3.5, dimasz 3, dimexo 1, dimexe 1.5, dimgap 1, dimtad 1, dimdec 2`; ezdxf's default `EZDXF` style is 0.25 units high and rendered as a dot on a mm drawing, measured) and metric linetypes `HIDDEN_MM [5,3,-2]` and `CENTER_MM [28,20,-3,2,-3]` (the shipped `DASHED2` is inch-scaled and rendered solid at mm scale, measured). Centre marks on circles and centre lines on side-on cylinders come from `features.holes`. Layers `OUTLINE HIDDEN CENTER DIM TEXT FRAME TITLE`.
8. `ezdxf.options.write_fixed_meta_data_for_testing = True`, written under `PYTHONHASHSEED=0` (the worker's environment).
9. `render.py`: replay ezdxf's `Recorder` into `PdfRender`, a `BackendInterface` of about 140 lines (measured prototype: 95 ms for the 3-view sheet, 21.9 KB, identical bytes across hash seeds 0, 1, 2 and two unseeded runs, `pdfplumber` reads it as 841.89 x 595.28 pt = A4 landscape, 1307 lines, 79 curves). Text arrives from ezdxf as glyph outlines, so **our PDFs have no text layer** (pdfplumber: `chars 0`); stated in the result and in `drawing_read` warnings, and the checks read the DXF. SVG: ezdxf's own `SVGBackend` (76 ms, no extra dependency). PNG: pypdfium2 renders the PDF (81 ms at 2x).

**Read** (`drawing_read`):
* DXF: `ezdxf.readfile`, falling back to `ezdxf.recover.readfile` (`data.recovered = true` plus the auditor's messages). Reported: version, `$INSUNITS` as a unit name, extents, layers with entity counts, entity histogram, block names, every DIMENSION as `{type, measurement, dimlfac, displayed (measurement x dimlfac when the text is "<>"), text, layer}`, TEXT/MTEXT, and a title block found by the toolkit's own `TITLE` layer first, then heuristics (INSERT of a block named like title/border, text cluster in the lower-right of the extents), labelled `method: "heuristic"` with no confidence number it cannot justify. Everything is in `drawing.json`; `data` holds capped heads with `*_total` counts (precedent: `regions_total`).
* PDF: `pdfplumber`: page size in mm, chars, words (head), lines, rects, curves, images, `find_tables` on the lower-right region, and `scanned_hint = (chars == 0 and images > 0)`. A PDF with no text layer and vector paths only gets the "outlined text or plotted drawing" warning. No OCR.
* DWG: `dwg2dxf` (LibreDWG, GPL) through `run_executable` (memory, time and process limits apply: it parses untrusted binary C), then the DXF path; the DXF is kept as an artifact.

**Converted** (`drawing_convert`): DXF to PDF, SVG, PNG through `render.py`; DWG to DXF; PDF to PNG through pypdfium2. The loss report counts entities by type and says what was approximated (SPLINE flattened), skipped (3D solids, proxy entities, images) and substituted (fonts). Refused with a reason: PDF to DXF (the design document itself marks it unstable), raster to vector, anything to DWG (LibreDWG's writer is not reliable and ODA is proprietary).

**Honestly out of reach with permissive tools**:
* DWG writing, and DWG reading without LibreDWG (a GPL tool the user installs; not bundled, not auto-downloaded).
* Drawn GD&T and tolerance intent. A B-Rep does not contain tolerance intent, so a feature control frame generated from geometry would be fabrication. The sheet prints only what the caller gives (`general_tolerance`), and the title block carries "UNREVIEWED".
* PMI: OCCT's XDE can write and read AP242 *semantic* dimensions (measured: a programmatically authored linear distance of 60 mm round-trips as `Location_LinearDistance`, value 60.0, schema `AP242_MANAGED_MODEL_BASED_3D_ENGINEERING_MIM_LF`, via `STEPCAFControl_Writer.SetDimTolMode` and `Reader.SetGDTMode`). That is a later `geometry_measure(kind="pmi")` slice. Not claimed: reading PMI from vendor files (no fixture exists without a commercial CAD), graphical presentation PMI, or checking tolerances against the geometry.
* Section, detail and exploded views, thread symbols, surface texture symbols, BOM tables, ISO 129-complete dimensioning (function and datum intent), multi-sheet assemblies, drawing OCR.
* JT and Parasolid: no open reader; ask for STEP.

### 8. `geometry_check_dfm`: fact-first

The shipped reference points the same way: the text-to-cad `dfm` skill states "a guided review skill, not an automatic feature-recognition or manufacturing certification engine", and its `mold_tool.py` "never emits pass/fail". Limits belong to a shop and a material. So:

* `limits=""`: measurements only; `findings` have `severity: "measured"` and no limit.
* `limits='{...}'`: each key is validated against the process's schema, every length through the unit gatekeeper, ratios as `"6:1"`; violations carry `limit_source: "caller"`.
* `limits="starter"`: values from `data/dfm_starter.json`, each with a `source` field; **rows without a citable source are not shipped**, so the set may be small or empty at first; findings carry `limit_source: "starter"` and a standing warning that these are not any shop's capability.

`cnc` (tool axis `axis`) measures: internal vertical corners (concave edges parallel to the axis: radius of the filling cylinder, or 0 for two planes), hole depth-to-diameter ratio per hole with through/blind, thickness of opposite parallel planar wall pairs (planes with opposite outward normals whose true distance, `BRepExtrema_DistShapeShape`, equals the plane distance, i.e. their projections overlap). `molding` measures per-face draft against the pull axis from B-Rep normals (exact, no mesh). `data.not_checked` always lists what is not evaluated: tool and holder access, undercuts and parting line, threads, stock, fixturing, setups. Sheet metal and FDM are later enum values, not placeholders.

### 9. Failures, determinism, hashes, and the hand-calculation test of each tool

**Error families** (extending the existing `units.*`, `input.*`, `engine.*`; a domain's `ERROR_FAMILIES` constant lists them and a test fails on a stray one):

| Family | When |
| --- | --- |
| `units.*`, `input.invalid`, `input.file_not_found`, `input.unsupported_format`, `input.unsupported_value` | existing meaning; STEP/DXF/PDF that cannot be read is `input.invalid` with the path (as scan) |
| `engine.unavailable` | missing extra or engine; details: module, dist, install command |
| `engine.timeout`, `engine.crash` | worker killed at the limit; worker exited without a result (details: exit status, stderr tail) |
| `engine.failure` | uncaught exception in the host (existing) |
| `code.forbidden` | dialect rule: details rule, line, col, snippet, hint |
| `code.syntax`, `code.runtime`, `code.no_result`, `code.invalid_result`, `code.timeout`, `code.resource`, `code.disabled` | script errors (runtime: the traceback is reduced to script lines), missing or non-solid `result`, memory or output quota exceeded, switch off |
| `geometry.ref_not_found`, `geometry.not_solid`, `geometry.unsupported_query` | `a`/`b` ids not in the model; volume asked of a shell; kind needs entities of another type |
| `drawing.projection_failed`, `drawing.no_geometry`, `drawing.unsupported_conversion`, `drawing.converter_missing` | HLR produced nothing; refused conversion with reason; dwg2dxf not found (details: where it looked and how to install) |

**Reproducibility per artifact** (measured unless marked):

| Artifact | How made | Result |
| --- | --- | --- |
| STEP | OCCT writer, header timestamp rewritten to 1970-01-01 | two builds differ only on line 4 (`FILE_NAME` timestamp); normalised sha256 equal; face order and ids identical (same face-signature hash) |
| DXF | `write_fixed_meta_data_for_testing` **and** `PYTHONHASHSEED=0` | with random seeds: two distinct byte streams (objects section order, `LAYOUT` vs `ACDBPLACEHOLDER`); seed 0 three times identical; 12 other seeds give both variants |
| PDF | own writer: no timestamps, no producer string | identical across runs and seeds |
| GLB | own writer, sorted JSON keys, 4-byte aligned chunks | identical; read back by pygltflib (independent reader) |
| STL | own binary writer, fixed 80-byte header | by construction (tested in slice 9) |
| PNG | pypdfium2 render | **excluded** from the claim: identical only for the same pdfium build; marked `reproducible: false` in the artifact `data` |

Every result carries `data.reproducibility = {inputs_hash, engines {occt, build123d, ezdxf, numpy, python}, hash_seed: 0, input_files [{path, sha256, bytes}], artifact_sha256 {...}}`; `provenance.inputs_hash` is the existing canonical hash of the tool arguments (for `make_from_code` that includes the code and params, so the script is in the hash). `script.py` is stored verbatim in the job dir and its sha256 is in `data`.

**Hand-calculation regression test per tool** (all fixtures built with OCP primitives or plain ezdxf in the test, as scan's tests do; no commercial CAD):

| Tool | Test and hand calculation |
| --- | --- |
| `make_from_code` | bracket above: V = 60*40*5 + 5*40*25 - 2*pi*4^2*5 = 16497.345 mm^3 (measured in the worker: 16497.345, bbox 60 x 40 x 30, peak 261 MB); mass = 16497.345 mm^3 x 7.85e-6 kg/mm^3 = 0.12950 kg; flange `Cylinder(40,8) - Cylinder(15,8) - 6 x Cylinder(4,8)`: V = 8*pi*(1600 - 225 - 96) = 32144.776 (measured 32144.776); param script: 60 x 40 x 5 plate, two 8 mm holes, 2 mm fillets: 11497.345 - 4 x (1 - pi/4) x 2^2 x 5 = 11480.18 (measured 11480.177). Every `RULES` id has a failing and a passing example; `while True` is killed at the limit; the memory bomb is `MemoryError`/`code.resource`; two runs have equal normalised STEP sha |
| `measure` | box 100 x 50 x 20: volume 100000, area 2 x (5000 + 2000 + 1000) = 16000, centroid (50, 25, 10); cylinder r 15, h 40: volume pi x 225 x 40 = 28274.33, diameter 30; `distance F(x=0) F(x=100)` = 100; `angle` of two adjacent faces: normal angle 90; the `summary` of a `make_from_code` artifact equals the build's returned values (the tools agree because `props.py` is one function) |
| `check_dfm` | block 40 x 30 x 20 with a 2 mm filleted vertical pocket corner and `min_internal_radius="3 mm"`: measured 2 < 3, one finding on the named faces; plate 20 mm thick with a through hole of diameter 8: depth/diameter ratio 20/8 = 2.5 against `6:1`: no finding; two parallel faces 1.5 mm apart: wall 1.5; molding: a face tilted 5 degrees from the pull axis reads draft 5 degrees, a vertical box wall 0 |
| `make_views` | box 100 x 50 x 20 with a 10 mm through hole along Y: front outline extent 100 x 20, top 100 x 50, right 50 x 20 (in sheet mm at 1:1); front view: a visible circle of diameter 10 and no hidden line; top and right views: two hidden lines each at the hole's +/-5 mm; dimensions read back with plain ezdxf: 100, 20, 50, diameter 10 with text `<> THRU`; at scale 1:2 drawn extents are 50 x 10 and the dimension text still reads 100 (DIMLFAC 2); A4 landscape PDF page is 297/25.4 x 72 = 841.89 pt wide; three runs under different `PYTHONHASHSEED` values in the host give equal DXF and PDF sha |
| `drawing_read` | a DXF built in the test with 3 LINE, 1 CIRCLE, a linear dimension of 30 drawn length with DIMLFAC 2 (displayed 60), two layers: counts exact; a hand-made PDF with Helvetica "TITLE" text: `chars == 5`; a truncated DXF goes through recover and reports `recovered: true`; our own PDF reports `chars == 0` and the outlined-text warning |
| `drawing_convert` | DXF with a SPLINE and a proxy entity to PDF: loss `approximated {SPLINE: 1}`, `skipped {ACAD_PROXY_ENTITY: 1}`; PDF page size as above; DWG: a Python shim named `dwg2dxf` proves the process boundary and error mapping (timeout, non-zero exit, empty output); a real-binary smoke test is skipped unless `ENG_TOOLKIT_DWG2DXF` and a user-supplied sample exist |
| `convert_model` | box to STL: 12 triangles, 84 + 12 x 50 = 684 bytes; GLB bytes parse with pygltflib: 8 vertices, 36 indices (measured) |

### 10. Interface depth and red-flag screen

* `host.run_task` hides process choice, environment scrub, Job Object, handshake, timeout, stdout hygiene, crash classification and output quota behind one function and one frozen `Limits`. Seven tools share it.
* `occt.step.read_step`, `topology.index`, `mesh.tessellate`, `props.mass_props` are one-call deep modules; tools never see a `TopoDS_*`.
* `Topology` ids are the only entity reference, defined once, read by `measure`, `check_dfm`, `make_views` (hole facts) and tests.
* Shallow module? The seven `tools.py` functions are deliberately thin (parse, call, shape) as in metrology; the depth is in `occt/` and the pure modules.
* Information leakage: layer names, dimstyle and title-block geometry live in `conventions.py`, imported by the writer and the reader; the dialect's rules live in `RULES`, which feeds the validator, the hints, the tool description and the tests.
* Temporal decomposition: there is no load/validate/transform/save chain; STEP read, units and root accounting are one function.
* Pass-through: `tools.py -> run_task` adds unit parsing, dialect pre-check and result shaping; `convert_model` and `make_from_code(formats=...)` share `meshio` and do not duplicate it.
* Split ownership: STEP read (`step.py`), tessellation (`mesh.py`), mass properties (`props.py`), holes (`features.py`) each have one owner; scan is migrated to them, with its tests as the guard.
* Two ways to do one task: one authoring path (code); one drawing source (DXF) with derived renderings; DWG to DXF is one adapter used by both `read` and `convert`.
* Importable internals and hand-synced lists: `tests/test_architecture.py` (rules above) and `ERROR_FAMILIES`.

## Synthesis decision

(filled in by arena)

## Tradeoffs accepted

* We accept 0.7 to 2.6 s of worker start-up on every geometry or drawing call (measured: 0.73 s OCP + numpy, 1.12 s with ezdxf, about 2.6 s with build123d stubbed) in exchange for crash and hang containment, memory limits, a fixed hash seed and one execution model. A warm worker pool is possible later; it would add state this design does not need yet.
* We accept a denylist-style dialect for code execution, which can never be a proof, in exchange for an OS-enforced floor (no process creation, memory cap, kill) and a result that prints what remains unprotected. A spec would be safe but unusable by an LLM; unrestricted `exec` is what the other MCP servers ship.
* We accept stubbing `bd_materials` and `threejs_materials` and a uv override that drops them from the lock, in exchange for 90 MB, 1.8 s of import, and not carrying an unlicensed package. The cost is a pin to `build123d~=0.13.0` and a guard test.
* We accept that our PDFs have no text layer (glyph outlines), in exchange for a 140-line renderer and no AGPL or matplotlib dependency. The DXF is the readable truth.
* We accept hole classification, dimension planning and wall detection as rules over B-Rep facts, not a recogniser: they dimension what a maker needs for prismatic parts and say what they skipped.
* We accept that `check_dfm` ships few or no default limits at first, in exchange for never printing a number without a source.
* We accept that scan keeps running in-process on the new shared reader for now (open question 2), though every new tool runs in a worker: the migration is a refactor with a guard, not a hosting change.
* We accept CPU-bound HLR with no fallback in the first release: a model whose HLR exceeds the timeout returns `engine.timeout`. `HLRBRep_PolyAlgo` exists in the build (import verified, not run) as the later fallback.
* What a reader may take for an oversight: `UnitsError` is reused as the carrier for non-unit errors raised in `compute` (as `check_scan` already does); the host re-validates the dialect that the worker validates again; `numpy` weld uses a grid, not Open3D's radius merge.

## Alternatives considered

1. **Declarative parametric spec (JSON feature tree) instead of code.** Interface: safe by construction, unit strings everywhere, trivially cacheable, no worker needed for isolation. It exposes a new operation vocabulary and selector language to every caller and hides little behind it, and it needs a second modelling path next to the code the design document and every LLM already use. Lost on depth and fluency; allowed back later only as a compiler to dialect scripts.
2. **In-process `exec` with restricted builtins (no worker).** Simplest. Exposes the server to native crashes, hangs, memory growth and to OCCT writing to stdout (the MCP channel), and cannot give a fixed hash seed (DXF bytes vary, measured). Lost.
3. **Wrap text-to-cad / `cadgen`.** The design document suggests it as a starting point and records its default telemetry. Measured by reading the installed 0.7.21: `engineering-drawing` is a `cadgen.eng_drawing` script API run through `uvx --python 3.13` with matplotlib, the output is PDF only (no DXF), `dfm` is guidance with no rule engine except `mold_tool.py` (trimesh, rtree, scipy, networkx, lxml, mesh-based). It conflicts with the Windows clean-install, Python 3.12, no-telemetry and licence-audit constraints, and would be a second geometry stack. Lost; its guidance (fact-only DFM, "dimension what a maker needs") is adopted.
4. **OCP-only modelling facade instead of build123d.** Saves about 252 MB and 1.9 s of start-up. A facade partly compatible with build123d is the worst case for an LLM (it writes the idiom, half of it fails), and a full one is a month of work. Lost, but cheap to revisit because only `tasks.build` and the dialect namespace would change.
5. **CadQuery instead of build123d.** `uv pip install --dry-run cadquery` resolves `cadquery-ocp 7.9.3.1.1` (the vtk variant), vtk 9.6.2, casadi, numba, matplotlib and nlopt, so it resolves to a different OCCT (7.9, not the 8.0 of the wheel scan already uses) and cannot share one `OCP`. Lost.
6. **PDF through ezdxf's PyMuPDF backend or matplotlib.** PyMuPDF is AGPL: forbidden. matplotlib adds about 40 MB and still outlines text. Lost to the 140-line renderer.
7. **FreeCAD TechDraw for views.** LGPL, GUI-bound, the design document already marks it as a fallback. Lost; OCCT's HLR is the same algorithm family and runs headless in 1 to 5 ms.
8. **One fat tool `geometry_run(op=...)`.** Minimal tool count, but a union of parameters that differ per op: callers must learn the implementation. Lost to seven purposeful tools.
9. **A verdict engine with shipped limits.** Looks more useful, is wrong for any shop that differs, and prints unsourced numbers. Lost to fact-first.
10. **Per-tool in-process OCP for the cheap tools (measure).** Saves 0.7 s per call, creates two execution models, and puts STEP parsing of untrusted files in the server process. Lost.

## Open questions and risks

Questions for the human:
1. Should `geometry_make_from_code` be on by default? The design says yes (local stdio server, individual engineer) with `ENG_TOOLKIT_CODE_EXEC=off`. Would you rather default it to off for HTTP transport or when bound to a non-loopback host?
2. Should `metrology_check_scan` also move into the worker, so there is one execution model for every OCCT call? The design migrates its STEP code to `occt/` but leaves hosting as is.
3. Is a 252 MB `geometry-build` extra acceptable, given the alternative (OCP-only facade) costs maintenance instead of megabytes?
4. Do you want a warm worker pool (about 0.7 to 2.6 s saved per call) once real usage shows the start-up matters?
5. How should LibreDWG reach users: documented manual install only, or an opt-in `install.ps1 -WithDwg` that downloads the signed 12 MB zip? Bundling it is not proposed (GPL source offer).
6. `ws/` grows without bound and drawings, GLBs and PNGs make it larger. Is a documented cleanup command in scope?
7. Which public sources may back the `starter` DFM limits, so that no number is shipped without a citation?
8. The `UnitsError` carrier carries non-unit families already. Rename it to something neutral in this work, or leave it?

Risks, with where each is caught:
* The dialect's false-positive rate on real LLM scripts is known from five scripts only. Mitigation: hints in every error, `classes` allowed, a corpus test added as users report failures.
* build123d minor versions can import more of the stubbed packages: pin and guard test.
* HLR on large or curved models is unmeasured (only a bracket, a cylinder, a sphere and a filleted box were run): timeouts return `engine.timeout`; the fallback is `HLRBRep_PolyAlgo`.
* Grid welding can split a node pair that straddles a cell boundary (expected rate about 2e-6 per shared node pair at 1e-6 mm cells, so about 0.2 per 100,000 shared nodes): the retry on a shifted grid and a test that applies 20 random rigid transforms to every fixture.
* Face ids are stable for the same file bytes and the same OCCT version only; every result records the file hash and `occt` version.
* Job Object and nested jobs: `AssignProcessToJobObject` succeeded here under the Claude Code shell; other MCP clients that start the server inside a restrictive job are untested.

**Not verified; probe before the slice that needs it**
* Slice 1: Open3D parity of `weld` and the closed-shell test on vertex-manifold edge cases (two boxes touching at one vertex), and `scan`'s 68 tests against the NumPy weld. Also `declared_unit` parsing from real STEP headers (inch, metre): `STEPControl_Reader.FileUnits()` raised `TypeError` through the bindings, and an inch-written file read back in mm correctly.
* Slice 2: worker behaviour on Linux and macOS (rlimits, process group kill); Job Object `ProcessMemoryLimit` versus `JobMemoryLimit` for forked workers; working-set peak of `ezdxf` + `pdfplumber` tasks.
* Slice 3: `build123d` API drift against real LLM output (`Part.export_step` is gone in 0.13: older code fails); dialect corpus; `Compound`/assembly results; STEP written by `write_step` from `result.wrapped` versus `export_step` (same header?).
* Slices 5 to 7: `pdfplumber` on real-world drawing PDFs and its table detection on title blocks (not run: it was installed and used only on our own text-free PDF); ezdxf text rendering on a machine with no system fonts (this machine rendered with ezdxf's bundled fallback); `recover` behaviour on real damaged files; ISO 5457 frame and title block dimensions against the standard text (not read here); dimension row stacking on dense parts.
* Slice 8: LibreDWG was not downloaded or run (no binary was fetched; only release metadata was read: 0.14, `libredwg-0.14-win64.zip`, 12,084,779 bytes). Version 0.14 is newer than the 0.13.4 in the design document. Behaviour on R2018+ files, its stderr format for the loss report, and whether the binary runs under `ActiveProcessLimit=1` are unverified.
* Slice 9: `cnc` rules (concave-edge convexity test, wall pair overlap) and `molding` draft on real filleted and drafted parts; no fixture exists except the ones the tests construct.
* Slice 10: PMI beyond the OCCT-authored fixture; `RWGltf_CafWriter` was imported but not run (the hand-written GLB writer makes it unnecessary); 3MF writer against a slicer.

## Next implementation step

Create `eng_toolkit/occt/` and move `read_model_mesh`, `ModelMesh`, `_face_triangles`, `_count` and the OCCT message silencing out of `metrology/scan.py` into `occt/step.py` and `occt/mesh.py` with a NumPy weld, keeping all 68 scan tests green, before any geometry tool is written.

## Engine facts I measured

Environment: Windows 11, uv 0.9.18, CPython 3.12.12, scratch venv `C:\Users\USER\ep-arena-2` (short path), deleted at the end. Probe sources: `probes/`. Model: the 3-hole L bracket of `probes/bracket_b123d.py` (the Usage bracket plus a 6 mm cross hole through the upright, V 16355.974), unless stated.

**Install closure and licences**

| Command | Result |
| --- | --- |
| `uv pip install --dry-run build123d` | 57 packages: build123d 0.13.0, cadquery-ocp-novtk 8.0.1.1.0, numpy 2.5.4, scipy 1.18.1, scikit-learn 1.9.1, sympy 1.14.0, ipython 9.17.1, requests 2.34.2, lib3mf 2.5.0, ezdxf 1.4.5, pillow 13.0.0, ocpsvg 0.7.0, ocp-gordon, bd-materials 0.2.4, threejs-materials 1.2.5 ... |
| `uv pip install --dry-run cadquery` | cadquery 2.8.0 + cadquery-ocp 7.9.3.1.1 (vtk variant) + vtk 9.6.2 + casadi 3.8.1 + numba 0.68.0 + matplotlib 3.11.2 + nlopt 2.11.0 + trame ...: not usable with the novtk wheel |
| install build123d with `--no-deps` and loop on the first `ModuleNotFoundError` | `import build123d` needs bd_materials, scipy, ocp_gordon, ezdxf, svgpathtools, svgwrite, ocpsvg, svgelements, trianglesolver, lib3mf, requests, threejs_materials, PIL, pygltflib, dataclasses_json, platformdirs, sklearn (`brep_from_stl`), IPython (`shape_core`): the closure cannot be trimmed by hand |
| sizes (sum of dist files) after `uv pip install build123d` | cadquery-ocp-novtk 175.2 MB, scipy 108.0, threejs-materials 90.1, numpy 42.0, scikit-learn 27.3, sympy 26.9, ezdxf 15.3, pillow 14.9, jedi 14.6, fonttools 12.1; `site-packages` 559 MB (212 MB with OCP + numpy + build123d `--no-deps`) |
| licence metadata | ocpsvg 0.7.0: empty metadata but `licenses/LICENSE` is the Apache-2.0 text; **bd_materials 0.2.4: no licence in metadata or in the wheel**; `gh api repos/bernhard-42/bd_materials` -> Apache-2.0, `gumyr/build123d` -> Apache-2.0, `bernhard-42/ocpsvg` -> Apache-2.0; threejs-materials Apache-2.0; certifi MPL-2.0; lib3mf, pygltflib, ezdxf metadata via classifier (BSD, MIT, MIT) |
| `uv lock` in a scratch project with `override-dependencies = ["bd-materials ; sys_platform == 'never'", "threejs-materials ; sys_platform == 'never'"]` | both stay in `uv.lock` with that marker; `uv export` prints `bd-materials==0.2.4 ; sys_platform == 'never'`: not installed by `uv sync` |
| `pdfplumber` install | + pdfminer.six 20260107 (MIT, 8.6 MB), pypdfium2 5.14.0 (BSD-3/Apache-2.0, 8.0 MB), cryptography 50.0.2 (10.5 MB), cffi, pycparser: about 28 MB beyond Pillow |
| ezdxf 1.4.5 `Requires-Dist` | pyparsing, typing_extensions, numpy, fonttools. Its `draw` extra pulls PyMuPDF (AGPL) and matplotlib; the PDF backend is `PyMuPdfBackend` |

**Import and start-up times**

| What | Time |
| --- | --- |
| `import OCP` | 0.43 s |
| `import build123d` | 4.1 to 4.5 s, 2401 modules; with `bd_materials` and `threejs_materials` stubbed 2.28 s |
| worker start + import, base interpreter `-I`: OCP + numpy / + ezdxf / + pdfplumber | 0.73 s / 1.12 s / 1.25 s (min of 3) |
| first build123d worker of the session (cold file cache) | 8.6 s to the handshake; warm 2.7 s |

**OCP 8.0.1.1 binding facts** (differences from CadQuery-era documentation, which scan.py partly knows)
* Present: `HLRBRep_Algo HLRBRep_HLRToShape HLRBRep_PolyAlgo HLRAlgo_Projector STEPCAFControl_Reader/Writer XCAFDoc_DimTolTool XCAFDimTolObjects_* RWGltf_CafWriter StlAPI_Writer BRepExtrema_DistShapeShape BRepCheck_Analyzer IntCurvesFace_ShapeIntersector BRepClass3d_SolidClassifier` (import test of 22 names, none missing).
* OCCT 8 naming: `TopTools_IndexedMapOfShape` is `from OCP.collections import IndexedMap_TopoDS_Shape_TopTools_ShapeMapHasher`; `TDF_LabelSequence` is `Sequence_TDF_Label`; `TColStd_HArray1OfReal` is `HArray1_double`. `Bnd_Box.Get()` cannot be called (returns an unregistered type): use `CornerMin()/CornerMax()` with `BRepBndLib.AddOptimal_s`. `gp_Ax2(P, N, Vx)`: the third argument is the X direction, not "up" (my first HLR run was rotated 90 degrees by this). `Interface_Static.SetCVal_s("write.step.schema", "AP242DIS")` only takes effect after `STEPControl_Controller.Init_s()`, and `STEPCAFControl_Writer.SetDimTolMode(True)` / `Reader.SetGDTMode(True)` are the PMI switches (there is no `SetGDTMode` on the writer). `STEPControl_Reader.FileUnits()` raised `TypeError`.
* Units: `write.step.unit` MM, INCH, M files all read back as 1 x 2 x 3 mm for a box made as 1 x 2 x 3: reading normalises to mm.

**STEP reading and reproducibility** (`probes/p2_read.py`, `bracket_b123d.py`)
* The same build123d script run twice in separate processes (5.0 s each): `diff a.step b.step` shows one line, `FILE_NAME('Open CASCADE Shape Model','2026-10-18T18:17:18',...` vs `...:23`. After replacing the timestamp the sha256 is equal (`99d0b9dedd69cf0a...`). Both files give 11 faces with the same (index, surface type, area) signature hash (`bae71a4ab85e5b2b`), volume 16355.974 for that variant, read in 6 to 9 ms. build123d 0.13: `Part` has no `export_step` method; `export_step(part, path)` is module-level.
* Hole classification from the B-Rep (`probes/p5_inventory.py`): F6 cylinder r 3, axis (1,0,0), at (0,20,20), HOLE; F10 and F11 r 4, axis (0,0,-1), at (30,10) and (30,30), HOLE, all by the sign of the outward normal against the radial direction.

**Shared mesh reader without Open3D** (`probes/p8_mesh.py`): box 8 vertices / 12 triangles closed True in 2.5 ms; two boxes closed True, solids 2; cylinder 244 / 484 closed in 17 ms; sphere 5969 / 11934 closed in 86 ms; box missing one face closed False; cylinder at deflection 0.0005: 2176 triangles closed in 18 ms.

**Hidden-line projection** (`probes/p3_hlr.py`, `p13_hlr_cyl.py`, `p14_seam.py`): bracket front/top/right/iso projections take 1.8 / 1.4 / 1.5 / 1.0 ms; filleted box 4.9 ms. Output types: lines, circles, BSplines (edge-on circles), ellipses in the iso view (11, because circles are split at visibility changes). Raw edges vs after dedupe: front 32 -> 24, top 22 -> 20, right 30 -> 30; hidden segments lie exactly under visible ones for edge-on circles; the hidden top line of a cylinder is split at the seam vertex; no mid-face seam line at 30 or 90 degrees rotation; outline compounds hold the silhouettes (cylinder at 0 degrees: only the left one).

**DXF, dimensions, PDF** (`probes/p4_dxf.py`, `p6_pipeline.py`, `p7_dimstyle.py`, `pdfback.py`)
* Dimension reading: `Dimension.get_measurement()` returns 40.0 and 8.0 for the two test dimensions; with `dimlfac=2` a 30 mm drawn length reports measurement 30.0, override lfac 2.0, text `<>`, and renders as "60".
* ezdxf's default dimstyle on a mm drawing renders the text as a dot (height 0.25 units); with an `ISO_MM` style `dimtxt=3.5, dimasz=3` the PNG shows "60", "Phi8" and arrowheads. Shipped linetypes (`DASHED2`...) are inch-scaled and render solid; `doc.linetypes.add("HIDDEN_MM", pattern=[5.0, 3.0, -2.0])` renders dashes.
* Determinism of DXF: `write_fixed_meta_data_for_testing=True` alone is not enough (seeds 1, 2, 3, 5, 6, 8, 9, 11, 12 gave sha `9fe5e71d2423`, seeds 4, 7, 10 and 5 of 6 unset runs gave `59c13eb8a3a8`; seed 0 gave `9fe5e71d2423` three times). The diff is the order of `LAYOUT` and `ACDBPLACEHOLDER` in the OBJECTS section.
* ezdxf's frontend turns text into glyph paths (`pipeline.py draw_text`: "Render text as filled paths"). Our PDF: `pdfplumber` page 841.89 x 595.276 pt, 1307 lines, 79 curves, 7 rects, `chars 0`; pypdfium2 renders 1684 x 1191 px in 81 ms. 3-view bracket sheet: HLR + DXF 33 to 40 ms, DXF to PDF 95 ms, PDF 21,966 bytes, same sha on every run. SVG backend: 10.3 KB in 76 ms.

**Sandbox probes** (`probes/sbx_parent.py`, `sbx_worker.py`; base interpreter, Job Object via ctypes, env scrubbed, handshake before user code)

| Case | Result |
| --- | --- |
| T1 bracket in the dialect | ok, V 16497.345 (hand 16497.345), bbox 60 x 40 x 30, peak 261 MB, run 131 ms after a 2.7 s import |
| T2 `import os` / T3 `().__class__.__bases__[0].__subclasses__()` / T4 `'{0.__class__}'.format(1)` / T5 `getattr` | `code.forbidden`, rule and line reported, 0 ms |
| T6 `while True: pass`, timeout 6 s | killed at 6.0 s by `TerminateJobObject` |
| T7 6 GB list, job limit 2 GB | `MemoryError` (an earlier run at a 1 GB limit failed inside OpenBLAS at import with the default thread count; the final run used 2 GB and `OPENBLAS_NUM_THREADS=1`) |
| T8 / T9 / T10 / T11 (no dialect, audit hook only) | `subprocess.Popen`, `os.system`, write outside the job dir, `socket.getaddrinfo` denied by the hook |
| T12 spawn a process with no hook, Job Object only | `OSError WinError 1816`: the OS blocks it |
| T13 network connect with no hook | reached the network stack (`TimeoutError` from the connect): Job Object does not block network |
| T14 / T15 native `StlAPI_Writer.Write` to a path outside the job dir, without / with the audit hook | file written both times |
| first run with `sys.executable` | all 15 cases exit 101 in 10 ms, stderr "Unable to create process using '...\python.exe" -I ...' (the uv venv trampoline cannot spawn its child under `ActiveProcessLimit=1`; reproduced a second time) |
| first run with an empty environment | build123d: `RuntimeError: Could not determine home directory` (set HOME and friends into the job dir) |
| five LLM-style scripts | flange (helper function, loop, f-string, math): ok V 32144.776; builder script with fillet/chamfer: `code.runtime` `Standard_NoSuchObject` (a real OCCT failure of the script, not the dialect); parameterised plate with `P`, lambda, try/except: ok V 11480.177 (hand 11480.18); `export_step`/`show` habit: `NameError` (becomes a hint); class definition: forbidden (becomes allowed) |

**PMI** (`probes/p9_pmi.py`): an XDE document with a shape, two face sub-shapes and one `XCAFDimTolObjects_DimensionObject` (type `Location_LinearDistance`, value 60.0) written with `AP242DIS`: the file contains `SHAPE_DIMENSION_REPRESENTATION` and `DIMENSIONAL_LOCATION`, schema `AP242_MANAGED_MODEL_BASED_3D_ENGINEERING_MIM_LF`; read back with `STEPCAFControl_Reader.SetGDTMode(True)`: 1 dimension label, type `Location_LinearDistance`, value 60.0.

**GLB writer** (`probes/p10_glb.py`): 888 bytes for a box, two calls equal, `pygltflib.GLTF2().load_binary` reads 1 mesh, accessors (36, SCALAR) and (8, VEC3).

**Reference reading** (installed `text-to-cad` 0.7.21): `dfm` SKILL.md: "a guided review skill, not an automatic feature-recognition or manufacturing certification engine"; `mold_tool.py` header: "never emits pass/fail", requires trimesh, numpy, rtree, scipy, networkx, lxml. `engineering-drawing` SKILL.md: output is one PDF, no DXF, no CLI, run through `uvx ... cadgen==0.7.21`, matplotlib. LibreDWG: `gh api repos/libredwg/libredwg/releases/latest` -> tag 0.14, assets `libredwg-0.14-win64.zip` 12,084,779 bytes and `-win32.zip`, `.sig` files (not downloaded).

**Not measured, so not claimed**: anything on Linux or macOS; any LibreDWG behaviour; `pdfplumber` on foreign PDFs; HLR on large models; PMI from vendor files; Open3D parity of the NumPy weld.

## Delivery plan

Ordered, each slice independently verifiable and ending in a check. The repository's test style applies throughout (hand calculation in the test docstring, engines skipped with the install hint when absent).

**Touch points in existing code**: `pyproject.toml` (extras, uv override), `licenses.py` (Engine fields, KNOWN rows), `gateway/server.py` (doctor: executable engines, `metadata_gap`), `domains/__init__.py` (`all_domains` adds geometry and drawing), `units.py` (an angle parser: `"30 deg"` accepted, a bare `30` rejected), `metrology/scan.py` and `metrology/tools.py` (migration to `occt`), `README.md`, `scripts/install.ps1`.

| # | Slice | Check |
| --- | --- | --- |
| **S1** | **One owner of STEP** (first slice). Create `occt/` (`EngineUnavailable`, `InputError`, `step.py`, `mesh.py` with the NumPy weld). Switch `scan.py` to it, delete `scan.read_model_mesh`, `_ocp`, `ScanEngineUnavailable`, `ScanInputError` (an alias for `ScanInputError` may live inside this slice only), update the three tests that call them. Add `Engine.kind/executable/metadata_gap` and the doctor path | `uv run pytest tests/test_metrology_check_scan*.py` green with unchanged expectations (68 + 1 tests); new `test_occt_mesh.py`: parity on box, cylinder at 0.001, two boxes, open shell, and each fixture under 20 random rigid transforms; `tests/test_architecture.py` rule 2 (`STEPControl_Reader` once) green; `rg STEPControl_Reader eng_toolkit` prints one file |
| S2 | **Process boundary + first capability**: `host.py`, `worker.py`, `sandbox_win.py` (POSIX stub that refuses code tasks), `geometry` extra, `geometry_measure` kinds `summary` and `inventory`, `topology.py`, `props.py`, `features.holes` | box 100 x 50 x 20: volume 100000, area 16000, centroid (50, 25, 10); the 3-hole bracket's inventory lists holes F6, F10, F11 as measured; a test-only task `sleep` is killed at the timeout; `os._exit` gives `engine.crash`; a native `print` in the worker does not corrupt the result line; spawn blocked (Windows-only test); `PYTHONHASHSEED` is 0 in the child; `toolkit_doctor` lists the new optional engines as informational |
| S3 | **`geometry_make_from_code`**: `dialect.py` (pure, testable with no engine), `tasks.build`, `meshio` (GLB, STL), `write_step` + header normalisation, `geometry-build` extra, uv override and stubs, `run_task(trust="script")` with the audit hook | bracket V 16497.345, flange 32144.776, param plate 11480.177 (hand calculations in the docstring); one failing and one passing script per `RULES` id; infinite loop killed; memory bomb `code.resource`; two runs equal normalised STEP sha; `sandbox` block present and truthful; stub guard test; `ENG_TOOLKIT_CODE_EXEC=off` gives `code.disabled` |
| S4 | **`geometry_measure` remaining kinds**: distance, diameter, angle, area, length, entity references | box and cylinder values above; `ref_not_found` and `not_solid` errors; ids from a `make_from_code` artifact usable in `measure` |
| S5 | **`drawing` extra + `drawing_read`** (DXF, PDF) with caps and `drawing.json` | DXF fixture counts and DIMLFAC display; hand-made text PDF `chars == 5`; truncated DXF `recovered: true`; file-size and entity caps; runs under the worker limits |
| S6 | **`drawing_make_views` geometry**: `views.py` with the four cleaning rules, layout, `sheet.py` (frame, title block, dimstyle, linetypes), `render.py` (PdfRender, SVG, PNG) | box with through-hole extents, hidden lines, duplicate removal; A4 page 841.89 pt; three runs under different hash seeds give equal DXF and PDF sha; read back with `drawing_read` equals the extents |
| S7 | **Auto dimensions and notes**: `dimension.py` planner, callouts, DIMLFAC at 1:2, caps and `dimensions_skipped` | dims read back with plain ezdxf: 100, 20, 50, diameter 10 `<> THRU`; at 1:2 drawn 50 x 10, text still 100; no overlapping dimension bounding boxes in a dense fixture |
| S8 | **`drawing_convert`** DXF to PDF/SVG/PNG with the loss report; DWG adapter `dwg.py` + `run_executable` | loss counts on a SPLINE + proxy fixture; shim-based DWG tests; real-binary smoke test marked and skipped by default |
| S9 | **`geometry_check_dfm`**: `features.internal_corners`, `wall_pairs`, `draft_angles`, `limits` schema, `not_checked` | hand cases in the test table; `limits=""` yields only `measured`; unknown limit key is `input.invalid` |
| S10 | **`geometry_convert_model`** and `measure(kind="pmi")` | GLB/STL byte sizes and parse; PMI round trip of the OCCT-authored fixture (60 mm) |

Order logic: S1 retires the ownership constraint while there is one consumer and its safety net already exists; S2 proves the process boundary with the smallest useful tool, so every later slice is a task function, not new infrastructure; S3 is the riskiest (code execution, a 250 MB extra) and goes before anything depends on it for fixtures (tests build fixtures with OCP directly, so nothing waits on it); S5 comes before S6 so the drawing tests have a reader; S9 comes after S6 to S7 because `holes` and the wall test are shared with the dimension planner; S10 last because the draft's own order puts conversion after the design loop.

First slice: **S1**. Justification: the hard constraint is an ownership rule, and it only gets more expensive as consumers appear; S1 changes no tool's behaviour, its check already exists (scan's tests), and without it S2 would be forced to copy the reader or to import scan from geometry.
