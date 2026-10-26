---
name: eng-digital-synthesis
description: Use to lint Verilog, get gate counts and decide whether two Verilog designs are equivalent with Yosys (digital_check, digital_synthesize, digital_equivalent). Covers unit-gate counts versus um2, bounded versus complete proofs, wide counters, long workspace paths.
---

# Digital design workflow (eng-toolkit)

Load `eng-toolkit` first. Yosys is a separate program (`toolkit_doctor`). All results are unreviewed. Give Verilog as `source` text (all modules in one text, then `top`) or `files`; `` `include `` is refused.

## 1. Pin down the problem

Which module is the top; reset style and initial values; the exact claim to test ("same outputs for all inputs and all time", "only from reset"); the number wanted (gate count, levels, area in um2) and for what (slide, decision). **Area in um2 needs the company's liberty file, which the toolkit does not read**: say it cannot be given and offer relative unit-gate numbers.

## 2. Tool choice

| Question | Tool |
|---|---|
| Syntax, undriven wires, latches, loops, width mismatches | `digital_check` (call succeeds even with errors: read `data.findings`, `values.errors`) |
| Cells, wires, levels, unit-gate area | `digital_synthesize` |
| Same behaviour of two designs | `digital_equivalent` |
| Bar chart of results | `plot_bar` with inline series (skill `eng-plot-results`); never hand-written charts |

## 3. Inputs that work (verified)

All with `source=<text of modules>`:

- `digital_check` of the counter pair below: 0 errors, 0 warnings.
- `digital_synthesize top="cnt8" target="structural"` gives 40 cells, unit-gate area 79, 6 levels (follows the source exactly); `target="optimized"` gives 24 cells, area 63, 8 levels (ABC: depends on the Yosys version). Other targets: `abc_simple`, `abc_gates`, `abc_aig`, `abc_cmos2`, `abc_nand`. **Always say target and metric** ("unit-gate, no liberty file"). The area model is NOT/AND/OR/NAND/NOR 1, XOR/XNOR 2, MUX 3, flip-flop 5.
- Adders: `add16_beh` (`a + b + cin`) 113 cells / 145 structural, 86 / 112 optimised; the hand-written ripple adder 80 / 112 either way, so the ripple version is not smaller after optimisation. Prove equal first: `digital_equivalent top_a="add16_beh" top_b="add16_rca" cycles=0` gives `equivalent`, proof complete.
- Two tops in one text: `source_a=<all modules> top_a="cnt8" top_b="cnt8_opt"` (leave `source_b` empty).

```verilog
module cnt8 (input clk, input rst, input en, output reg [7:0] count, output tc);
  assign tc = en & (count == 8'hFF);
  always @(posedge clk) begin if (rst) count <= 8'h00; else if (en) count <= count + 8'h01; end
endmodule
module cnt8_opt (input clk, input rst, input en, output reg [7:0] count, output tc);
  assign tc = en & (&count[6:0]);
  always @(posedge clk) begin if (rst) count <= 8'h00; else if (en) count <= count + 8'h01; end
endmodule
```

## 4. Equivalence: what a verdict means

- `cycles=0` is a complete proof over all inputs but only for designs **without** flip-flops, latches or memories.
- `cycles=N` (1 to 100) is **bounded**: `no_mismatch_within_bound` is not a proof, it only says no difference shows up in the first N steps. Never write "equivalent" from it. `not_equivalent` comes with a counterexample trace and is a definite answer (the reported cycle is where the solver found a difference, not necessarily the first).
- **Wide counters**: the bound must exceed the deepest state that matters (an 8-bit counter's flag differs at count 127, which needs 127 enabled edges: `cnt8` against `cnt8_opt` at `cycles=100` says `no_mismatch_within_bound` although they differ). Three routes, in order: (1) split out the combinational part into two small modules and prove them with `cycles=0`: `tc_a = en & (count == 8'hFF)` against `tc_b = en & (&count[6:0])` gives `not_equivalent` at `count=127, en=1`; then argue that 127 is reachable from reset by counting up (it is: 127 enabled clocks). (2) Repeat the check on a reduced-width copy (4 bits, compare on `count[2:0]`) with `cycles=40`: `not_equivalent` with an input trace. (3) A full-width sequential proof (induction) is not available: say so. The colleague's claim "the top bit is redundant" is wrong whenever `count` can be 127 with `en=1`, which is always after reset.
- Both designs must have the same ports. Initial values: flip-flops without an initial value start at 0 in the bounded check; the real chip may not.

## 5. Failures and limits

- **Long workspace path**: optimised ABC synthesis fails (error naming a temp file `Opening ... for writing failed`, or `yosys could not be started`) when the job folder path is very long on Windows. Set a short `ENG_TOOLKIT_WS` (for example `C:\etk`) for the server and retry; the numbers are unchanged.
- Not covered: timing closure, power, technology mapping to a vendor library, formal property proofs (assertions), clock-domain checks, simulation of a testbench, `include`/file I/O in Verilog. Say "outside the toolkit" and suggest the vendor flow.

## 6. Report

Result (counts with target and metric named, verdict with its kind: proved, counterexample, bounded only), inputs and assumptions (top, reset, bound, unit-gate model), checks (digital_check clean, hand reasoning for why a counterexample is reachable), limits (no um2, bounded not proof, unreviewed). When giving a counterexample to a colleague, list the input values and the two outputs.
