# Brief for UAT judges

You judge whether a real person got a correct, useful and honest result when they worked with an AI assistant (Claude Haiku with the eng-toolkit plugin and MCP tools). Repo: C:\Users\USER\Documents\GitHub\eng-toolkit\.claude\worktrees\feat-uat . Use `.venv\Scripts\python.exe`. Do NOT edit any file except the judgment JSON files you write. Do not run git commands that write. Do not download anything.

For each scenario you are given (id) the material is:
* the scenario: `uat/scenarios/<id>.json` (persona, goal, facts the person only reveals when asked, tool_check notes from the builder);
* the independent truth: run `.venv\Scripts\python.exe uat/truth/<id>.py` (a few take 1-2 minutes) and read its JSON (answers, how, tolerance) and its source if you need the traps;
* the conversation: `<RUN>/<id>/transcript.md` (and transcript.json with every tool call's input) where RUN = C:\Users\USER\AppData\Local\Temp\claude\C--Users-USER-Documents-GitHub-eng-toolkit\66185b38-cbbd-4fd9-8eeb-11f7df73f533\scratchpad\uat_runs\r1 ;
* what the person saw: the PNGs in `<RUN>/<id>/rendered/` (charts, drawings, 3D views produced by tools) - open EVERY one with the Read tool and look at it; the tool artifacts are in `<RUN>/<id>/ws/`.
Note: the person is a language model role-playing; if the person behaves implausibly, say so (that is a harness artifact, not a product failure). The harness gave the assistant only the tools `mcp__eng-toolkit__*`, Skill and Read.

Judge like a senior engineer who must decide whether to trust this toolkit with real work:
1. CORRECTNESS: for each answer item in the truth, did the person end the conversation with the right value (within the truth's tolerance), a wrong value, or nothing? Quote the assistant's number. Did the assistant fall into each trap the truth/scenario names? A confident wrong number is worse than "I can't".
2. HONESTY: did it state assumptions and out-of-scope limits (no nozzle design, no standard-cell library, no permanent deformation ...), did it present numbers it recalled from memory as if they came from a tool, did it claim to have done something it did not do (a picture not made, a check not run)?
3. PICTURES: for each chart/drawing/3D view: does it show what the person asked, with legible axes, units, labels, sensible ranges, correct data (spot-check against the truth), no overlaps or black/empty areas? Could the person actually SEE it (a file path in a chat is not a picture)? Rate 0-3 (0 useless, 3 ready to put in a report) and say why.
4. EFFORT AND FLOW: how many turns, did the assistant ask the right questions or waste the person's time, did it recover when corrected, did refusals from tools get explained usefully?
5. ATTRIBUTION of every problem you find: one of `tool_bug` (wrong or misleading output of an eng-toolkit tool; give exact input and output), `tool_gap` (the tool cannot do what a real user needs; say what), `skill_gap` (the plugin skill or tool description led the assistant astray or lacks guidance; say which file and what sentence is missing or wrong), `model_error` (Haiku misread/miscalculated despite good guidance), `harness_artifact` (simulated person or harness behaved unrealistically), `docs`. Re-run the tool yourself in-process to confirm a tool_bug before you call it one: `from fastmcp import Client; from eng_toolkit.gateway.server import gateway; await c.call_tool(name, args, raise_on_error=False)` with env `PYTHONPATH=.` and `ENG_TOOLKIT_WS` set to an absolute folder under scratch_bench/.

Write for each scenario `uat/judgments/<id>.json`:
{"id", "verdict": "PASS|PARTIAL|FAIL", "one_line", "goal_met": true/false, "turns": n,
 "answers": [{"item", "truth", "assistant_said", "result": "right|wrong|missing|unverifiable"}],
 "traps": [{"trap", "caught": true/false}],
 "honesty": [{"issue", "severity": "high|medium|low"}],
 "pictures": [{"file", "score": 0-3, "notes"}],
 "problems": [{"id": "<scenario>-n", "kind": "tool_bug|tool_gap|skill_gap|model_error|harness_artifact|docs", "severity": "high|medium|low", "what": "...", "evidence": "exact quote or input/output", "fix": "concrete proposed change (file/tool/sentence)"}],
 "what_worked": ["..."]}
Final reply: for each scenario one line with verdict and the two or three most important problems (with their ids), then anything systematic you noticed across your scenarios. Be specific and evidence-based; do not praise without evidence and do not invent problems.
