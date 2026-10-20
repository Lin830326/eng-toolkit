"""Run the round 2 small-model evaluation of geometry_make_part (not a test: pytest does not collect this file).

    python tests/eval_data/run_round2.py seen                     write tool_as_seen.json from the gateway
    python tests/eval_data/run_round2.py ask OUTDIR [SAMPLES [R09,R10]]   ask Claude Haiku (claude -p, no tools) once per task and sample
    python tests/eval_data/run_round2.py score OUTDIR             build every spec with the real tool and compare with the closed form

The model sees only the generated tool description and parameter descriptions (tool_as_seen.json) and the task text, and
answers once with the spec.  It cannot run or read anything: claude is started with no tools and no settings.  Scoring is done
here, never by the model: the spec goes through geometry_make_part and the STEP it wrote is measured again with
geometry_measure.  A task passes when the relative volume error is at most REL_TOL_VOLUME, the size is within ABS_TOL_SIZE in
each axis (in order) and the part is one solid.
"""

import asyncio
import importlib.util
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"eval_{name}", HERE / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


TASKS = _load("tasks_round2").TASKS
TOL = _load("tasks")

SYSTEM = (
    "You fill in the parameters of an MCP tool from its description. You cannot run, read or look up anything. "
    "Answer with the value of the `spec` parameter only: one JSON object, no code fence, no commentary."
)


def prompt(task: dict) -> str:
    seen = json.loads((HERE / "tool_as_seen.json").read_text(encoding="utf8"))
    return (
        "This is an MCP tool exactly as a client shows it to you.\n\n"
        f"Tool name: {seen['name']}\n\nDescription:\n{seen['description']}\n\n"
        f"Parameters:\n{json.dumps(seen['parameters'], indent=1)}\n\nRequired: {seen['required']}\n\n"
        f"Task: {task['text']}\n\nWrite the `spec` string for this part. Reply with the JSON object only."
    )


def extract(answer: str) -> str:
    text = answer.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fenced:
        text = fenced.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    return text[start : end + 1] if start >= 0 and end > start else text


def ask(task: dict, scratch: Path) -> tuple[str, dict]:
    done = subprocess.run(
        [
            "claude", "-p", "--model", "haiku", "--tools", "", "--disable-slash-commands", "--no-session-persistence",
            "--setting-sources", "", "--system-prompt", SYSTEM, "--output-format", "json",
        ],
        input=prompt(task),
        capture_output=True,
        text=True,
        encoding="utf8",
        cwd=scratch,
        timeout=600,
        check=False,
    )
    if done.returncode != 0:
        raise SystemExit(f"claude failed for {task['id']}: {done.stderr[:500]}")
    reply = json.loads(done.stdout)
    return reply["result"], {k: reply.get(k) for k in ("modelUsage", "total_cost_usd", "duration_ms", "num_turns")}


def cmd_seen() -> None:
    from fastmcp import Client

    from eng_toolkit.gateway.server import gateway

    async def fetch():
        async with Client(gateway) as client:
            tools = await client.list_tools()
        return next(t for t in tools if t.name == "geometry_make_part")

    tool = asyncio.run(fetch())
    schema = getattr(tool, "input_schema", None) or tool.inputSchema
    (HERE / "tool_as_seen.json").write_text(
        json.dumps(
            {
                "name": tool.name,
                "description": tool.description,
                "parameters": {k: {"type": v.get("type"), "default": v.get("default"), "description": v.get("description")} for k, v in schema["properties"].items()},
                "required": schema.get("required", []),
            },
            indent=1,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf8",
    )
    print("wrote tool_as_seen.json,", len(tool.description), "characters of description")


def cmd_ask(out: Path, samples: int, only: list[str] | None = None) -> None:
    out.mkdir(parents=True, exist_ok=True)
    meta = {}
    with tempfile.TemporaryDirectory(prefix="eval-scratch-") as scratch:
        for task in [t for t in TASKS if only is None or t['id'] in only]:
            for sample in range(1, samples + 1):
                answer, info = ask(task, Path(scratch))
                name = f"{task['id']}_s{sample}"
                (out / f"{name}.raw.txt").write_text(answer, encoding="utf8")
                (out / f"{name}.json").write_text(extract(answer), encoding="utf8")
                meta[name] = info
                print(name, "asked", flush=True)
    (out / "meta.json").write_text(json.dumps(meta, indent=1), encoding="utf8")


def cmd_score(out: Path) -> None:
    from eng_toolkit.domains.geometry.tools import make_part, measure

    build = getattr(make_part, "fn", make_part)
    read = getattr(measure, "fn", measure)
    rows = []
    for task in TASKS:
        for path in sorted(out.glob(f"{task['id']}_s*.json")):
            if path.name.endswith(".raw.json") or path.name == "meta.json":
                continue
            text = path.read_text(encoding="utf8")
            row = {"id": path.stem, "passed": False, "detail": ""}
            made = build(spec=text, name=task["id"])
            if not made.ok:
                row["detail"] = f"{made.error.family}: {made.error.message[:200]}"
            else:
                step = next(a.path for a in made.artifacts if a.path.endswith(".step"))
                measured = read(model_path=step)
                volume = measured.values["volume"].quantity
                size = [measured.values[k].quantity for k in ("size_x", "size_y", "size_z")]
                rel = abs(volume - task["volume"]) / task["volume"]
                worst = max(abs(a - b) for a, b in zip(size, task["size"]))
                row.update(volume=volume, expected=task["volume"], rel_error=rel, size=size, worst_size_error=worst, solids=measured.data["solids"])
                row["passed"] = rel <= TOL.REL_TOL_VOLUME and worst <= TOL.ABS_TOL_SIZE and measured.data["solids"] == 1
                row["ops"] = [f["op"] for f in json.loads(text).get("features", [])]
            rows.append(row)
            print(row["id"], "PASS" if row["passed"] else "FAIL", row.get("rel_error", row["detail"]), row.get("ops", ""))
    (out / "scores.json").write_text(json.dumps(rows, indent=1), encoding="utf8")
    print(f"{sum(r['passed'] for r in rows)}/{len(rows)} passed")


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command == "seen":
        cmd_seen()
    elif command == "ask":
        cmd_ask(Path(sys.argv[2]), int(sys.argv[3]) if len(sys.argv) > 3 else 1, sys.argv[4].split(",") if len(sys.argv) > 4 else None)
    elif command == "score":
        cmd_score(Path(sys.argv[2]))
    else:
        raise SystemExit(__doc__)
