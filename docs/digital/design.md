# The `digital` domain: Verilog synthesis, lint and equivalence checking with Yosys

Code: `eng_toolkit/domains/digital/` (`tools.py` the three tools, `guard.py` the source gatekeeper, `flow.py` scripts, the limited run and the log reader, `lint.py`, `equiv.py`, `metrics.py`, `engines.py` registers `EXTERNAL_TOOLS = (YOSYS,)`). Tests: `tests/test_digital_*.py`, including direct and gateway Unicode validation in `test_digital_unicode.py`; real-Yosys tests skip when Yosys is absent. The program is Yosys (ISC licence), started only as a separate process through `eng_toolkit.external` (see `docs/external/design.md`); nothing is imported, linked or bundled.

Checked on Yosys 0.70+157 (OSS CAD Suite) on Windows 11. Nothing was run on Linux or macOS and no other Yosys version was tried.

## The three tools

All take Verilog as text (`source`) or as files (`files`: `.v`/`.sv`, up to 20, separated by `;` or line breaks, or a JSON list; read through `JobHandle.input_file`), `language` (`verilog` default, or `systemverilog` = `read_verilog -sv`, Yosys's subset) and `timeout_seconds` (1 to 120, default 30). Every result follows the one contract, `review_status: unreviewed`, and every count is a plain number (no NaN or infinity can occur: tested through `fastmcp.Client`).

### `digital_synthesize(top, source|files, target, language, timeout_seconds)`

`read_verilog`, `hierarchy -check -top`, `proc`, `flatten -noscopeinfo`, then by `target`:

| `target` | script after the flatten | meaning |
|---|---|---|
| `structural` | `synth -top T -noabc` | no optimiser: each operator expanded to simple gates; the count follows the source and can be derived by hand |
| `optimized` (default) | `synth -top T` | Yosys's default flow, ending with ABC's fast script |
| `abc_simple` `abc_gates` `abc_aig` `abc_cmos2` `abc_nand` | `synth -top T -noabc`, `abc -g simple\|gates\|aig\|cmos2\|NAND`, `opt_clean` | ABC maps to a generic library: AND OR XOR MUX; AND NAND OR NOR XOR XNOR ANDNOT ORNOT; AND NAND OR NOR ANDNOT ORNOT; NAND NOR NOT; NAND NOT |

then `stat` (text and `-json`), `ltp -noff`, `write_verilog -noattr`, `write_json`.

Result: `value` and `values.cells` (total cells), `wires`, `wire_bits`, `area_unit_gates`, `levels` (absent when there is a combinational loop); `data.cells_by_type`, `data.unrated_cells`, `data.area_model` (the whole table), `data.longest_path` (signal names along the ltp path), `data.public_wires`, `data.ports`, `data.sources`, `data.yosys` (version, seconds, limits), `data.yosys_warnings`. Artifacts: `netlist.v`, `netlist.json`, `stat.json`, `yosys-script.ys` (the exact script that ran), `yosys-log-tail.txt` (last 20 000 characters of the log, job directory replaced by `<job>`).

* **Cells** are the cells Yosys lists after the flow, minus the ones that are not hardware: `$scopeinfo` (where a flattened module came from), `$print` (a `$display`), `$check`, `$assert` and the other formal cells. Everything else is counted, also a black-box module instance.
* **Wires** are `num_wires` of `stat -json` for the top module: ports and every internal net, nets of the same name merged. A 4-bit AND has 3 wires and 12 wire bits.
* **Levels** is `ltp -noff`: the number of cells on the longest path between a primary input or flip-flop output and a primary output or flip-flop input. A wire from input to output is 0, a register that loads its input is 0.
* **Unit-gate area** is a convention of this toolkit, not a technology number, and it is only for comparing two versions of one design: `$_NOT_ $_AND_ $_NAND_ $_OR_ $_NOR_ $_ANDNOT_ $_ORNOT_` 1, `$_XOR_ $_XNOR_ $_AOI3_ $_OAI3_` 2, `$_AOI4_ $_OAI4_ $_MUX_ $_NMUX_` 3, `$_BUF_` 0, any flip-flop (`$_DFF*`, `$_SDFF*`, `$_ALDFF*`) 5, any latch (`$_DLATCH*`) 3. A cell type not in the table is counted as a cell, listed in `data.unrated_cells`, left out of the area, and a warning says so. The table is part of every result.

### `digital_check(source|files, top, language, timeout_seconds)`

`read_verilog`, `hierarchy -check [-top T]`, `proc -noopt`, `write_json design.json` (for source locations), `check`, then `flatten` and `check` again, only to find combinational loops that run through a module boundary. `proc -noopt` skips the final `opt_expr` pass, which can erase self-inverting wires and inverter rings before `check` sees them. Preserving those cells lets `check` report `combinational-loop` with source lines while genuinely undriven wires still report `undriven-signal`. `ok` is true when the check ran, also when it found errors (`values.errors`, `values.warnings`, `data.findings`, `data.clean`). Yosys stops at the first error it cannot recover from (a syntax error, a missing module), so that is one finding and the checks after it did not run (a warning in the result says so).

A finding is `{rule, severity, message, file, line, lines, module, signal, detail, hint}`. File and line come from the Yosys message, or from the `src` attribute Yosys keeps on every wire and cell. `file` is the file name you gave, or `source` for inline text; the toolkit's own names (`src0.v`) are replaced in every message.

| rule | severity | Yosys says |
|---|---|---|
| `syntax-error` | error | the parser stopped (file and line of the error) |
| `hierarchy-missing-module`, `hierarchy-port-mismatch`, `duplicate-module` | error | `hierarchy -check` |
| `latch-inferred` | warning | `proc_dlatch`: a signal not assigned on every path of a combinational block |
| `combinational-loop` | error | `check` (also after flattening) |
| `multiple-drivers` | error | `check` |
| `undriven-signal` | warning | `check`: a wire used with no driver |
| `implicit-declaration` | warning | the front end: an undeclared name became a 1-bit wire |
| `width-mismatch` | warning | `hierarchy`: a port connection resized (`Resizing cell port`) |
| `yosys-warning`, `yosys-error` | warning, error | anything else Yosys says |

This is what Yosys reports, not a linter. Yosys does not warn about the width of a plain `assign`, nor about unused signals, nor about style. Not covered: anything that needs simulation, clock-domain crossings, reset coverage, X propagation.

### `digital_equivalent(top_a, source_a|files_a, top_b, source_b|files_b, cycles, language, timeout_seconds)`

Each design is read into its own Yosys design, `hierarchy -check -top`, `proc`, `flatten`, `memory`, `opt_clean`, `async2sync`, `rename -top gold` (or `gate`), `design -stash`; the two are copied into one design, `miter -equiv -flatten -make_outputs gold gate miter`, then `sat -verify -prove trigger 0 -show-inputs -show-outputs` with `-seq N -set-init-zero` when `cycles = N > 0`. The fixed names make two designs with the same module names, or a top called `gold`, safe (tested). With `source_b` and `files_b` empty, design B is the module `top_b` of design A's sources (two tops of one source).

* `cycles = 0`: a **complete proof** over all input values. Only for designs without state: if either holds a flip-flop, latch or memory the call is refused (`input.invalid`, `data` names the cell types) because without a time bound the SAT model treats registers as free inputs and a verdict would mean nothing.
* `cycles = N` (1 to 100): a **bounded** check. All inputs are free in each of N time steps; step k (counting from 0) is the state after k clock edges; every flip-flop without an initial value starts at 0. A mismatch within the bound is a real counterexample. **No mismatch within the bound is not a proof**: it is reported as `verdict: no_mismatch_within_bound`, `equivalent: null`, `proof: bounded`, with a warning that says so. A bound of N checks steps 0 to N-1 (a difference at step 6 is found with N = 7, missed with N = 6).
* `verdict` is `equivalent` (proved, only for `cycles = 0`), `not_equivalent` (with `counterexample`) or `no_mismatch_within_bound`. `values.mismatch_found` is 1 or 0 (0 does not mean equivalent when bounded: read `verdict`); bounded checks also give `values.checked_cycles`.
* `counterexample`: `inputs` (integers, the first mismatching step), `gold_outputs`, `gate_outputs` (design A and B), `differing_outputs`, `widths`, `cycle`; bounded checks add `steps` (all steps up to the mismatch) and `first_mismatch_cycle`. Clock inputs appear as ordinary inputs (they read 0: the SAT model steps every flip-flop each time step and does not look at the clock).
* Both designs need the same ports (names and widths): otherwise `digital.interface_mismatch`.
* Asynchronous resets are turned into synchronous behaviour by `async2sync` (the SAT model has no asynchronous flip-flops). Designs with several clocks are not modelled faithfully.

## The security model

Verilog and Yosys are program-adjacent: Yosys's preprocessor opens any file an `` `include `` names and its system tasks read and write files and run programs. The rule: **the caller's text is data and never becomes a Yosys command, a command-line argument or a file name.**

All three tools check every string parameter for UTF-8 encodability before the runner writes or hashes inputs. Lone surrogates return `ok: false` with `input.invalid`, naming the parameter and saying its text is not valid Unicode text without echoing it. These refusals create no job; their provenance hashes an ASCII JSON representation of the rejected inputs. Paths decoded from JSON lists in `files`, `files_a`, and `files_b` get the same validation before file access.

1. The Yosys script is written by the toolkit (`flow.py`, `lint.py`, `equiv.py`, `tools.py`) from fixed lines and values validated first: file names `src<N>.v` chosen by the toolkit, module names that match `[A-Za-z_][A-Za-z0-9_]{0,63}`, `language` and `target` from short lists, integers. The script is stored with the result. A test requires every line of every script to match a fixed pattern (`tests/digital_fixtures.SCRIPT_LINE`) and checks that a marker placed in the Verilog is in no command.
2. The arguments of Yosys are `-q -l <log> -s <script>`, all inside the job directory (`PathLike`, checked by the layer). No option, include path, plugin, `-D`, `exec`, `shell` or `tee -o` reaches Yosys from a caller.
3. Every source passes through `guard.sanitize` before it is written (the sanitised text is what Yosys reads, never the caller's file):
   * size 1 000 000 bytes for the sources of one design (`input.too_large`), UTF-8 only, no NUL or other control character (`input.invalid` with the line), line endings normalised to `\n`;
   * a backslash at the end of a line is refused everywhere (it can make a comment or a macro mean one thing to the checker and another to Yosys);
   * one pass blanks comments and the insides of strings (an unterminated comment or string is refused) and treats escaped identifiers as opaque (refused if they contain a backtick, a quote or a comment marker);
   * a backtick must be followed directly by a name. `` `include `` in any case and spacing is refused; allowed directives are `timescale default_nettype define undef ifdef ifndef else elsif endif resetall celldefine endcelldefine`; any other directive is refused; a macro may be used only if the same file defines it; a `define` with arguments is refused; a body may not contain a backtick, `$`, a quote, a backslash or a comment marker, nor start or end with `/` or `*`, so no expansion can create a directive, a system task, a string or a comment (this closes `` `define A read `` / `` `define B memh `` / `` $`A`B ``, and `` `define X /* `` hiding a following `` `include ``);
   * every `$` must start a system task from the list `$signed $unsigned $clog2 $bits $size $high $low $left $right $dimensions $display $write $finish $stop`; everything else (`$readmemh`, `$readmemb`, `$fopen`, `$fwrite`, `$fdisplay`, `$system`, `$dumpfile`, `$dumpvars`, `$sformat`, `$fscanf`, ...) is refused, as is an allowed name that a macro could extend (`` $display`F ``); a `$` inside an identifier (`a$b`) is fine;
   * non-ASCII characters outside comments and strings are refused; more than 200 `module` keywords is `input.out_of_range`.
   Every refusal is `input.forbidden_construct` (or `input.invalid`) with the construct and the line in the message and at most 80 characters of the caller's line in `details.snippet`. A refused call starts no program: the job directory has no `yosys` folder (tested).
4. Limits through the layer: time (`timeout_seconds`, default 30, at most 120; the process tree is killed), memory 1024 MB (a Windows Job Object; on Linux `prlimit`; none on macOS), 50 MB written, 1 MB printed per stream. A generate loop of 10^9 iterations ends in `engine.timeout`; a 2^30-word memory ends in `engine.resource_limit` (Yosys runs out of memory under the cap) or a timeout; a 2-billion-bit vector is a Yosys error that is reported with the file and line; a 140 000-term expression crashes Yosys's parser (stack overflow) and is reported as such. All tested through the gateway client.
5. The guard is deliberately narrow, not a parser. A valid design that uses a macro with arguments, `` `line ``, `$fatal`, `$error`, non-ASCII identifiers or a macro defined in another file is refused with the name of the feature. It is never silently changed.

A negative control in the tests shows that Yosys really does read an included file when nothing stops it.

## What is exact, and what is not

* **Exact:** the counts for `structural` (derived by hand, below); the counts of the unit-gate area for any given cell counts; the rule that the same input and the same Yosys give the same counts (two runs compared, also the bytes of the netlist, for every target); every refusal; the equivalence verdicts on the cases below; the semantics of `cycles`.
* **Depends on ABC and the Yosys version:** every count that goes through ABC (`optimized` and the `abc_*` targets). Yosys 0.70 maps the full adder to 4 NAND + 1 OR + 1 XNOR (6 cells); another Yosys or ABC may give another number. The tests assert the exact 6 only when the version starts with `0.70` and otherwise that it is not more than the structural 7. `levels` of an optimised design depends on the same thing.
* **Determinism, as observed:** `structural`, `optimized` and every `abc_*` target gave identical cell counts and a byte-identical `netlist.v` on two runs in one install (`tests/test_digital_synthesize.py`). Yosys and ABC are deterministic for the same input and version, but that was not tried across machines or versions.
* **Not a technology result:** the area is in unit gates, the levels count cells and not delays.

## Hand-derived numbers in the tests

| design | derivation | result |
|---|---|---|
| `y = a & b` | one AND | `{$_AND_: 1}`, 3 wires, area 1, 1 level |
| 4-bit `y = a & b` | one AND per bit | `{$_AND_: 4}`, 3 wires / 12 bits, area 4 |
| full adder, `structural` | sum: 2 XOR; cout = (a&b)\|(a&cin)\|(b&cin): 3 AND + 2 OR; 7 cells; area 3 + 2 + 2x2 = 9; wires 5 ports + 5 internal = 10; longest path AND, OR, OR = 3 | `{$_AND_: 3, $_OR_: 2, $_XOR_: 2}` |
| full adder, `optimized` (Yosys 0.70) | ABC's choice | `{$_NAND_: 4, $_OR_: 1, $_XNOR_: 1}`, area 7, 3 levels |
| `y = s ? a : b` | one mux | `{$_MUX_: 1}`, area 3 |
| 4-bit register | 4 flip-flops, no gate | `{$_DFF_P_: 4}`, area 20, 0 levels |
| two instances of an AND, flattened | 2 ANDs in series | `{$_AND_: 2}`, 2 levels |
| mux three ways (`?:`, `if/else`, and/or) | | `equivalent` |
| `a & b` vs `a \| b` | differ when a != b | counterexample (1,0) or (0,1), checked in Python |
| counter vs counter that shows 0 when the count is 6 | step k shows k; difference at step 6 | `cycles` 8 finds it at cycle 6; 3 and 6 do not; 7 does |
| the 16 truth tables of two inputs vs AND | | equivalent only for 0b1000, every counterexample checked |
| netlists from all seven targets vs the source | | equivalent (proved by SAT) |

## Not done, not verified

* **Linux and macOS.** The code is the same, but the memory cap code, the exit codes mapped to messages (the Windows stack-overflow code is mapped; `-11` is a guess for Linux) and the Yosys locations were not run there.
* **Other Yosys versions.** Message formats are read from the log (`Warning:`, `ERROR:`, the `ltp` and SAT lines, `stat -json` keys); a version that words them differently would lose findings or counterexamples silently or fail as `engine.failure`. Only 0.70+157 was tried.
* **The guard is not proven complete.** It was reasoned from how Yosys's preprocessor and lexer treat text and tested on the cases above, plus a negative control, but no fuzzing was done and the Yosys preprocessor was not read line by line. The residual risk is a construct that makes Yosys read or write a file that is neither a `` `include `` nor an allowed-list system task. Yosys parses untrusted input in C++ (stack overflow and `bad_alloc` crashes were seen); the layer is not a sandbox (see `docs/external/design.md`).
* **Macros across files.** A macro defined in one file and used in another is refused (Yosys would share the definition).
* **SystemVerilog** only as far as `read_verilog -sv` goes; assertions and interfaces are not supported by Yosys's front end. `$error`, `$fatal` and immediate/concurrent assertions that use system tasks outside the list are refused.
* **Sequential equivalence is bounded and starts from zero.** No unbounded proof (no induction, no `-tempinduct`), no free initial state, no reset sequences, no multi-clock designs, no X semantics beyond what `sat` does.
* **`digital_check`** only knows what Yosys reports; the first syntax error ends the run. The loop check after flattening reports a loop only when its source lines are not already in a loop found before flattening, so a loop found twice under different names may appear twice. Wire locations come from `src` attributes: a wire without one has `line: null`. Multiple-driver messages sometimes name an unexpected signal (Yosys names the alias that carries the conflict); the lines come from the drivers it lists.
* **Absurd designs.** Time (30 s default) and memory (1024 MB) stop them, but a legitimate large design may need `timeout_seconds` raised to 120 and may still be refused. No size heuristic on literals or widths: a declared width of 10^9 bits runs until the time limit.
* **A real LLM client.** The tools were called through `fastmcp.Client`; a call from a real model (`claude -p --mcp-config`) was not made here.
* `provenance.engine_versions` is for Python distributions and cannot name the version of Yosys, which is in `data.yosys.version`.
