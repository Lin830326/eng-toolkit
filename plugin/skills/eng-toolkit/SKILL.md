---
name: eng-toolkit
description: Load before any eng-toolkit tool (mcp__eng-toolkit__*) and for any engineering calculation, simulation or CAD question (structures, machines, fluids, heat, control, signals, circuits, PCB, STEP, tolerances). Unit rules, ask-first, routing to the workflow skills.
---

# eng-toolkit: how to use the engineering tools

The `eng-toolkit` MCP server provides deterministic, unit-aware tools (`mcp__eng-toolkit__<domain>_<tool>`). If it is not connected, say so and give the command from `/eng-toolkit-setup`; never guess numbers instead. Before the first tool call also load the matching workflow skill from the routing list at the end: it holds ask-first lists, verified inputs, limits.

## Where numbers come from

- **A number that decides a result comes from a tool call.** Name the tool and field. Never present a number as a tool result unless a call returned it.
- Unit scaling (mm to m, rad/s to rpm) and sums or products of tool outputs are allowed, labelled **"my arithmetic"**; never call them tool output.
- A value from memory (datasheet, catalogue, standard table) is labelled **"from memory, not a tool result"**.

## Ask first (hard rule)

If an input that decides the answer is missing, ask BEFORE calculating, in one short list, and give no verdict ("no fan needed", "shaft OK") and no provisional numbers until it is known. Ask only what cannot be inferred (restate the model you infer and run it) and only the items of the matching list below; never say the person gave an input he did not. Never invent a pump curve, a motor, MOSFET or bearing datasheet value, a material grade, a load, a limit temperature, a safety or service factor, a reliability or an allowable. Typical lists:

- **Shaft, bearing, key, gear**: material grade, reliability, design and service factors, load positions, axial force direction (helical Fa adds a couple Fa*d/2), diameters either side of every shoulder.
- **Pressure vessel, bolts**: design (not working) pressure, allowable, joint efficiency, corrosion allowance, end shape (sphere = true hemisphere only; the tool warns), loaded area.
- **Thermal**: temperature limit, datasheet Rjc and interface resistance, conditions behind a catalogue Rth (dT, orientation, length).
- **Electronics**: amplifier: source impedance, coupling/bypass capacitors, load; converter: Vin range, part data.
- **CNC review**: shop limits and the milling spindle axis (the verdict depends on it).
- **Fits, stack-ups**: where each spacer or shim sits, whether it is selected at assembly, the operating temperature. A bearing seat: `metrology_fit_limits` gives the ISO 286 pair only; add the ring tolerance with `metrology_stackup_1d`. Position "from the bend": which datum?

## Outside the toolkit

Where no tool applies (nozzle reinforcement, spreading resistance, preloaded-bolt fatigue, code checks), say "outside the toolkit", give the standard method once, labelled **"hand method, not tool-checked"**, and do not iterate unverified numbers.

## Rules for every call

1. **Every physical input is a string with a unit**: `"100 mm"`, `"0 N"` (zero too). Pure coefficients (n, nu, Cd, counts) are plain numbers.
2. **Temperature differences use `K`** (or `delta_degC`); `degC` is absolute.
3. **Record lists** (`nodes`, `loads`, ...) are strict text: records split by `;` or newline, fields by `,`, units on every physical field; copy the parameter's example.
4. **Read the whole result**: `values`, `data`, `warnings`, `artifacts`, `error`. Read `error.message` and `error.details.param`, fix the input, retry once; if still refused, tell the user why. A refusal is an answer.
5. **Read the tool's self-reporting before saying "passed"** and repeat it: `data.measurements` (geometry_check_dfm: `bends: 0` or `smallest_wall_mm` null: that rule did not run), checks not performed, `limits[].source` (`caller` = YOUR assumption, `default` = rule of thumb), lower bounds (pcb_drc `at_kicad_limit` counts mean "at least"), warnings.
6. **Unreviewed** (`review_status`): never call a result approved, certified or code-compliant.
7. **Sanity-check** against a hand formula or second route (labelled); state `data.assumptions` and yours.
8. Reply in the user's language and script (Traditional vs Simplified Chinese). Four significant figures with units. The asked case first, then alternatives.
9. **Charts**: only with `plot_xy` / `plot_panels` / `plot_bar` (inline `series` for numbers from several results), handed over as a PNG: `format="png"`, path in `data.png_path`; Read the PNG once before describing it. Never hand-write SVG, ASCII or text charts or plotting code; if no picture can be shown, give a table.

## Tool map (`toolkit_list_tools` lists all)

- Domains: `structures structures3d mech machine fluids calc thermal fatigue electric optimize control dsp spice digital pcb fem geometry drawing metrology plot`. `kVA` is not `kW`; `optimize` formulas are plain numbers.
- External programs (`toolkit_doctor`): ngspice (`spice_*`), Yosys (`digital_*`), KiCad (`pcb_*`), Gmsh and CalculiX (`fem_*`). `geometry_view` and `fem_view` write a PNG next to the GLB.

## Workflows (pick by this list)

- Beams, frames, trusses, columns, plates: strength, deflection, buckling, vibration: **eng-structural-check**.
- Manufacturability of a STEP/IGES/BREP part (CNC, sheet metal): **eng-dfm-review**.
- Fatigue life, rainflow, notch, strain-life, crack growth: **eng-fatigue-check**.
- Motor and gearbox sizing, induction/PM motors, converters, transformers, faults: **eng-motor-drive-sizing**.
- Minimising, linear programs, equations, fits with intervals, uncertainty, DOE: **eng-optimize**.
- A chart to SEE (curve, spectrum, histogram): **eng-plot-results**. PNG plus coloured 3D model of a part, DFM findings or an FEM result: **eng-3d-view**.
- Fan or heatsink for a transistor, junction temperature, fins, radiation, exchanger, warm-up: **eng-thermal-check**.
- PI/PID loop from datasheet plant numbers, margins, discretising: **eng-control-tuning**.
- Spectrum of a sampled record, rpm to Hz, gear mesh, aliasing, filters, decimation: **eng-signal-analysis**.
- Pump and system curve, operating point, branches, pipe losses, DN/Kv, open channel, water hammer: **eng-flow-network**.
- Drawing of a STEP part (A4 to A0, PDF/PNG/DXF), reading DXF/PDF: **eng-drawing** FIRST, before any other drawing skill or script.
- KiCad board ready for fab (DRC/ERC, gerbers): **eng-pcb-review**.
- Analog circuit with ngspice (bias, Bode, transient, converters): **eng-circuit-sim**.
- Verilog lint, gate counts, equivalence of two designs: **eng-digital-synthesis**.
- Shafts with keys and bearings, gears, bolts, springs, welds, belts, pressure vessels: **eng-machine-design**.
- ISO 286 fits, bearing seats, tolerance stack-ups, true position, form error: **eng-tolerancing**.
- Anything else: pick the domain, read the tool description, call it, follow the rules above.

## Report format

Result with units; inputs and assumptions (yours, the tool's, from memory); checks (sanity, self-reporting, warnings); limits ("Unreviewed textbook estimate; not a design-code check" plus validity limits).
