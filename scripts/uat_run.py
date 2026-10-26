"""User-acceptance runs: a simulated person works with a Claude assistant that has the eng-toolkit plugin and tools.

    python scripts/uat_run.py uat/scenarios/P1_*.json --out uat/runs/2026-10-28 [--assistant haiku] [--user sonnet]

A scenario is a JSON file:
    id, persona (who they are, how they write), goal (what they want to leave with), opening (their first message; may use
    {name} placeholders filled from `files`), facts (what they know and reveal only when asked), files {name: path},
    max_turns, truth (path of a script that prints the independent expected answers as JSON; used by the judge, never by the
    assistant or the simulated user).

For every scenario the runner writes <out>/<id>/ with transcript.md, transcript.json (messages, tools used with inputs,
artifacts), ws/ (the tool artifacts) and rendered/ (PNG of every SVG, PDF page and GLB the tools produced, so a judge can look
at what the person saw). Needs the `claude` CLI; resvg-py and matplotlib are used only here, to rasterise.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SIM_PROMPT = """You are role-playing a real person talking to an AI assistant that has engineering calculation tools. You are NOT the assistant.

WHO YOU ARE: {persona}

WHAT YOU WANT TO LEAVE WITH: {goal}

THINGS YOU KNOW (reveal a fact only when the assistant asks for it or it becomes relevant; you do not volunteer everything, and you may not know technical terms): {facts}

Conversation so far:
{history}

