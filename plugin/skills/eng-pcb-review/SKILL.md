---
name: eng-pcb-review
description: Use FIRST for "is this board ready for fab", "which KiCad file is the board", DRC or ERC results, gerbers and drill files (pcb_info, pcb_drc, pcb_erc, pcb_export). Real findings vs library noise, fab rules vs DRC rules, yes/no checklist.
---

# KiCad PCB review workflow (eng-toolkit)

Load `eng-toolkit` too. The tools read KiCad 5 to 10 files; `pcb_drc`, `pcb_erc` and `pcb_export` need `kicad-cli` (run `toolkit_doctor`). A clean DRC is **not** proof the board is manufacturable or correct: all results are unreviewed.

## 1. Pin down the problem (one short list)

- **Which file is which.** `.kicad_pcb` = the board (`pcb_info`, `pcb_drc`, `pcb_export`). `.kicad_sch` = one schematic sheet (`pcb_erc`; sub-sheets in other files are not checked). `.kicad_pro` = project settings (rules, severities, read automatically from next to the file). `.kicad_sym` / `.pretty` / `fp-lib-table` = libraries, not checkable. Keep the board, `.kicad_pro` and `.kicad_dru` together in one folder.
- **Whose board is it?** Files inside KiCad's `share/kicad/demos` are KiCad's own samples: say so if the user thinks they are a colleague's board.
- **The fab's capability sheet**: min track width and clearance, min drill, annular ring, via size, board size, layer count, finish, board thickness. **Ask for it before any "can I send this" verdict**; if the user has none, give measured values only and say the verdict is open.

## 2. Tools

| Question | Tool |
|---|---|
| Layers, outline size, parts, nets, track widths, vias, drills, thickness | `pcb_info` (own parser, no KiCad needed; `max_listed` to list parts) |
| Design rule violations, missing connections | `pcb_drc` |
| Schematic electrical errors | `pcb_erc` |
| Fabrication files | `pcb_export` `gerbers` (zip plus job file), `drill`, `pos`, `pdf` / `svg` of chosen layers |

## 3. Inputs that work (verified on KiCad's pic_programmer demo, 2 copper layers, 63 parts, 111 nets, 160.0 x 99.1 mm)

- `pcb_info board_path=".../pic_programmer.kicad_pcb" max_listed=0` reads `copper_layers`, `board_width`, `board_height` (Edge.Cuts centre lines), `data.tracks.width_mm` (min 0.35), `data.vias.drill_mm` (min 0.6) and `size_mm` (1.6), `data.board.thickness_mm` (1.6). Use these, not memory or a colleague's chat message, for layer count and outline.
- `pcb_drc board_path=... min_clearance="0.2 mm" min_track_width="0.25 mm" max_violations=20` gives 0 errors, 0 unconnected and 63 warnings, all library warnings (`data.violations.library_warnings.count` 63, `design_warnings` 0).
- `pcb_erc schematic_path=".../pic_programmer.kicad_sch"` gives 4 errors (`hier_label_mismatch`, design) and 168 warnings, of which 164 are library warnings; the 4 errors are real findings next to a clean DRC. Sub-sheets in other files are not checked.
- `pcb_export board_path=... export="gerbers"` (10 files, `gerbers.zip`), then separately `export="drill"` (`board.drl`). Layers are checked against the board (`In1.Cu` on a 2-layer board is refused, with the list).

## 4. Read the results

1. **Real findings versus noise.** The tool separates them: `data.violations.library_warnings` (count and `by_type`: `lib_footprint_issues`, `lib_symbol_issues`, `footprint_link_issues`, ...) means the libraries were not visible to the run and says nothing about the board; `data.violations.design_warnings` counts the other warnings; every listed row has `group` `design` or `library`, library rows last; `values.library_warning_count` is the same count. `warning_count` and `violation_count` still include the library ones, so quote `design_warnings` and the errors by type with positions.
2. **Capped counts.** kicad-cli stops listing at 499 for clearance (199 for track width and similar: not verified here). The tool flags such a count itself: `data.violations.by_type[].at_kicad_limit` true, the list `data.violations.at_kicad_limit` and a warning say the true count is **at least** that (the same 499 appears at 0.2 mm or 1 mm clearance). Quote it as "at least", give no per-threshold sensitivity from it, and treat the type as a systemic problem. ERC counts are not flagged (limits unknown).
3. **What DRC checked.** Read `data.rule_set`: source (KiCad defaults or the `.kicad_pro` / `.kicad_dru` next to the board), the numbers, the `checks_disabled` list, `overrides_mm` from your arguments. Fab limits are board setup values in KiCad: only `min_clearance` and `min_track_width` can be passed in. Everything else (drill, annular ring, via size, hole to hole) is checked against the project's own numbers, so compare the fab sheet with the measured values from `pcb_info` **by hand** (annular ring = (via size - drill) / 2) and say it was by hand.
4. `unconnected_count 0` means every net in the file is routed on the layers reported. `refill_zones` false uses stored fills (they may be stale): refill with `refill_zones=true` before a verdict if zones matter.
5. Run `pcb_erc` before a verdict when the schematic is given, and report its errors next to the DRC.

## 5. Coverage limits (outside the toolkit: say so)

No 3D render, STEP or PNG export (use `pdf`: board true size on one page, layers overlaid; SVG may not open in common viewers), no signal integrity, impedance, thermal, EMC, BOM or assembly review, no stackup or panel check, no fab-specific CAM. Outputs land in the toolkit workspace (`artifacts` paths), files are named `board-*` and cannot be placed in a folder of the user's choosing: give the paths.

## 6. Report

**A fab verdict is a yes or no against the fab's WRITTEN rules.** Checklist table: fab rule, fab value, measured or checked value, margin (measured minus fab minimum), how (DRC or by hand), pass or fail. A fab minimum is inclusive unless the sheet says otherwise: a measured value equal to the limit passes (margin 0); say so plainly with one sentence of risk, not a hedge. Example sheet: min track 0.35 mm, board at most 150 x 100 mm: track 0.35 passes with margin 0, board 160.0 x 99.1 fails the first row. Then "ready" or "not ready" naming each failing row; "ready with caveats" only when the caveats are listed and none is a failing row. Add: files used, layer count and outline from `pcb_info`, DRC and ERC counts (`at_kicad_limit` ones labelled "at least"), library warnings listed as noise, export file list with paths. Limits: unreviewed rule check; a qualified engineer and the fab's own CAM review come before ordering.
