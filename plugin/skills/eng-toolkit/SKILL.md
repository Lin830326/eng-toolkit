---
name: eng-toolkit
description: Use when the user asks an engineering calculation, simulation or CAD question that the eng-toolkit MCP tools can answer (beams, frames, trusses, columns, stress, plates, shafts, gears, welds, pressure vessels, fluids and pipe networks, heat transfer, control, signals, SPICE/digital/PCB checks, STEP files, tolerances). Gives the tool map, unit rules and the reporting rules. Always load it before calling any mcp__eng-toolkit__* tool.
---

# eng-toolkit: how to use the engineering tools

The `eng-toolkit` MCP server provides deterministic, unit-aware calculation tools (100 tools, tool names `mcp__eng-toolkit__<domain>_<tool>`). **Compute with the tools; never do the arithmetic yourself and never invent numbers.** If the server is not connected, say so and give the registration command from the `/eng-toolkit-setup` command; do not fall back to guessing.

## Rules that apply to every call

1. **Every physical input is a string with a unit**: `"100 mm"`, `"210 bar"`, `"200 GPa"`, `"1e6 mm^4"`. Bare numbers for physical quantities are refused. Pure coefficients (n, nu, Cd, counts) are plain numbers. Zero also needs a unit (`"0 N"`).
2. **Temperature differences use `K`** (or `delta_degC`); `degC`/`celsius` mean an absolute temperature and are refused where a difference is meant.
3. **Record-list parameters** (`nodes`, `members`, `supports`, `loads`, ...) are strict text: records separated by `;` or newline, fields by `,`, units on every physical field. Copy the example in the parameter description exactly; do not add headers, quotes or trailing separators.
4. **Read the whole result**, not just `value`: `values` (named, with units), `data` (assumptions, stability, convergence), `warnings`, `artifacts` (CSV/JSON files with tables) and `error`. An `error` is final: read `error.message` and `error.details.param` (a JSON pointer to the offending input), fix the input, retry once; if it is still refused, tell the user why. A refusal such as a mechanism, an ill-conditioned model, a value out of range, or "detached shock" is a real engineering answer, not something to work around.
5. **Every result is `review_status: "unreviewed"`**: textbook estimates for engineering judgement. Never call a result approved, certified or code-compliant, and never claim a design-code check (AISC, Eurocode, ASME, AGMA...) was done: none of the tools does one.
6. **Sanity-check before reporting**: compare with a hand formula (`P L^3/(3 E I)`, `p r/t`, ...) or a second tool; look at equilibrium residuals, convergence data and warnings. If two independent routes disagree, say so.
7. **State assumptions** the tool made (in `data.assumptions`/`validity`) and the user's inputs you assumed (material, support type, load direction). Ask the user instead of guessing when a missing input changes the answer by more than a few percent (support conditions, load case, allowable stress, units).
8. Reply in the user's language. Keep the numbers with units and sensible significant figures (4 digits unless the user asks otherwise).

## Tool map (domain: tools)

- **structures** (2D, linear): `section` (area, I, centroid, Z, J of standard shapes or polygons), `truss` (2D/3D pin-jointed), `frame` (2D beams/frames, releases, springs, member loads, M/V/N diagrams), `modal` (frequencies of 2D frames), `column` (Euler, Johnson, secant buckling), `stress` (Mohr, principal, von Mises/Tresca, rosettes, Hooke).
- **structures3d**: `static` (space frames), `modal`, `buckling` (linear buckling load factors), `plate` (Kirchhoff plate bending), `plate_stability` (plate buckling and vibration), `foundation` (beam on elastic foundation).
- **mech**: `beam_bending` (one-span closed form), `bolt_check`, `bearing_life`, `spring_compression`.
- **machine**: `shaft` (static + fatigue + critical speed), `gear` (spur Lewis/Hertz), `weld` (fillet weld groups), `pressure_vessel` (thin/thick wall), `belt`, `key` (key and spline).
- **fluids**: `pipe_network`, `pump_system`, `open_channel`, `compressible`, `surge` (water hammer), `external_flow` (drag, terminal velocity). **calc**: `pipe_pressure_drop`, `orifice_flow`, `pump_power`, `cylinder_force`, `cylinder_speed`, `accumulator_precharge`.
- **thermal**: `conduction`, `convection`, `radiation`, `fin`, `exchanger`, `transient`.
- **fatigue**: `sn` (S-N, Marin factors, mean-stress and notch corrections), `rainflow` (ASTM E1049 counting + Miner damage), `strain_life` (Coffin-Manson-Basquin, Neuber), `fracture` (stress intensity, critical crack size, Paris/Forman growth), `material` (true/engineering stress-strain, Ramberg-Osgood, Larson-Miller, typical properties; counts of cycles must be given as `cycle`/`count`, never as an angle).
- **electric**: `transformer`, `induction_motor` (torque-speed), `pm_dc_motor` (DC, PMSM/BLDC dq, field weakening), `power_flow` (three-phase, symmetrical components, fault currents), `drive_sizing` (torque/inertia/duty), `converter` (buck/boost/buck-boost). `kVA` is not `kW`.
- **optimize**: `minimize`, `linprog`, `root`, `fit` (nonlinear least squares with confidence intervals), `sensitivity` (GUM, Monte Carlo, Sobol), `doe` (designs and response surfaces). Formulas are passed as plain-number expressions (`x^2+sin(y)`, grammar in the tool description): variables carry no units inside an expression.
  Expression underflow refuses. Root brackets constrain every multistart solve; root systems must pass a Newton-correction accuracy check. Fits check objective curvature before inference and normalize relative weights. Second-order sensitivity refuses unresolved Hessians, including cancellation behind large output offsets.
