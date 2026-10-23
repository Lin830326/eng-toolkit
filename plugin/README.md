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
| skill `eng-toolkit` | unit rules, how to read results and errors, the tool map for all domains, the report format |
| skill `eng-structural-check` + `/eng-structural-check` | beam, frame, truss, column, plate, vibration checks: model choice, inputs, hand check, convergence, utilization |
| skill `eng-dfm-review` + `/eng-dfm-review` | CNC milling and sheet-metal review of a STEP/IGES/BREP file |
| `/eng-toolkit-setup` | connection and dependency check, registration command |
| prompt hook (`hooks/hooks.json`) | one short reminder line with every prompt: engineering numbers must come from the `mcp__eng-toolkit__` tools, not hand arithmetic. Delete `hooks/` to turn it off |

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

Run it yourself: `python scripts/eval_plugin.py --mcp-config mcp.json --plugin-dir plugin --model haiku --repeat 2`.
