# eng-toolkit

[English](README.md) | [繁體中文](README.zh-TW.md)

**Engineering tools an AI agent can call.** eng-toolkit is an MCP (Model Context Protocol) server that wraps
permissively licensed open-source engineering engines. Every result carries units, can be reproduced, and names its
source.

> **Results are not engineering-approved.** Every output says `review_status: "unreviewed"`. A qualified engineer
> must check a result before it is used for design or purchasing.

## Why

A language model that does engineering arithmetic in its head gets numbers wrong and cannot show where they came from.
eng-toolkit gives the model tools instead:

* **Units are required.** `"100 mm"` is accepted; `100` is refused with `units.bare_number`.
* **One return contract.** Every tool returns the same shape: the value with its unit, warnings, the files it wrote
  with their SHA-256, and the hash of the inputs.
* **Refusals the model can act on.** An error names the argument that caused it as a JSON pointer in
  `error.details.param`, so an agent can correct the call and retry.
* **No LLM inside.** The toolkit never calls a model and needs no API key. It works with any MCP client.
* **License policy enforced by tests.** Engines imported in-process are Apache, MIT, BSD, ISC or PSF. GPL programs
  run only as separate processes.

## Example

```json
{"bore": "100 mm", "rod": "50 mm", "pressure": "210 bar"}
```

`calc_cylinder_force` returns `push_force = 164.93 kN` and `pull_force = 123.70 kN`, with a warning that seal
friction and back pressure are not included.

## Install

