# User-acceptance scenarios

A scenario is a realistic piece of engineering work, started by a person who writes the way people write (vague, missing units,
sometimes in Chinese), run against an assistant that has the eng-toolkit plugin and tools. `scripts/uat_run.py` plays the person
with a language model, keeps the transcript and rasterises every chart, drawing and model the tools produced. A judge then
compares what the person got with an independent answer (`uat/truth/<id>.py`: not our tools; KiCad's own CLI, ngspice, Yosys,
CalculiX, scipy, a textbook formula) and looks at the pictures.

Scenario file (`uat/scenarios/<id>.json`):

```json
{
  "id": "P1_uno_board_review",
  "domains": ["pcb"],
  "persona": "who they are and how they write",
  "goal": "what they want to walk away with",
  "opening": "first message, may use {board} placeholders filled from files",
  "facts": {"anything they know but only tell when asked": "..."},
  "files": {"board": "C:/path/to/real/file"},
  "max_turns": 4,
  "truth": "uat/truth/P1_uno_board_review.py",
  "expect_picture": true
}
```

`truth` scripts print one JSON object: `{"answers": {...named numbers/facts with units...}, "how": "what produced them", "tolerance": "how close counts as right"}`.