- **control / dsp**: `control_{analyze,response,frequency,feedback,pid_tune,discretize}`; `dsp_{filter_design,filter_apply,spectrum,sampling,correlate,window}`.
- **electronics**: `spice_{simulate,measure,check_netlist}` (ngspice), `digital_{synthesize,check,equivalent}` (Yosys), `pcb_{info,drc,erc,export}` (KiCad), `fem_{mesh,static}` (Gmsh + CalculiX). These need external programs; run `toolkit_doctor` to see what is installed.
- **geometry / drawing**: `geometry_measure`, `geometry_check_dfm`, `geometry_make_part` (build a part from a text spec), `geometry_convert` (STEP/IGES/BREP to STEP/BREP/STL/GLB), `geometry_view` (a PNG picture to paste into a mail plus a coloured GLB: by face kind, by DFM findings, or highlighted faces in one or several named colour groups); `drawing_make_views`, `drawing_read`, `drawing_convert`. Needs the `cad`/`drawing` extras.
- **metrology**: `fit_limits` (ISO 286 fits), `fit_search`, `stackup_1d`, `form_error`, `position_tolerance`, `check_scan`.
- **plot**: `plot_xy` (lines/scatter/step/stem, log axes, second axis, bands, fill-to-zero), `plot_panels` (stacked panels sharing x: Bode, waveforms), `plot_bar` (bars, histograms). They read the CSV artifacts other tools write (`artifacts[].path`, e.g. `filter_response.csv`, `member_1_stations.csv`) or inline series and return an SVG (`data.svg_inline` when small, file `plot.svg`); `format="png"` (or `"both"`) also writes `plot.png`, 1600 px wide, path in `data.png_path` (needs the png extra, `uv sync --extra png`; without it the SVG comes back with a warning). Use `x_scale=log` for S-N life axes and for frequency tables that start above 0 Hz (a table with a 0 Hz row, such as `dsp_filter_design`'s linear grid, is refused on a log axis: plot it linear; `x_range` does not avoid the refusal); give axis labels with units; when the user asked to see a curve or diagram, ask for `format="png"`, give them the `data.png_path` and also say in words what the chart shows (never only an SVG path).
- **toolkit**: `toolkit_list_tools` (everything installed), `toolkit_doctor` (engine/external program health).

## Workflows

- Structural strength / deflection / buckling / vibration of beams, frames, trusses, columns: use the **eng-structural-check** skill (`/eng-structural-check`).
- Manufacturability of a STEP/IGES/BREP part for CNC milling or sheet metal: use the **eng-dfm-review** skill (`/eng-dfm-review`).
- Fatigue life, infinite-life check, rainflow and Miner damage, notch and strain-life, crack growth: **eng-fatigue-check** (`/eng-fatigue-check`).
- Sizing a motor and gearbox for a motion profile, induction or PM motor operating point, converters, transformers, three-phase faults: **eng-motor-drive-sizing** (`/eng-motor-drive-sizing`).
- Minimising, linear programs, solving equations, fitting measured data with confidence intervals, uncertainty propagation, design of experiments: **eng-optimize** (`/eng-optimize`).
- Turning results (CSV artifacts or inline series) into a chart: **eng-plot-results** (`/eng-plot-results`).
- A picture (PNG, to paste into a mail) and a coloured 3D model (GLB) of a part, its DFM findings or an FEM stress/displacement result: **eng-3d-view** (`/eng-3d-view`). `geometry_view` and `fem_view` write the PNG by default; hand it over first.
- A curve, spectrum, diagram or histogram the user wants to SEE: call the tool that makes the table, then `plot_xy`/`plot_panels`/`plot_bar` on its CSV artifact with `format="png"`, then give the person the `data.png_path` and say in words what the chart shows.
- Anything else: pick the domain above, read the tool description for the inputs, call it, and follow the rules above.

## Report format (use for any final answer with numbers)

1. **Result**: the key numbers with units, one line each.
2. **Inputs and assumptions**: what you used (including anything you assumed) and what the tool assumed.
3. **Checks**: the sanity check and its outcome, plus relevant warnings from the tool.
4. **Limits**: "Unreviewed textbook estimate; not a design-code check" and the specific validity limits that matter here.
