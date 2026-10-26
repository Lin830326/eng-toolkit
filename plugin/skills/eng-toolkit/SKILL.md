---
name: eng-toolkit
description: Use for engineering calculations, simulation or CAD questions the eng-toolkit MCP tools can answer (structures, machines, fluids, heat, control, signals, circuits, PCB, STEP, tolerances, drawings). Tool map, unit rules, ask-first, reporting. Load before any mcp__eng-toolkit__ tool.
---

# eng-toolkit: how to use the engineering tools

The `eng-toolkit` MCP server provides deterministic, unit-aware tools (`mcp__eng-toolkit__<domain>_<tool>`). If it is not connected, say so and give the command from `/eng-toolkit-setup`; never fall back to guessed numbers. Choose a workflow skill by the routing list at the end.

## Where numbers come from

- **A number that decides a result comes from a tool call.** Name the tool and field. Never present a number as a tool result unless a call returned it.
- Simple unit scaling (mm to m, rad/s to rpm, N to kN) and sums or products of tool outputs are allowed, but label them **"my arithmetic"**. Do not use a tool as a calculator, and do not hand-scale a value and call it tool output.
- A value from memory (datasheet, catalogue, standard table) is labelled **"from memory, not a tool result"**.

## Ask first (hard rule)

If an input that decides the answer is missing, ask BEFORE calculating, in one short list, and give no verdict ("no fan needed", "shaft OK") until it is known. Never invent a pump curve, a motor, MOSFET or bearing datasheet value, a material grade, a load, a limit temperature, a safety or service factor, a reliability or an allowable. Illustrations with assumed values are labelled "invented values" and give no conclusion. Typical lists:

- **Shaft, bearing, key, gear**: material grade and size range, reliability, design and service factors, load positions, axial force direction (helical Fa adds a couple Fa*d/2), diameters either side of every shoulder.
- **Pressure vessel, flange, bolts**: design (not working) pressure, allowable, joint efficiency, end shape (sphere formula = true hemisphere only; 2:1 ellipsoidal and torispherical ends need about the shell thickness), loaded area.
- **Thermal**: temperature limit, datasheet Rjc and interface resistance, conditions behind a catalogue Rth (dT, orientation, length). Vertical natural-convection length = the extent the air rises along; heat-sink fins: `thermal_fin` `tip="convective"`.
- **Electronics**: amplifier: source impedance, coupling/bypass capacitors, load; converter: Vin range, part data.
- **Fits, stack-ups**: where each spacer or shim sits, and whether it is selected at assembly. A bearing seat: `metrology_fit_limits` gives the ISO 286 pair only; combine with the ring tolerance via `metrology_stackup_1d`; light-alloy or hot housings need tighter outer-ring fits (K7/M7), not looser. Position "from the bend": which datum?

## Outside the toolkit

Where no tool applies (nozzle reinforcement, spreading resistance, preloaded-bolt fatigue, code checks), say "outside the toolkit", give the standard method once, labelled **"hand method, not tool-checked"**, and do not iterate unverified numbers.

## Rules for every call

1. **Every physical input is a string with a unit**: `"100 mm"`, `"0 N"` (zero too). Pure coefficients (n, nu, Cd, counts) are plain numbers.
2. **Temperature differences use `K`** (or `delta_degC`); `degC` is absolute.
3. **Record lists** (`nodes`, `loads`, ...) are strict text: records split by `;` or newline, fields by `,`, units on every physical field. Copy the parameter's example (no headers or trailing separators).
4. **Read the whole result**: `values`, `data` (assumptions, convergence), `warnings`, `artifacts`, `error`. An `error` is final: read `error.message` and `error.details.param`, fix the input, retry once; if still refused, tell the user why. A refusal (mechanism, ill-conditioned, out of range) is an answer, not an obstacle.
5. **Read the tool's self-reporting before saying "passed"** and repeat it: `data.measurements` (geometry_check_dfm: `bends=0` or `smallest_wall` null means that rule was not exercised), checks not performed, `limits[].source` (`caller` = YOUR assumption, `default` = toolkit rule of thumb; neither is a shop's or standard's rule), capped counts (pcb_drc stops counting near 499: say "at least"), warnings.
6. **Unreviewed** (`review_status`): never call a result approved, certified or code-compliant; no tool does a design-code check.
7. **Sanity-check** against a hand formula or second route (labelled as such); say when they disagree. State the tool's assumptions (`data.assumptions`) and yours.
8. Reply in the user's language and script (Traditional vs Simplified Chinese). Four significant figures with units.
9. **Charts**: only with `plot_xy` / `plot_panels` / `plot_bar`, also for numbers from several tool results (inline `series`). Never hand-write SVG, ASCII charts or plotting code; if the picture cannot be shown, give a table.

