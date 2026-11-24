# User-acceptance scenarios

A scenario is a realistic piece of engineering work, started by a person who writes the way people write (vague, missing units,
sometimes in Chinese), run against an assistant that has the eng-toolkit plugin and tools. `scripts/uat_run.py` plays the person
with a language model, keeps the transcript and collects every chart, drawing and model the tools produced. A judge then
compares what the person got with an independent answer (`uat/truth/<id>.py`: not our tools; KiCad's own CLI, ngspice, Yosys,
CalculiX, OpenCASCADE, scipy, a textbook formula) and looks at the pictures.

## Two sets: development and hold-out

| Set | Scenarios | Use |
| --- | --- | --- |
| development | the 21 scenarios `C1`..`C4`, `E1`..`E4`, `F1`..`F5`, `M1`..`M4`, `S1`..`S4` | Failures found here are fixed in the tools and skills. The skills and tool descriptions know these problems. |
| hold-out | the 7 scenarios `H1`..`H7` (`"set": "holdout"`) | Never used to tune anything. They cover other parts and other numbers in the same domain groups. A pass here shows the skills work on a problem they were not written for. |

Rule: **skills, tool descriptions, docs and tests must never quote a hold-out scenario's numbers, part names or answers.** Each
hold-out scenario lists its distinctive names and numbers in `leak_tokens`; `tests/test_uat_holdout.py` fails when any of them
appears under `plugin/` or `docs/`. After a hold-out run, fix the tool or skill behaviour in general terms (no worked example
from the scenario), and treat the scenario as spent for tuning: if a fix was made because of it, say so and rely on a new
hold-out scenario for the next round.

| Hold-out | Domain | Core test |
| --- | --- | --- |
| H1 `nano_carrier_fab` | pcb | KiCad Arduino_Nano template: 0 DRC errors yet unrouted (2 missing connections); 0.2 mm rule, layers, outline, gerber and drill |
| H2 `irfp4568_diecast_box` | thermal, plot | TO-247 MOSFET on a bare die-cast box: surface emissivity decides the junction temperature; a bar chart |
| H3 `runway_beam_and_canopy_truss` | structures | crane runway beam with a wheel pair (worst position is not the centred one, deflection governs); monopitch truss with wind normal to the roof |
| H4 `countershaft_vbelt_spur` | machine, mech | pulley overhang (F1 + F2, not F1 - F2), two planes of bending, keyway and shoulder fatigue, bearing life |
| H5 `fan_motor_bearing_envelope` | dsp, plot | 25.6 kHz accelerometer record: the bearing fault is visible only in the envelope spectrum; defect frequencies from the geometry |
| H6 `indexing_table_servo` | electric, plot | 6-station indexing table: cycle and dwell holding torque decide RMS; '50 per minute' is index moves, not rpm |
| H7 `stonecold_hs132_cnc` | geometry, dfm | real KiCad STEP heatsink: the stated spindle axis flips the DFM verdict; mass, stock size, coloured picture |

## Scenario file (`uat/scenarios/<id>.json`)

```json
{
  "id": "P1_uno_board_review",
  "domains": ["pcb"],
  "persona": "who they are and how they write",
  "goal": "what they want to walk away with",
  "opening": "first message, may use {board} placeholders filled from files",
  "facts": {"anything they know but only tell when asked": "..."},
  "files": {"board": "C:/path/to/real/file or uat/data/relative/file"},
  "max_turns": 4,
  "truth": "uat/truth/P1_uno_board_review.py",
  "expect_picture": true,
  "tool_check": "what calling the tools in-process gave (the solvability check)",
  "set": "holdout",
  "leak_tokens": ["names and numbers no skill may quote (hold-out only)"]
}
```

A `files` path relative to the repository (for example `uat/data/H5_fan_motor_accel_25k6.csv`) is copied to the assistant's inbox;
generated data sets have their generator next to them (`uat/data/make_*.py`, deterministic seed).

`truth` scripts print one JSON object: `{"answers": {...named numbers/facts with units...}, "how": "what produced them", "tolerance": "how close counts as right"}`.
They never import eng-toolkit.

## Running

```
python scripts/uat_run.py --only H1,H3 --tag r3-holdout --out <dir> [--assistant haiku] [--user sonnet] [--workers 3] [--no-plugin]
python scripts/uat_run.py uat/scenarios/E2_ce_amplifier_bode.json --out <dir>
```

* `--only` takes scenario ids or id prefixes (`H1`, `E2_ce_amplifier_bode`); without scenario arguments it selects from `uat/scenarios/*.json`.
* `--tag` is printed in the log and written to `transcript.json` and `transcript.md`.
* Each run directory holds `transcript.md`, `transcript.json` (messages, tool calls, artifacts, `skill_summary`), `ws/` and `rendered/`.
* `rendered/` is what the simulated person could open: the tools' own PNG files (copied, named `<jobdir>_<file>.png`), plus a PNG
  of every SVG, PDF page (3 at most) and GLB that has no PNG of its own. The person is told it sees pictures only through that list;
  a bare file path in the assistant's reply is something it cannot open.
* SVG to PNG uses `resvg-py` (not a project dependency; install it only for this runner). A Windows CJK font (`msjh.ttc`, `kaiu.ttf`,
  `mingliu.ttc`) is passed to it so Chinese text is not garbled. GLB rendering needs matplotlib in the python named by
  `UAT_RENDER_PYTHON`, in the runner's python, or on `PATH`.
* `skill_summary` records which `Skill` calls happened, the first tool call, and whether it came before any skill load.