You need [uv](https://docs.astral.sh/uv/). It installs Python 3.12 for you.

```bash
git clone https://github.com/Lin830326/eng-toolkit.git
cd eng-toolkit
uv sync --all-groups --extra scan --extra drawing --extra png
uv run eng-toolkit doctor        # health check; exit 0 = healthy
```

On a clean Windows 11 machine, `powershell -ExecutionPolicy Bypass -File scripts\install.ps1` installs uv and does the
rest. The project is developed and tested on Windows 11; other platforms are not verified yet.

A plain `uv sync` installs only the core. The optional extras (`cad`, `scan`, `drawing`, `png`) add OpenCASCADE,
Open3D, ezdxf and pypdfium2. Without an extra, its tools still list and a call returns `engine.unavailable` with the
install command.

## Connect an MCP client

Claude Code:

```bash
claude mcp add --scope user eng-toolkit -- uv run --extra cad --extra drawing --extra png --directory "C:\path\to\eng-toolkit" eng-toolkit
```

Cursor, LM Studio and other clients that take a JSON configuration:

```json
{
  "mcpServers": {
    "eng-toolkit": {
      "command": "uv",
      "args": ["run", "--directory", "C:\\path\\to\\eng-toolkit", "eng-toolkit"]
    }
  }
}
```

Streamable HTTP: run `uv run eng-toolkit serve --transport http --host 127.0.0.1 --port 8765` and point the client at
`http://127.0.0.1:8765/mcp`. With HTTP, set `ENG_TOOLKIT_READ_DIRS` to the directories the tools may read.

A Claude Code plugin with workflow skills and slash commands is in [plugin/](plugin/README.md).

## Domains

Each domain is a sub-server with at most 6 tools. The gateway adds `toolkit_list_tools` and `toolkit_doctor`
([reference](docs/tools/toolkit.md)). The per-domain reference pages are in 繁體中文 for now; translations are welcome.

| Domain | What it covers | Tools | Needs |
|---|---|---|---|
| [`calc`](docs/tools/calc.md) | Hydraulics: cylinder force and speed, pipe pressure drop, orifice flow, pump power, accumulator precharge | `calc_cylinder_force` `calc_pipe_pressure_drop` `calc_orifice_flow` `calc_pump_power` `calc_accumulator_precharge` `calc_cylinder_speed` | — |
| [`metrology`](docs/tools/metrology.md) | ISO 286 limits and fits, fit search, 1-D tolerance stack-up, form error, position tolerance, scan against a STEP model | `metrology_fit_limits` `metrology_stackup_1d` `metrology_fit_search` `metrology_check_scan` `metrology_form_error` `metrology_position_tolerance` | `scan` extra for `metrology_check_scan` |
| [`geometry`](docs/tools/geometry.md) | Measure STEP, IGES and BREP models, build parts from a JSON spec, DFM checks, format conversion, 3D views | `geometry_measure` `geometry_make_part` `geometry_check_dfm` `geometry_convert` `geometry_view` | `cad` extra |
| [`drawing`](docs/tools/drawing.md) | Orthographic views with hidden lines, title block and dimensions as DXF, PDF, SVG and PNG; read and convert drawings | `drawing_make_views` `drawing_read` `drawing_convert` | `drawing` extra; `png` extra for PNG; LibreDWG `dwg2dxf` for DWG |
| [`control`](docs/tools/control.md) | Poles, zeros and stability, time and frequency response, feedback, PID tuning, discretization | `control_analyze` `control_response` `control_frequency` `control_feedback` `control_pid_tune` `control_discretize` | — |
| [`digital`](docs/tools/digital.md) | Verilog synthesis, lint and equivalence checking | `digital_synthesize` `digital_check` `digital_equivalent` | Yosys |
| [`dsp`](docs/tools/dsp.md) | IIR and FIR filter design, spectra, filtering, sampling, correlation, windows | `dsp_filter_design` `dsp_spectrum` `dsp_filter_apply` `dsp_sampling` `dsp_correlate` `dsp_window` | — |
| [`electric`](docs/tools/electric.md) | Transformers, induction and PM/DC motors, three-phase power, drive sizing, DC-DC converters | `electric_transformer` `electric_induction_motor` `electric_pm_dc_motor` `electric_power_flow` `electric_drive_sizing` `electric_converter` | — |
| [`fatigue`](docs/tools/fatigue.md) | Stress-life, rainflow counting, strain-life, crack growth, material data | `fatigue_sn` `fatigue_rainflow` `fatigue_strain_life` `fatigue_fracture` `fatigue_material` | — |
| [`fem`](docs/tools/fem.md) | Tetrahedral meshing and linear static analysis of a solid, coloured result views | `fem_mesh` `fem_static` `fem_view` | `cad` extra, Gmsh, CalculiX |
| [`fluids`](docs/tools/fluids.md) | Pipe networks, pump systems, open channels, compressible flow, water hammer, external drag | `fluids_pipe_network` `fluids_pump_system` `fluids_open_channel` `fluids_compressible` `fluids_surge` `fluids_external_flow` | — |
| [`machine`](docs/tools/machine.md) | Shafts, spur gears, weld groups, pressure vessels, belts, keys and splines | `machine_shaft` `machine_gear` `machine_weld` `machine_pressure_vessel` `machine_belt` `machine_key` | — |
| [`mech`](docs/tools/mech.md) | Bolt check, bearing life, compression springs, beam bending | `mech_bolt_check` `mech_bearing_life` `mech_spring_compression` `mech_beam_bending` | — |
| [`optimize`](docs/tools/optimize.md) | Constrained minimization, LP/MIP, root finding, curve fitting, sensitivity, design of experiments | `optimize_minimize` `optimize_linprog` `optimize_root` `optimize_fit` `optimize_sensitivity` `optimize_doe` | — |
| [`pcb`](docs/tools/pcb.md) | Board facts, design rule check, electrical rule check, gerber/drill/position export | `pcb_info` `pcb_drc` `pcb_erc` `pcb_export` | KiCad (`kicad-cli`) for DRC, ERC and export |
| [`plot`](docs/tools/plot.md) | Deterministic SVG or PNG charts from CSV artifacts or inline series | `plot_xy` `plot_panels` `plot_bar` | `png` extra for PNG |
| [`spice`](docs/tools/spice.md) | Operating point, DC, AC and transient circuit simulation, netlist check, measurements | `spice_simulate` `spice_check_netlist` `spice_measure` | ngspice |
| [`structures`](docs/tools/structures.md) | Section properties, trusses, plane frames, modes, column buckling, stress states | `structures_section` `structures_truss` `structures_frame` `structures_modal` `structures_column` `structures_stress` | — |
| [`structures3d`](docs/tools/structures3d.md) | Space frames, modal analysis, linear buckling, thin plates, Winkler foundations | `structures3d_static` `structures3d_modal` `structures3d_buckling` `structures3d_plate` `structures3d_plate_stability` `structures3d_foundation` | — |
| [`thermal`](docs/tools/thermal.md) | Conduction, heat exchangers, fins, transient conduction, radiation, convection | `thermal_conduction` `thermal_exchanger` `thermal_fin` `thermal_transient` `thermal_radiation` `thermal_convection` | — |

## Architecture

```
MCP client (Claude Code, Codex, Cursor, LM Studio, ...)
        |  stdio or streamable HTTP (JSON-RPC)
        v
+--------------------------------------------------------------+
|  gateway (FastMCP)       toolkit_list_tools   toolkit_doctor |
|    mounts every package under eng_toolkit/domains/ that      |
|    exposes DOMAIN, as <domain>_<tool>                        |
+--------------------------------------------------------------+
|  domains: calc  metrology  geometry  drawing  control  ...   |
|    in-process engines: permissive licenses only              |
|    GPL programs (Gmsh, CalculiX, KiCad): separate processes  |
+--------------------------------------------------------------+
|  contract.py   one return shape for every tool (pydantic)    |
|  units.py      unit gate (pint): bare numbers are refused    |
|  workspace.py  ws/<job-id>/ files with sha256                |
|  licenses.py   license tiers and environment scan            |
+--------------------------------------------------------------+
```

Domains are discovered: a package under `eng_toolkit/domains/` that exposes `DOMAIN` is mounted, and no shared list
is edited. See [docs/adding-a-domain.md](docs/adding-a-domain.md) and the
[gateway design](docs/gateway/design.md).

## Return contract

```json
{
  "ok": true,
  "value":  {"quantity": 164.93, "unit": "kilonewton", "uncertainty": null},
  "values": {"push_force": {"...": "..."}, "pull_force": {"...": "..."}},
  "artifacts": [{"path": "ws/<job-id>/inputs.json", "format": "json", "sha256": "..."}],
  "provenance": {"engine": "eng_toolkit.calc.hydraulics", "inputs_hash": "sha256:...", "input_files": []},
  "warnings": ["Ideal theoretical force: ..."],
  "error": null,
  "review_status": "unreviewed"
}
```

The same inputs give the same `inputs_hash`. Each call writes `inputs.json`, `result.json` and `manifest.json` under
`ws/<job-id>/`.

## Contributing

Bug reports, verification cases, new tools and new domains are welcome, in English or 繁體中文. Read
[CONTRIBUTING.md](CONTRIBUTING.md) first. Report vulnerabilities privately as described in
[SECURITY.md](SECURITY.md).

The project is clean-room: it contains no code, data, models, drawings or branding of any employer or client, and
contributions must keep it that way.

## License

Apache-2.0, see [LICENSE](LICENSE). The Claude Code plugin under `plugin/` is MIT, see
[plugin/LICENSE](plugin/LICENSE). The license tier of every dependency is registered in `eng_toolkit/licenses.py` and
checked by `toolkit_doctor`.