PICTURES SHOWN TO YOU (you may look at these image files with the Read tool; they are the charts, drawings and views the assistant's tools produced so far):
{pictures}

Write your next message exactly as this person would type it: short, natural, in their language and register, imperfect (missing units, vague wording, a wrong guess) where that is realistic. React to what the assistant actually said: answer its questions, push back if something looks wrong or confusing to you, ask for the picture or file if you wanted one and it did not show it. Never reveal that you are role-playing, never write the assistant's part. You cannot change what the assistant is allowed to do (you have no way to approve tools, install software or grant permissions: if it asks, say you don't know how). You can only see what the assistant writes in chat and the picture files listed under PICTURES SHOWN TO YOU; if it only gives a file path that is not listed there, you cannot open it and you say so.
If you now have what you wanted, or you are giving up, reply with exactly one line starting with <DONE> followed by satisfied or gave up and a short reason. Otherwise reply with only your message."""


def run(cmd, timeout, cwd=None):
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, stdin=subprocess.DEVNULL, cwd=cwd)


def parse_stream(stdout):
    """Assistant text, tool calls and artifact paths from claude --output-format stream-json."""
    text, tools, artifacts, result = "", [], [], {}
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        kind = obj.get("type")
        if kind == "result":
            result = obj
            text = obj.get("result", text)
        elif kind == "assistant":
            for block in (obj.get("message") or {}).get("content") or []:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    tools.append({"name": block.get("name", ""), "input": json.dumps(block.get("input"), ensure_ascii=False)[:1500]})
        elif kind == "user":
            for block in (obj.get("message") or {}).get("content") or []:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    content = block.get("content")
                    blob = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
                    for m in re.finditer(r'"path"\s*:\s*"([^"]+\.(?:svg|png|pdf|glb|csv|step|stl|dxf|json|gerber|zip|drl|pos))"', blob.replace("\\\\", "/")):
                        artifacts.append(m.group(1))
    return text, tools, sorted(set(artifacts)), result


def rasterise(run_dir):
    out = run_dir / "rendered"
    out.mkdir(exist_ok=True)
    made = []
    for path in sorted((run_dir / "ws").rglob("*")):
        try:
            if path.suffix == ".svg":
                import resvg_py

                svg_text = path.read_text(encoding="utf-8")
                # resvg ignores CSS var(--name, fallback): use the fallback so the picture is the default theme, not black
                svg_text = re.sub(r"var\(\s*--[\w-]+\s*,\s*([^)]+?)\s*\)", lambda m: m.group(1), svg_text)
                # CAD exporters write width="16.5mm"; resvg wants a plain number
                svg_text = re.sub(r'(<svg[^>]*?\s(?:width|height)=")([0-9.]+)(?:mm|cm|in|pt|px)"', lambda m: m.group(1) + str(round(float(m.group(2)) * 8, 2)) + '"', svg_text, count=2)
                png = resvg_py.svg_to_bytes(svg_string=svg_text, width=1100)
                target = out / (path.parent.name + "_" + path.stem + ".png")
                target.write_bytes(bytes(png))
                made.append(str(target))
            elif path.suffix == ".pdf":
                import pypdfium2

                pdf = pypdfium2.PdfDocument(str(path))
                for i in range(min(len(pdf), 3)):
                    target = out / f"{path.parent.name}_{path.stem}_p{i + 1}.png"
                    pdf[i].render(scale=1.6).to_pil().save(target)
                    made.append(str(target))
            elif path.suffix == ".glb" and path.stat().st_size < 60_000_000:
                target = out / (path.parent.name + "_" + path.stem + ".png")
                proc = run([sys.executable, str(ROOT / "uat" / "render_glb.py"), str(path), str(target)], 180)
                if target.exists():
                    made.append(str(target))
        except Exception as exc:  # a render problem must not stop the run
            (out / (path.stem + ".error.txt")).write_text(repr(exc), encoding="utf-8")
    return made


def scenario_run(path, args):
    spec = json.loads(Path(path).read_text(encoding="utf-8"))
    run_dir = Path(args.out).resolve() / spec["id"]
    (run_dir / "ws").mkdir(parents=True, exist_ok=True)
    mcp = run_dir / "mcp.json"
    mcp.write_text(json.dumps({"mcpServers": {"eng-toolkit": {
        "command": sys.executable, "args": ["-m", "eng_toolkit"],
        "env": {"PYTHONPATH": str(ROOT), "ENG_TOOLKIT_WS": str(run_dir / "ws")}}}}), encoding="utf-8")
    inbox = run_dir / "inbox"
    inbox.mkdir(exist_ok=True)
    work = run_dir / "work"  # the assistant runs here: empty, so it cannot see the repository, truth or scenario files
    work.mkdir(exist_ok=True)
    data_root = (ROOT / "uat" / "data").as_posix()

    def stage(text):
        """Copy uat/data files named in the text to the inbox and point the text there."""
        def fix(match):
            src = Path(match.group(0))
            if not src.exists():
                return match.group(0)
            dst = inbox / src.name
            dst.write_bytes(src.read_bytes())
            return dst.as_posix()
        return re.sub(re.escape(data_root) + r"/[\w.\-]+", fix, text)

    spec = json.loads(stage(json.dumps(spec, ensure_ascii=False)))
    opening = spec["opening"].format(**{k: v for k, v in spec.get("files", {}).items()})
    session = str(uuid.uuid4())
    history, turns = [], []
    message = opening
    for turn in range(spec.get("max_turns", 4)):
        history.append(("PERSON", message))
        cmd = ["claude", "-p", "--model", args.assistant, "--mcp-config", str(mcp), "--strict-mcp-config",
               "--tools", "Read,Skill", "--allowedTools", "mcp__eng-toolkit,Skill,Read", "--output-format", "stream-json", "--verbose"]
        if not args.no_plugin:
            cmd += ["--plugin-dir", str(ROOT / "plugin")]
        cmd += (["--session-id", session] if turn == 0 else ["--resume", session])
        cmd += ["--", message]
        try:
            proc = run(cmd, args.timeout, cwd=work)
            text, tools, artifacts, result = parse_stream(proc.stdout)
            if not text:
                text = f"(no answer; stderr: {proc.stderr[-400:]})"
        except subprocess.TimeoutExpired:
            text, tools, artifacts, result = "(the assistant timed out)", [], [], {}
        history.append(("ASSISTANT", text))
        turns.append({"turn": turn + 1, "person": message, "assistant": text, "tools": tools, "artifacts": artifacts,
                      "turns_used": result.get("num_turns"), "cost_usd": result.get("total_cost_usd")})
        convo = "\n\n".join(f"{who}: {what}" for who, what in history)
        rendered_now = rasterise(run_dir)
        pictures = "\n".join(f"- {r}" for r in rendered_now) or "(none yet)"
        sim = SIM_PROMPT.format(persona=spec["persona"], goal=spec["goal"], facts=json.dumps(spec.get("facts", {}), ensure_ascii=False), history=convo, pictures=pictures)
        reply = run(["claude", "-p", "--model", args.user, "--tools", "Read", "--allowedTools", "Read", "--output-format", "text", "--", sim], 600, cwd=work).stdout.strip()
        if reply.startswith("<DONE>") or not reply:
            turns[-1]["ended"] = reply or "<DONE> (empty reply)"
            break
        message = reply
    rendered = rasterise(run_dir)
    (run_dir / "transcript.json").write_text(json.dumps({"scenario": spec, "turns": turns, "rendered": rendered}, ensure_ascii=False, indent=1), encoding="utf-8")
    lines = [f"# {spec['id']}", "", f"Persona: {spec['persona']}", f"Goal: {spec['goal']}", ""]
    for t in turns:
        lines += [f"## Turn {t['turn']}", "", f"**PERSON:** {t['person']}", "", f"**ASSISTANT** (tools: {', '.join(x['name'].replace('mcp__eng-toolkit__', '') for x in t['tools']) or 'none'}):", "", t["assistant"], ""]
        if t.get("ended"):
            lines += [f"_{t['ended']}_", ""]
    lines += ["## Rendered outputs", ""] + [f"- {r}" for r in rendered]
    (run_dir / "transcript.md").write_text("\n".join(lines), encoding="utf-8")
    return spec["id"], len(turns), len(rendered)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("scenarios", nargs="+")
    ap.add_argument("--out", required=True)
    ap.add_argument("--assistant", default="haiku")
    ap.add_argument("--user", default="sonnet")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--no-plugin", action="store_true", help="run the assistant without the plugin (baseline)")
    args = ap.parse_args()
    files = [p for pattern in args.scenarios for p in sorted(Path().glob(pattern))] or [Path(p) for p in args.scenarios]
    with ThreadPoolExecutor(args.workers) as pool:
        for sid, turns, renders in pool.map(lambda p: scenario_run(p, args), files):
            print(f"{sid}: {turns} turns, {renders} rendered outputs")


if __name__ == "__main__":
    main()
