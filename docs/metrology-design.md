# metrology domain: design rationale

Produced with pstack's `architect` flow (Phases A, B, D, run by hand; see "Process notes" at the end).
Scope: Phase 1 tolerance tools, `metrology_fit_limits` (ISO 286 hole/shaft fits) and `metrology_stackup_1d` (1-D tolerance stack-up).

## Problem

Agents need two things that LLMs get wrong from memory: ISO 286 limits and fits, and arithmetic over a tolerance chain.
Constraints from Phase 0 that the design must honor:

- every tool returns `ToolResult` through `runner.run_tool`; physical inputs cross `units.parse_quantity` (bare numbers rejected);
- tool parameters are plain strings, booleans and integers so small local models can fill them;
- clean-room, permissive-only dependencies, no LLM calls, no API keys, all results `unreviewed`.

## Usage (caller's view)

```text
metrology_fit_limits(nominal="25 mm", fit="H7/g6")
  -> hole 25.000..25.021, shaft 24.980..24.993, min clearance 0.007 mm, max 0.041 mm, fit_type "clearance"

metrology_stackup_1d(
  chain='[{"name":"housing","nominal":"50 mm","tol":"0.10 mm","direction":"+"},
          {"name":"bearing","nominal":"20 mm","plus":"0","minus":"0.12 mm","direction":"-"}, ...]',
  method="rss", output_unit="mm", lower_limit="0.2 mm", upper_limit="0.8 mm",
  monte_carlo=True, samples=100000, seed=1)
  -> nominal / min / max of the closing dimension, per-link contribution, P(out of spec)
```

## Shape

Three modules inside `eng_toolkit/domains/metrology/`:

| module | owns | knows nothing about |
| --- | --- | --- |
| `iso286.py` + `data/iso286.json` | the ISO 286 tables, designation parsing, zone deviation rules (general and special rule) | MCP, pint, the contract |
| `stackup.py` | link model, worst-case, RSS, Monte Carlo | MCP, ISO 286 |
| `tools.py` | FastMCP server, string parsing into the two pure modules, `run_tool` glue | table values, statistics |

The pure modules take floats in a fixed unit (mm for dimensions) and raise `UnitsError` with the existing error families (`input.invalid`, `input.out_of_range`), so `run_tool` turns them into structured failures without new plumbing.
Interface depth: `iso286.fit_limits(size_mm, "H7/g6")` hides range lookup, IT grade lookup, the fundamental-deviation tables, the Δ special rule and the js/j/J exceptions behind one call. `stackup.stack(links, method, ...)` hides the asymmetric-tolerance mean shift and the RNG.

## Decisions

### 1. ISO 286 tables: build our own JSON, do not borrow isofits' data

- isofits (PyPI, MIT, license file shipped) covers 37 hole and 37 shaft classes for 3-400 mm as two-line strings such as `"+28" / "+20"`. mcp-tolerance (JSR, MIT, TypeScript) covers IT1-IT18 for 0-500 mm but only a subset of positions (shaft c-g, h, js, k, m, n, p, r, s, u). The toolkit needs IT01-IT18, every position letter and 3-500 mm, so neither covers the scope, and borrowing either would still leave us with a second table to write (red flag: split ownership).
- Neither is trustworthy enough to be the source. The sweep in `tests/test_metrology_fits.py` found cells where isofits disagrees with the standard's own rules (E7 at 315-400 mm, K6 at 6-10 mm, M6 at 250-315 mm, f6 at 120-180 mm), and mcp-tolerance's reference fixtures omit the delta special rule for K/P holes and give k a value of 0 for sizes above 3 mm. Copying either would have imported those errors.
- Our JSON stores the standard's tabulated **IT values** and **shaft fundamental deviations** (the tables the standard prints as primary data) and derives hole deviations with the standard's own general and special rules in code. That is about 30 rows of numbers instead of thousands of cells, so a transcription error has one place to live.
- isofits is a **test-only oracle** (`dev` dependency group, never imported at runtime). The tests compare our output to every class isofits has, across all its size ranges, and pin the exact list of cells where it is wrong (each with the hand calculation). A handful of mcp-tolerance reference fits that agree with hand calculation are copied into the tests as plain data. A second check recomputes each tabulated shaft deviation from the ISO 286-1 formulas and fails on gross disagreement, which catches a mistyped number even outside isofits' coverage.
- Values are facts of an international standard and are tabulated in many open references; the JSON carries source and edition fields and the README states that the standard itself, not this file, is authoritative.

