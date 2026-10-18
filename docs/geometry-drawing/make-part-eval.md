# geometry_make_part: small-model evaluation and what it changed

This is the slice S3 design record. The design (docs/geometry-drawing/design.md, decision 1) chose a declarative part spec over server-side code execution, mainly for safety. It also assumed a small model fills the spec at least as reliably as it writes build123d. The cross-judge flagged that nobody had measured this. This page records the measurement, the one grammar change it caused, and its limits.

## Question and baseline

Can a small model produce a correct part from a plain description, in one pass, using only (a) the generated tool description of `geometry_make_part`, or (b) its own knowledge of build123d?

**Model.** Claude Haiku. The project owner decided that Haiku is the small-model baseline; a local 7B to 8B model was not tested (none is installed on the machine, and the owner did not ask for it).

## Protocol

* 13 basic tasks (T01 to T13) and 5 stretch tasks (S01 to S05), described in plain English with explicit axes (`tests/eval_data/tasks.py`, `tasks_stretch.py`).
* The model writes each answer once, without running anything and without reading any other file. The spec arm reads only `tests/eval_data/tool_as_seen.json`, which is exactly what an MCP client shows (description and parameters of the tool). The build123d arm gets only the task text and "build123d 0.13".
* Scoring is done by a trusted script, never by the model: the spec goes through the real `geometry_make_part`; the written STEP is measured again with `geometry_measure` (a second code path); the build123d scripts are run in a scratch environment and measured with build123d itself.
* A task passes when the relative volume error is at most 1e-4, the bounding-box size is within 1e-3 mm in each axis (axes in order) and the result is one solid.
* Ground truth is closed-form, independent of any CAD kernel:

| id | part | volume (mm^3) | size |
| --- | --- | --- | --- |
| T01 | block 80x40x10 | 80*40*10 = 32000 | 80,40,10 |
| T02 | block 60x30x15, d8 through hole | 27000 - pi*4^2*15 = 26246.0178 | 60,30,15 |
| T03 | plate 100x60x8, four d6 holes | 48000 - 4*pi*3^2*8 = 47095.2213 | 100,60,8 |
| T04 | cylinder d30 h40 | pi*15^2*40 = 28274.3339 | 30,30,40 |
| T05 | tube OD40 ID30 L100 | pi*(20^2-15^2)*100 = 54977.8714 | 40,40,100 |
| T06 | block 50x50x20, R3 on vertical edges | 50000 - 4*(9 - 9*pi/4)*20 = 49845.4867 | 50,50,20 |
| T07 | L-bracket | 60*40*5 + 5*40*25 = 17000 | 60,40,30 |
| T08 | flange d80x10, d20 centre, six d6 on a d60 circle | pi*(1600*10 - 100*10 - 6*9*10) = 45427.4298 | 80,80,10 |
| T09 | stepped shaft d20x30 + d12x40 | pi*100*30 + pi*36*40 = 13948.6714 | 20,20,70 |
| T10 | block 40x40x30, blind d10 depth 15 | 48000 - pi*25*15 = 46821.9028 | 40,40,30 |
| T11 | block 100x50x20, pocket 30x20x8 | 100000 - 4800 = 95200 | 100,50,20 |
| T12 | block 60x40x20, 2 mm chamfer on 4 vertical edges | 48000 - 4*(2*2/2)*20 = 47840 | 60,40,20 |
| T13 | washer r8..r14 h3 (revolve) | pi*(14^2-8^2)*3 = 1244.0707 | 28,28,3 |
| S01 | sphere d30 | 4/3*pi*15^3 = 14137.1669 | 30,30,30 |
| S02 | open-top box 60x40x30, 3 mm walls and bottom | 72000 - 54*34*27 = 22428 | 60,40,30 |
| S03 | frustum d40 to d20, h30 | pi*30/3*(400+200+100) = 21991.1486 | 40,40,30 |
| S04 | plate 100x40x10, d6 hole with d12 90-degree countersink | 40000 - pi*9*10 - (pi*3/3*(36+18+9) - pi*9*3) = 39604.1593 | 100,40,10 |
| S05 | bar 100x20x8, five d6 holes at 15 mm pitch | 16000 - 5*pi*9*8 = 14869.0266 | 100,20,8 |

## Results

| arm | basic 13 | stretch 5 |
| --- | --- | --- |
| build123d (Haiku, own knowledge) | 13/13 | 5/5 |
| spec language v1 (Haiku, tool description) | 13/13 | 4/5 |
| spec language v1.1 (sphere and cone added) | 13/13 | 5/5 |

* The one failure was S01, a sphere. v1 had no sphere and no arc in a profile; the model approximated it with a polygon revolve (1.7 percent volume error). S02 (open box), S03 (frustum by polygon revolve), S04 (countersink) and S05 (explicit hole list) were expressible and passed.
* Change made: two body ops, `sphere` and `cone` (the cone takes `top_diameter`, 0 mm for a full cone). The tool description grew from 3456 to 3737 characters (limit 3900); the schema is 1009 characters.
* After the change all 18 were rerun with the new description: 18/18, volume errors at most 3.3e-12 relative.

## Reading the numbers honestly

* Both arms reach the ceiling on these tasks, so they do not discriminate. The evaluation does not show that a spec is easier than build123d for Haiku. The choice of a spec over executing code rests on safety (a Job Object stops neither network access nor file writes; a code tool bypasses the client's per-command permission prompt), on units carried through to the kernel, on field-level error pointers and on reproducible provenance, not on accuracy.
* n = 18 per arm, one model, one pass, one sample per task. No variance estimate exists.
* S01 was tuned on: the grammar change was made because of it, and the same task was then rerun. That rerun confirms the fix, it does not estimate generalisation. The other 17 tasks were not used for tuning.
* Haiku is the baseline by the owner's decision. A weaker local model could behave differently.
* The tasks are simple prismatic and turned parts. Anything needing shells, patterns, mirrors, lofts, sweeps, arc profiles or threads is outside what v1.1 can express (known gaps, round 2 of the spec language); build123d can express them, which is the price of not executing code.

## What stays pinned in the repository

* `tests/eval_data/specs/*.json`: the 18 specs exactly as the model wrote them. `tests/test_make_part_eval_specs.py` rebuilds each and checks it against the closed-form volume and size, so a later grammar change cannot silently break specs that a model has already written.
* `tests/eval_data/tool_as_seen.json`: the description the model saw. The same test file fails when `MAKE_PART_DESCRIPTION` changes, which is the signal to repeat the evaluation.
* `tests/eval_data/build123d/*.py`: the baseline scripts the model wrote (not executed by the tests, build123d is not a dependency).

## How to repeat it

1. Regenerate `tool_as_seen.json` from the gateway (list the tools with `fastmcp.Client` and keep the description and the parameter descriptions of `geometry_make_part`).
2. Give a fresh Haiku run only that file and the task texts from `tasks.py` and `tasks_stretch.py`; require one pass, no execution, one JSON file per task.
3. Run each file through `geometry_make_part`, measure the written STEP with `geometry_measure`, and compare with the closed-form values above.
4. Replace the committed specs and `tool_as_seen.json` with the new run and update this page.
