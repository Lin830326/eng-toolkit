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
| skill `eng-toolkit` | unit rules, the ask-first rule (no invented datasheet values or verdicts on missing inputs), where numbers may come from, reading a tool's self-reporting, coverage limits, the tool map, the report format |
| skill `eng-structural-check` + `/eng-structural-check` | beam, frame, truss, column, plate, vibration checks: model choice, inputs, hand check, convergence, utilization |
| skill `eng-dfm-review` + `/eng-dfm-review` | CNC milling and sheet-metal review of a STEP/IGES/BREP file |
| skill `eng-fatigue-check` + `/eng-fatigue-check` | infinite-life check with Marin factors and mean stress, rainflow + Miner damage, notches, strain-life, crack growth |
| skill `eng-motor-drive-sizing` + `/eng-motor-drive-sizing` | motor and gearbox sizing for a motion profile, induction and PM motor operating points, converters, transformers, faults |
| skill `eng-optimize` + `/eng-optimize` | minimise, linear programs, root finding, curve fitting with intervals, uncertainty propagation, design of experiments |
| skill `eng-plot-results` + `/eng-plot-results` | charts (PNG or SVG) from other tools' CSV artifacts or inline series: Bode, S-N, histograms, station diagrams |
| skill `eng-3d-view` + `/eng-3d-view` | a PNG picture (to paste into a mail) and a coloured GLB of a part, DFM findings, named face groups or an FEM stress/displacement result |
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
   /eng-dfm-review C:\parts\bracket.step cnc_milling tool radius 3 mm, min wall 1.5 mm
   /eng-fatigue-check 30 mm machined shaft, Su 690 MPa, bending 110 MPa amplitude with 60 MPa mean: infinite life?
   /eng-motor-drive-sizing 10:1 reducer, 0.8 kg*m^2 load, 12 rad/s in 0.6 s, hold 1.8 s: peak and RMS motor torque
   /eng-optimize fit T = Tinf + A*exp(-t/tau) to these cooling data with a 95 % interval on tau
   /eng-plot-results Bode plot of the filter just designed
   /eng-3d-view C:\parts\bracket.step colour by DFM findings
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