## Tool map (domain: tools)

- **structures** `section truss frame modal column stress`; **structures3d** `static modal buckling plate plate_stability foundation`; **mech** `beam_bending bolt_check bearing_life spring_compression`; **machine** `shaft gear weld pressure_vessel belt key`.
- **fluids** (`pipe_network pump_system open_channel compressible surge external_flow`), **calc** (hydraulics); **thermal** `conduction convection radiation fin exchanger transient`.
- **fatigue**: `sn rainflow strain_life fracture material` (cycle counts as `cycle`/`count`); **electric**: `transformer induction_motor pm_dc_motor power_flow drive_sizing converter` (`kVA` is not `kW`); **optimize**: `minimize linprog root fit sensitivity doe` (plain-number formulas, no units inside).
- **control**, **dsp**; **electronics**: `spice_*` (ngspice), `digital_*` (Yosys), `pcb_*` (KiCad), `fem_{mesh,static,view}` (Gmsh, CalculiX): see `toolkit_doctor`.
- **geometry / drawing**: `geometry_{measure,check_dfm,make_part,convert,view}` (`geometry_view` and `fem_view` write a PNG picture next to the GLB), `drawing_{make_views,read,convert}`; **metrology** `fit_limits fit_search stackup_1d form_error position_tolerance check_scan`; **plot** `plot_xy plot_panels plot_bar` (SVG by default; `format="png"` also writes `plot.png`, path in `data.png_path`, needs the png extra). `toolkit_list_tools` lists all.

## Workflows (pick by this list)

- Strength, deflection, buckling, vibration of beams, frames, trusses, columns: **eng-structural-check** (`/eng-structural-check`).
- Manufacturability of a STEP/IGES/BREP part (CNC, sheet metal): **eng-dfm-review** (`/eng-dfm-review`).
- Fatigue life, infinite life, rainflow, notch, strain-life, crack growth: **eng-fatigue-check**.
- Motor and gearbox sizing, induction/PM motors, converters, transformers, faults: **eng-motor-drive-sizing**.
- Minimising, linear programs, equations, fits with intervals, uncertainty, DOE: **eng-optimize**.
- A chart the user wants to SEE (curve, spectrum, histogram; `format="png"`, hand over `data.png_path`): **eng-plot-results**. A PNG and coloured 3D model of a part, DFM findings or an FEM result: **eng-3d-view**.
- Heat transfer, heatsink or junction temperature, fan or not, fins, radiation, exchanger duty, warm-up time: **eng-thermal-check**.
- Tuning or checking a PI/PID loop from datasheet plant numbers, step response, margins, saturation, discretising: **eng-control-tuning**.
- Spectrum and peaks of a sampled record, rpm to Hz, gear mesh, aliasing, anti-alias filter, decimation: **eng-signal-analysis**.
- Pump and system curve, operating point, parallel branches, pipe losses with DN/Kv, open channel, water hammer: **eng-flow-network**.
- Dimensioned engineering drawings (A4 to A0, PDF/PNG/DXF) of a STEP part, hole callouts, reading DXF/PDF drawings: **eng-drawing** FIRST, before any other drawing skill or script.
- Reviewing a KiCad board or schematic before fabrication (DRC/ERC, fab-rule checklist, gerbers): **eng-pcb-review**.
- Simulating an analog circuit with ngspice (bias, Bode, transient, converter waveforms): **eng-circuit-sim**.
- Verilog lint, gate counts, equivalence of two Verilog designs (proof or bounded check): **eng-digital-synthesis**.
- Shafts with keys and bearings, gears, bolts, springs, welds, belts, pressure vessels: **eng-machine-design**.
- ISO 286 fits, bearing seat fits, tolerance stack-ups, true position, form error: **eng-tolerancing**.
- Anything else: pick the domain above, read the tool description for the inputs, call it, and follow the rules above.

## Report format

Result with units; inputs and assumptions (yours, the tool's, from memory); checks (sanity, tool self-reporting, warnings); limits ("Unreviewed textbook estimate; not a design-code check" plus validity limits).
