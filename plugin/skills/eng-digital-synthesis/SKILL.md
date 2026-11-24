---
name: eng-digital-synthesis
description: Use FIRST for "how many gates is this Verilog", area, "is my optimised version equivalent", lint (Yosys, digital_* tools). Unit-gate counts versus um2, bounded versus complete proofs, wide counters.
---

# Digital design workflow (eng-toolkit)

Load `eng-toolkit` too. Yosys is a separate program (`toolkit_doctor`). All results are unreviewed. Give Verilog as `source` text (all modules in one text, then `top`) or `files`; `` `include `` is refused.

## 1. Pin down the problem

Which module is the top; reset style and initial values; the exact claim to test ("same outputs for all inputs and all time", "only from reset"); the number wanted (gate count, levels, area in um2) and for what (slide, decision). **Area in um2 needs the company's liberty file, which the toolkit does not read**: say it cannot be given and offer relative unit-gate numbers.

## 2. Tool choice

| Question | Tool |
|---|---|
| Syntax, undriven wires, latches, loops, width mismatches | `digital_check` (call succeeds even with errors: read `data.findings`, `values.errors`) |
| Cells, wires, levels, unit-gate area | `digital_synthesize` |
| Same behaviour of two designs | `digital_equivalent` |
| Bar chart of results | `plot_bar` with inline series (PNG by default, give `data.png_path`; skill `eng-plot-results`); never hand-written charts |

## 3. Inputs that work (verified)

All with `source=<text of modules>`:

- `digital_check` of the module set below: 0 errors, 0 warnings.
- `digital_synthesize top="wrap6" target="structural"` gives 29 cells, unit-gate area 58, 6 levels (follows the source exactly); `target="optimized"` gives 18 cells, area 47, 6 levels (ABC: depends on the Yosys version). Other targets: `abc_simple`, `abc_gates`, `abc_aig`, `abc_cmos2`, `abc_nand`. **Always say target and metric** ("unit-gate, no liberty file"). The area model is NOT/AND/OR/NAND/NOR 1, XOR/XNOR 2, MUX 3, flip-flop 5.
- Adders (12 bit): `sum12_beh` (`a + b + cin`) 81 cells, area 105 structural and 64 cells, area 84 optimised; the hand-written ripple adder 60 cells, area 84 either way: after optimisation the ripple version is not smaller in area. Prove equal first: `digital_equivalent top_a="sum12_beh" top_b="sum12_rca" cycles=0` gives `equivalent`, proof complete.
- Two tops in one text: `source_a=<all modules> top_a="wrap6" top_b="wrap6_lean"` (leave `source_b` empty).

```verilog
module wrap6 (input clk, input rst, input en, output reg [5:0] count, output tc);
  assign tc = en & (count == 6'h3F);
  always @(posedge clk) begin if (rst) count <= 6'h00; else if (en) count <= count + 6'h01; end
endmodule
module wrap6_lean (input clk, input rst, input en, output reg [5:0] count, output tc);
  assign tc = en & (&count[4:0]);
  always @(posedge clk) begin if (rst) count <= 6'h00; else if (en) count <= count + 6'h01; end
endmodule
```

## 4. Equivalence: what a verdict means

- `cycles=0` is a complete proof over all inputs but only for designs **without** flip-flops, latches or memories.
- `cycles=N` (1 to 100) is **bounded**: `no_mismatch_within_bound` is not a proof, it only says no difference shows up in the first N steps. Never write "equivalent" from it. `not_equivalent` comes with a counterexample trace and is a definite answer (the reported cycle is where the solver found a difference, not necessarily the first).
- **Wide counters**: the bound must exceed the deepest state that matters (the flag of `wrap6_lean` differs at count 31, which needs 31 enabled edges: `cycles=20` says `no_mismatch_within_bound` although the designs differ; `cycles=40` finds it at cycle 34). Three routes, in order: (1) split out the combinational part into two small modules and prove them with `cycles=0`: `tc_a = en & (count == 6'h3F)` against `tc_b = en & (&count[4:0])` gives `not_equivalent` at `count=31, en=1`; then argue that 31 is reachable from reset by counting up. (2) Repeat the sequential check with enough cycles or on a reduced-width copy. (3) A full-width sequential proof (induction) is not available: say so. A claim such as "the top bit is redundant" is wrong whenever the counter can reach the state where only that bit differs, which is always after reset. When giving the counterexample to a colleague, list the input sequence and the two outputs.
- Both designs must have the same ports. Initial values: flip-flops without an initial value start at 0 in the bounded check; the real chip may not.

## 5. Failures and limits

- **Workspace path**: no special `ENG_TOOLKIT_WS` is needed. Yosys and ABC run in a short temporary folder of their own and a relative `ENG_TOOLKIT_WS` is made absolute; optimised synthesis gave the same cells and area with a 200-character workspace path. If a start error (`yosys could not be started`) still appears, report it as it is.
- Not covered: timing closure, power, technology mapping to a vendor library, formal property proofs (assertions), clock-domain checks, simulation of a testbench, `include`/file I/O in Verilog. Say "outside the toolkit" and suggest the vendor flow.

## 6. Report

Result (counts with target and metric named, verdict with its kind: proved, counterexample, bounded only), inputs and assumptions (top, reset, bound, unit-gate model), checks (digital_check clean, hand reasoning for why a counterexample is reachable), limits (no um2, bounded not proof, unreviewed). 
