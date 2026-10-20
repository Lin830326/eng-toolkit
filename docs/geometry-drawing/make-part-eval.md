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

## Round 2: the ops added in slice S10

Design record: [make-part-round2.md](make-part-round2.md). The same question as round 1 for the new grammar (pattern, mirror, shell, path profiles with arcs, taper, `of:` selectors): can Claude Haiku, reading only the generated tool description, write a correct spec in one pass?

**Protocol.** Model `claude-haiku-5-5` (the `haiku` alias of `claude -p` on 2026-10-20). Started with `--tools ""`, `--setting-sources ""`, `--disable-slash-commands`, `--no-session-persistence` and a one-sentence system prompt, in an empty directory: it saw `tests/eval_data/tool_as_seen.json` (tool description and parameter descriptions) and the task text, and could run and read nothing. Three independent samples per task. Scoring as in round 1 (the real `geometry_make_part`, the STEP measured again with `geometry_measure`, volume within 1e-4 relative, size within 1e-3 mm per axis, one solid). Runner: `tests/eval_data/run_round2.py`; tasks and closed forms: `tests/eval_data/tasks_round2.py` (the task texts do not name any op). The ground truth was checked first with a reference spec written by hand for each task (`specs_round2_reference/`): all ten match to 1e-9, so the closed forms and the grammar agree.

| id | part | closed form (mm^3) |
| --- | --- | --- |
| R01 | bar 120 x 20 x 8, six d5 through holes in a row at 20 mm pitch | 19200 - 6 pi 2.5^2 8 = 18257.52 |
| R02 | round plate d100 x 6, eight d6 holes on a d70 bolt circle | pi (50^2 6 - 8 3^2 6) = 45766.72 |
| R03 | part symmetric about x = 0: 30 x 40 x 10 half with a d8 hole, plus its mirror image | 2 (12000 - pi 4^2 10) = 22994.69 |
| R04 | cup: cylinder d50 x 40 hollowed, 2.5 mm walls and bottom, top open | pi (25^2 40 - 22.5^2 37.5) = 18898.64 |
| R05 | plate 80 x 50 x 6 with a stadium through slot (30 straight, width 16) | 24000 - (30 16 + pi 8^2) 6 = 19913.63 |
| R06 | D-shaped plate 5 thick, straight edge 30 plus a radius-15 half circle | pi 15^2 / 2 5 = 1767.15 |
| R07 | 30 x 30 square boss 20 high, walls leaning 5 deg inward | 20/3 (A1 + A2 + sqrt(A1 A2)), A2 = (30 - 40 tan 5 deg)^2 |
| R08 | plate 80 x 50 x 10, boss d24 x 14, only the top edge of the boss filleted R3 | 40000 + pi 12^2 14 - 2 pi (r^2 (u0 + r/2) - (pi r^2/4)(u0 + 4r/(3 pi))), r 3, u0 9 (Pappus) |
| R09 | disc d80 x 8 with six spoke slots 20 x 4, each the first turned in 60 deg steps | pi 40^2 8 - 6 20 4 8 = 36372.39 |
| R10 | plate with two d16 x 12 bosses, only the top edge of the one at x = 20 filleted R2 | 40000 + 2 pi 8^2 12 - 2 pi (...) (Pappus, r 2, u0 6) |

R09 and R10 were **added after the first run**: in that run (R01 to R08, description of 5048 characters) Haiku met R01 and R02 mostly with explicit lists of hole positions and R08 with the ordinary selector `>z & circle`, so those runs did not exercise `pattern` or `of:`. R09 (rotated slots cannot be listed without trigonometry) and R10 (two identical rims, only one to round: no ordinary selector separates them) were written so that the new ops are the natural route. They were then run once on the same description before any change.

### Results

| run | description | tasks | samples passed | what the model chose |
| --- | --- | --- | --- | --- |
| 1 | 5048 characters | R01 to R08 | 24 / 24 | `pattern` in 1 of 6 samples of R01 and R02 (the rest listed every hole position; the eight bolt-circle positions were rounded to four decimals, still exact enough for the volume); `mirror` 3/3, `shell` 3/3, path profile 6/6 (R05 and R06; the stadium slot was cut with an extruded path, not the existing `slot`), taper 3/3, `of:` 0/3 in R08 |
| 2 | 5048 characters | R09, R10 | 5 / 6 | R09: no `pattern` (computed six rotated polygons by hand; sample 3 failed, 0.44 % volume error from rounded coordinates); R10: `of:` 3/3 |
| 3 | 5108 characters (final) | R01 to R10 | 29 / 30 | `pattern` R02 3/3, R09 1/3, R01 0/3; `mirror`, `shell`, path, taper 3/3 each; `of:` R10 3/3; the one failure is R09 sample 1 (0.88 % off, hand-rounded coordinates of the rotated slots) |

### The one grammar change, and what it was tuned on

After run 2 the summary of `pattern` was changed from "copy an earlier body or hole feature to count instances in all" to "repeat an earlier body or hole feature (rather than computing copies or rotated outlines by hand) to count instances in all", 60 characters, and all ten tasks were run again (run 3). `pattern` use rose from 1/3 to 3/3 on R02 and from 0/3 to 1/3 on R09; R01 stayed at 0/3. The change was made because of R09 and then judged on R09, so it is tuned on that task: it shows the wording helps, not that it generalises. No second example was added.

### Reading the numbers honestly

* 29 of 30 and 24 of 24 say the new grammar is writable by Haiku in one pass; they do not show that it is *used*: for plain row and bolt-circle holes the model prefers a list of entry points, which is valid and exact, and for a simple `>z & circle` fillet it uses the ordinary selector. Where the new op was the only sensible route (R03 mirror, R04 shell, R06 and R05 arcs, R07 taper, R10 `of:`) it used it every time. For `pattern` the use is partial (R09 1 of 3 samples) and the failure of R09 sample 1 is exactly what the op exists to prevent.
* n = 3 samples for each of 10 tasks, one model, one day, no variance estimate beyond that. R09 and R10 were added after seeing run 1; R09's wording change was tuned on it.
* `import` was not evaluated: it needs a file path the model cannot know. It is covered by the hand-calculation tests only, including an inch STEP.
* The pinned specs (`tests/eval_data/specs_round2/`, 30 files exactly as written, run 3) are rebuilt by `tests/test_make_part_eval_specs.py` against the closed forms; the recorded failure `R09_s1` is asserted to stay a failure so that a later change cannot hide it. `tool_as_seen.json` is now the round 2 description; the round 1 description the first 18 specs saw is `tool_as_seen_round1.json`, and the 18 round 1 specs still build.
* Cost of the whole evaluation: 60 Haiku calls, about 0.09 USD in total.

