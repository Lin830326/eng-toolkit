# eng-toolkit plugin for Claude Code

Skills and slash commands that teach an AI how to use the eng-toolkit MCP server well.
The plugin carries **no calculation code**; the numbers always come from the MCP tools.

Argument validation failures return the same structured refusal as calculation
errors: check `ok`, `error.family` and the JSON pointer in `error.details.param`.
Bare quantity numbers use `units.bare_number`; missing required arguments use
`input.missing`; other type errors and unknown arguments use `input.invalid`.
Use K or explicit delta units in compound quantities; offset units such as
`degC` and `degF` are accepted only as plain absolute temperatures. JSON job
artifacts preserve nonfinite inputs as quoted text. See the
[shared gateway design](../docs/gateway/design.md).

| Piece | What it does |
|---|---|
| skill `eng-toolkit` | the ask-first lists (no invented datasheet values or verdicts on missing inputs), where numbers may come from, reading a tool's self-reporting, coverage limits, the tool map, the report format. The seven hard rules themselves travel with the MCP server instructions, so a client that never opens a skill still sees them |
| skill `eng-structural-check` + `/eng-structural-check` | beam, frame, truss, column, plate, vibration checks: model choice, inputs, hand check (a truss reports its summed joint loads in `data.applied_total_fx/fy` to compare with the hand total), convergence, utilization |
| skill `eng-dfm-review` + `/eng-dfm-review` | CNC milling and sheet-metal review of a STEP/IGES/BREP file: spindle axis to ask for (default +z is flagged), bends that were not recognised or only within a relaxed tolerance, findings ringed in the picture |
| skill `eng-fatigue-check` + `/eng-fatigue-check` | infinite-life check with Marin factors and mean stress, rainflow + Miner damage (record range, spike and dominant-cycle checks), notches, strain-life, crack growth; preloaded bolts are outside the toolkit |
| skill `eng-motor-drive-sizing` + `/eng-motor-drive-sizing` | motor and gearbox sizing for a motion profile or move plus dwell (`strokes_per_cycle=2` for an out-and-back axis), induction and PM motor operating points, converters, transformers, faults |
| skill `eng-optimize` + `/eng-optimize` | minimise, linear programs, root finding, curve fitting with intervals (initial values set the parameter scales), uncertainty propagation, design of experiments |
| skill `eng-plot-results` + `/eng-plot-results` | charts from other tools' CSV artifacts or inline series: Bode (with phase unwrapping), S-N, histograms, bars with limit lines, station diagrams. The PNG is written by default and appears in the reply; its path is still handed over |
| skill `eng-3d-view` + `/eng-3d-view` | a PNG picture (shown in the reply, to paste into a mail) and a coloured GLB of a part, DFM findings (ringed, with a close-up panel), named face groups or an FEM stress/displacement result, with the mesh-convergence recipe |
| skill `eng-thermal-check` + `/eng-thermal-check` | heatsink and junction temperature (catalogue Rth is valid only at its quoted temperature rise; natural convection between fins with the channel model), insulation, fins, radiation, exchanger duty, warm-up curve from one lumped-transient call |
| skill `eng-control-tuning` + `/eng-control-tuning` | PI/PID from datasheet plant numbers (V/krpm trap), exact-plant check, phase and delay margin, current-limit check, Tustin difference equation |
| skill `eng-signal-analysis` + `/eng-signal-analysis` | spectrum and peak naming (rpm to Hz, gear mesh), alias check, anti-alias filter and decimation by specification, sample files with or without a header line |
| skill `eng-flow-network` + `/eng-flow-network` | pump and system curve (`curve_points` writes the table and the operating point), branch flow split, DN to bore, Kv and datasheet dp to K, open channel, nozzle, drag, water hammer |
| skill `eng-drawing` + `/eng-drawing` | dimensioned A4-A0 drawing (PDF, PNG, DXF) of a STEP part with hole callouts and hole list; read and convert DXF and PDF drawings |
| skill `eng-pcb-review` + `/eng-pcb-review` | KiCad board and schematic review before fabrication: board facts, DRC and ERC (real findings versus library warnings, counts at kicad-cli's listing limit), checklist against the fab's rules, gerber export (layers, drill and job file in one zip), board-fitted PDF and SVG |
| skill `eng-circuit-sim` + `/eng-circuit-sim` | ngspice from a verbal circuit description: bias, Bode (wrapped and unwrapped phase), transient windows, band-pass corners, converter sizing and its limits |
| skill `eng-digital-synthesis` + `/eng-digital-synthesis` | Verilog lint, unit-gate counts, equivalence proofs versus bounded checks, wide counters |
| skill `eng-machine-design` + `/eng-machine-design` | shafts with keys and bearings (helical gear loads by hand; the gear tool is spur only), bolts, springs, welds, belts, pressure vessels: what to ask and what the tools cannot do |
| skill `eng-tolerancing` + `/eng-tolerancing` | ISO 286 fits, bearing seat fits, tolerance stack-ups, true position, form error |
| `/eng-toolkit-setup` | connection and dependency check, registration command |
| prompt hook (`hooks/hooks.json`) | one short reminder with every prompt: numbers that decide a result come from an `mcp__eng-toolkit__` call; unit scaling and sums of tool outputs are allowed only labelled "my arithmetic"; values from memory are labelled "from memory, not a tool result"; missing deciding inputs are asked for, not invented. Delete `hooks/` to turn it off |

## Install

1. Register the MCP server once (the repository path is yours):

   ```bash
   claude mcp add --scope user eng-toolkit -- uv run --extra cad --extra drawing --extra png --directory "C:\path\to\eng-toolkit" eng-toolkit
   ```

2. Add the plugin from the repository's marketplace file:

   ```
   /plugin marketplace add C:\path\to\eng-toolkit
   /plugin install eng-toolkit@eng-toolkit-marketplace
   ```

   To try it without installing: `claude --plugin-dir C:\path\to\eng-toolkit\plugin`.

3. Ask in plain language, or use the commands:

   ```
   /eng-structural-check 4 m steel cantilever (E 200 GPa, A 10000 mm2, I 1e8 mm4), 10 kN tip load: tip deflection and bending stress
   /eng-dfm-review C:\parts\bracket.step cnc_milling tool radius 2 mm, min wall 2 mm, spindle from the top
   /eng-fatigue-check 30 mm machined shaft, Su 690 MPa, bending 110 MPa amplitude with 60 MPa mean: infinite life?
   /eng-motor-drive-sizing 10:1 reducer, 0.8 kg*m^2 load, 12 rad/s in 0.6 s, hold 1.8 s: peak and RMS motor torque
   /eng-optimize fit T = Tinf + A*exp(-t/tau) to these cooling data with a 95 % interval on tau
   /eng-plot-results Bode plot of the filter just designed
   /eng-3d-view C:\parts\bracket.step colour by DFM findings
   /eng-thermal-check TO-220 regulator, 5 W on a catalogue 6 K/W heatsink in a 40 C box, Tj rule 88 C: fan needed? warm-up of the 120 g sink
   /eng-control-tuning 24 V brushed motor velocity loop, 80 ms rise, <5 % overshoot, 2 kHz controller: gains and difference equation
   /eng-signal-analysis vib.csv at 8 kHz, motor 1750 rpm, 17-tooth pinion: spectrum peaks, are 1x and 2x there, decimate by 4
   /eng-flow-network chilled loop DN50 40 m, plate exchanger, two branches, pump curve points: operating point and split
   /eng-drawing C:\parts\plate.step A3 drawing with top, front, right and iso views, PNG and PDF, plus a hole list
   /eng-pcb-review C:\boards\main.kicad_pcb and main.kicad_sch: can I send this to a fab that allows 0.2 mm tracks?
   /eng-circuit-sim NPN common-emitter amplifier, 15 V, 68k/12k bias, 3.3k collector, 680 ohm emitter with 220 uF: bias, gain, Bode
   /eng-digital-synthesis is my lean terminal-count counter equivalent to the reference one? gate counts for both
   /eng-machine-design output shaft of a helical gearbox, 18 kW at 960 rpm: shaft diameter, key length, bearing life
   /eng-tolerancing 6207 bearing on a 35 mm shaft in an aluminium housing: shaft and housing fits and the end float stack-up
   ```

## Does it help? (Claude Haiku, `scripts/eval_plugin.py`)

The harness asks the same engineering questions with and without the plugin and scores the numbers in the
answers plus whether an eng-toolkit tool was actually called. Findings so far:

- Textbook questions (cantilever, Euler column, Mohr's circle) are answered correctly by hand with or without
  the plugin; the tools add reproducibility, not accuracy, there.
- Without the plugin Haiku called an eng-toolkit tool in 3 of 14 runs of questions that need numerical work
  (portal frame, plate, oblique shock, channel profile, pump point); with the plugin (skills + hook) in 13 of 14,
  and its answers matched the verified reference numbers in 34 of 36 checks against 27 of 36.
- The skills alone did not change that behaviour (the model rarely loads a skill for a "simple" question);
  the prompt hook does.

- The five workflow skills for fatigue, motor and drive sizing, optimization, plotting and 3D views
  (`scripts/eval_tasks_flows.py`, 15 tasks whose expected numbers come from the real tools plus an independent
  calculation): Haiku without the plugin called an eng-toolkit tool in 6 of 15 runs and matched 64 of 85 checks;
  with the plugin it called a tool in 15 of 15 and matched 85 of 85. The tasks that fail without the plugin are the
  ones with no short closed form: S-N and crack-growth life, a drive duty cycle, an induction motor load point,
  a field-weakening envelope, a nonlinear fit with confidence intervals.

Run it yourself: `python scripts/eval_plugin.py --mcp-config mcp.json --plugin-dir plugin --model haiku --repeat 2`.

## License

The plugin (everything under `plugin/`) is licensed under MIT, see [LICENSE](LICENSE). The MCP server and the rest of
the repository are licensed under Apache-2.0.
