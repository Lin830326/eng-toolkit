# Fixtures of the pcb tests

Small boards and schematics, all written by hand as text. Nothing here was exported from a real design, and nothing comes from any
other project.

| File | What it is | How it was made |
|---|---|---|
| `src_v7/*.kicad_pcb` | the boards in the KiCad 7 file syntax (format `20221018`: nets declared as `(net 1 "SIG")` and referred to by number, `fp_text`, no uuids) | `make/make_boards_v7.py <outdir> make/board_header_v7.txt` (a Python script that only joins text) |
| `clean`, `clearance`, `unconnected`, `narrow`, `no_outline` `.kicad_pcb` | the same boards in the current format (`20260206`, KiCad 10.0.6) | each `src_v7` file copied and brought up with `kicad-cli pcb upgrade <file>`; the resulting `.kicad_prl` was deleted |
| `src_v8/*.kicad_sch`, `erc_ok`, `erc_open` `.kicad_sch` | the schematics in the KiCad 8 syntax and in the current format | `make/make_schematics_v8.py <outdir>`, then `kicad-cli sch upgrade <file>` |
| `features.kicad_pcb` | a board in the old syntax with one of every kind of element the reader knows (4 copper layers, an arc on Edge.Cuts, a rotated footprint with a circle on Edge.Cuts, a track arc, blind and micro vias, a zone, a back-side footprint, an oval drill). Never upgraded: it tests the reader on the old format, and KiCad 10.0.6 loads it as it is | by hand (the header from `make/board_header_v7.txt` with two more copper layers) |

`kicad-cli pcb upgrade` exists in KiCad 10.0.6 and works on these files (exit code 0, "Successfully saved board file using the latest
format"). The old syntax is accepted by KiCad without it; the upgrade was done so that the tests check the current format too.

## The numbers the tests expect (all derived by hand in the tests)

`clean.kicad_pcb`: outline (0,0)-(20,20). R1 at (5,15) and R2 at (15,15); their pads 1 at (x,14) on net SIG and pads 2 at (x,16) on
net GND; R2's pad 2 is a through hole (drill 0.6). Tracks 0.25 wide: SIG F.Cu (5,14)-(15,14), GND F.Cu (5,16)-(15,16), GND B.Cu
(10,16)-(15,16). Via at (10,16), 0.8 / 0.4, GND. No rule is broken and no copper end is left free (the via has copper on both layers,
the B.Cu track ends on the via and on the through-hole pad).

* `clearance`: clean + N3 track (5,5)-(15,5) and N4 track (10,5.3)-(10,9), both 0.2 wide. The round end of the N4 track reaches
  y = 5.3 - 0.1 = 5.2, the N3 track's copper reaches y = 5 + 0.1 = 5.1: gap 0.1 mm, against the 0.2 mm of the default net class.
* `unconnected`: clean without the SIG track: pad 1 of R1 and pad 1 of R2 are both on SIG and nothing joins them.
* `narrow`: clean with the GND track on F.Cu 0.1 wide (the minimum is 0.2).
* `no_outline`: clean without the Edge.Cuts rectangle. KiCad reports one `invalid_outline` error for such a board.
* `erc_ok`: two 10k resistors wired pin 1 to pin 1 and pin 2 to pin 2. `erc_open`: the pin 2 to pin 2 wire removed, so two pins have
  nothing on them (R1 pin 2 at (100.33, 83.82), R2 pin 2 at (115.57, 83.82); a pin end lies 3.81 mm from the symbol origin).