### 2. Stack-up: closed-form pure Python for worst-case and RSS, NumPy for Monte Carlo, no monaco

- worst-case and RSS are a few sums; numpy would add nothing. Plain `math` keeps them exact and trivially hand-checkable.
- Monte Carlo is vectorised draws from `numpy.random.default_rng(seed)`: one dependency (BSD-3-Clause, already a transitive dependency of most scientific Python), fixed seed gives reproducible output, `PCG64` is a stable stream.
- monaco (MIT) is a general Monte Carlo experiment framework (case/variable/output objects, parallel pools, plotting). It requires numpy, scipy, matplotlib, pillow, psutil, cloudpickle and tqdm, and an object model we would wrap in a function that draws a few arrays. The wrapper would hide almost none of its surface (a shallow module) and would pull about a dozen packages into a toolkit whose rule is a small, licence-audited core. Plain `numpy.random.default_rng(seed)` is enough for sums of independent draws.

### 3. Tool granularity: two coarse tools, no sub-tools

- `metrology_fit_limits` takes one fit code and returns everything (limits, clearances, fit type) so an agent never chains "look up IT", "look up deviation", "combine". Listing tables is out of scope: an agent asks for a fit, not for a table.
- `metrology_stackup_1d` takes the whole chain as one JSON string and chooses the method by a `method` argument; Monte Carlo is a boolean on the same call, not a third tool. A chain object per call means the small model never maintains state between calls.
- The chain is a JSON string because the contract keeps parameters to primitives. One string format, one way to state a tolerance (`tol` for symmetric, `plus` + `minus` for bilateral/unilateral).

## Tradeoffs accepted

- We accept transcribing the shaft tables by hand in exchange for owning a small, testable, documented data file. Mitigated by the isofits and formula cross-checks.
- Hole deviations are derived, not tabulated, so they are only as right as the general/special rule implementation. The isofits sweep covers K, M, N, P, R, J, JS holes at the grades isofits has.
- RSS treats every tolerance as a +/-3 sigma bound on a centered or mean-shifted normal. This is the common drawing-office convention and is stated in the result; it is not a process-capability study.
- Sizes below 3 mm and above 500 mm are refused with an explicit error rather than extrapolated.

## Alternatives considered

1. **Copy isofits' tables.** Smaller and quicker, but 3-400 mm and 74 classes only. Loses on coverage and on shared ownership of data. Rejected as above.
2. **Compute everything from the ISO formulas at runtime.** No tables at all. The standard's tabulated values are rounded by rules (preferred-number rounding) that the formulas do not reproduce exactly, so results would differ from every drawing. Kept only as a test check.
3. **Many small tools** (`it_grade`, `fundamental_deviation`, `fit_type`, `worst_case`, `rss`, `mc`). Each is trivial, the agent coordinates them, and small models drop arguments between calls. Rejected for depth and for the project's coarse-tool rule.
4. **monaco for stack-up.** See decision 2.

## Open questions and risks

- Do we want sizes in inches (`"1 in"`)? Currently any length unit is accepted and converted to mm, then range-checked.
- Is ISO 286 edition 2010 the right target, or do users need the 1988 tables for legacy drawings? The file records the edition. `js`/`JS` use exactly +/-IT/2 (the standard allows rounding odd IT7-IT11 down; we do not, which matches isofits).
- Monte Carlo of non-normal links (uniform, truncated) is limited to `normal` and `uniform`.

## Process notes (pstack in Claude Code)

- Read `docs/harness/claude-code.md` first and applied its mapping: pstack skills were read from the plugin folder (the `Skill` tool refuses most pstack skills); the Cursor model rule and `AskQuestion` were not needed.
- `architect`: Phase A (ground: Phase 0 `calc.hydraulics`, `units`, `runner`, `contract`, gateway tests) and Phase D (implement against the sketch) were run. Phase B asks `arena` to fan out to the `architect runners` models; the defaults include a non-Claude model, so per the harness doc it would run on the session model and add nothing. The "design it twice" requirement was met by hand (see Alternatives considered). Skipped in Claude Code: `setup-pstack` (writes user-global config, left to the user).
- `tdd` was used for implementation: failing tests first (import error, then specific assertion failures), then code, then a rerun. Both tools were built that way.
- `poteto-mode`: not run. Its scripts need `bun`, which is not installed here, and we were told not to install it.
