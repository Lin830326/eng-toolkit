"""User-acceptance runs: a simulated person works with a Claude assistant that has the eng-toolkit plugin and tools.

    python scripts/uat_run.py uat/scenarios/P1_*.json --out uat/runs/2026-10-28 [--assistant haiku] [--user sonnet]

A scenario is a JSON file:
    id, persona (who they are, how they write), goal (what they want to leave with), opening (their first message; may use
    {name} placeholders filled from `files`), facts (what they know and reveal only when asked), files {name: path},
    max_turns, truth (path of a script that prints the independent expected answers as JSON; used by the judge, never by the
    assistant or the simulated user).

    python scripts/uat_run.py --only H1,H3 --tag r3 --out uat/runs/r3      (scenarios default to uat/scenarios/*.json)

For every scenario the runner writes <out>/<id>/ with transcript.md, transcript.json (messages, tools used with inputs,
artifacts, a skill summary), ws/ (the tool artifacts) and rendered/ (the tools' own PNGs copied, plus a PNG of every SVG, PDF
page and GLB that has no PNG of its own, so a judge can look at what the person saw). Needs the `claude` CLI; resvg-py
(with a CJK font from C:/Windows/Fonts when present) is used only here, to rasterise.
"""

import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SIM_PROMPT = """You are role-playing a real person talking to an AI assistant that has engineering calculation tools. You are NOT the assistant.

WHO YOU ARE: {persona}

WHAT YOU WANT TO LEAVE WITH: {goal}

THINGS YOU KNOW (a fact stays unsaid until the assistant asks for it or your last message needs it; never list facts, numbers or wishes nobody asked about; you may not know technical terms): {facts}

Conversation so far:
{history}

PICTURES SHOWN TO YOU (image files you may open with the Read tool: the charts, drawings and views the assistant's tools produced so far; this list is the only way you see a picture):
{pictures}

Write your next message exactly as this person would type it: short, natural, in their language and register, imperfect where realistic. Answer what the assistant asked, and only that. React to what it actually said: push back if something looks wrong or confusing, ask for the picture or file if you wanted one and it did not show it. A bare file path in the assistant's reply is something you cannot open unless the same file is listed under PICTURES SHOWN TO YOU; if it is not listed, say you cannot see it. Never reveal that you are role-playing, never write the assistant's part. You cannot approve tools, install software or grant permissions: if asked, say you don't know how.
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


CJK_FONTS = ["C:/Windows/Fonts/msjh.ttc", "C:/Windows/Fonts/kaiu.ttf", "C:/Windows/Fonts/mingliu.ttc", "C:/Windows/Fonts/NotoSansTC-VF.ttf"]


def svg_to_png(svg_text):
    """resvg PNG bytes; a CJK font file is passed when resvg_py takes one (its default fonts have no Chinese glyphs)."""
    import resvg_py

    fonts = [f for f in CJK_FONTS if Path(f).exists()][:1]
    family = {"msjh.ttc": "Microsoft JhengHei", "kaiu.ttf": "DFKai-SB", "mingliu.ttc": "MingLiU", "NotoSansTC-VF.ttf": "Noto Sans TC"}
    base = dict(svg_string=svg_text, width=1100)
    if fonts:
        name = family.get(Path(fonts[0]).name)
        tries = ([dict(base, font_files=fonts, sans_serif_family=name, serif_family=name, font_family=name),
                  dict(base, font_files=fonts, font_family=name)] if name else []) + [dict(base, font_files=fonts)]
    else:
        tries = []
    for kwargs in tries + [base]:  # an older resvg_py without these parameters raises TypeError: fall back to fewer
        try:
            return bytes(resvg_py.svg_to_bytes(**kwargs))
        except TypeError:
            continue
    raise RuntimeError("resvg_py.svg_to_bytes failed")


def rasterise(run_dir):
    """Pictures the person could see, in rendered/: the tools' own PNGs (native, copied) first; an SVG, PDF page or GLB gets a
    rendered PNG only when no native PNG of the same name sits next to it. Files already rendered are not redone."""
    out = run_dir / "rendered"
    out.mkdir(exist_ok=True)
    made = []
    files = sorted(p for p in (run_dir / "ws").rglob("*") if p.is_file() and "input" not in p.relative_to(run_dir / "ws").parts[1:-1])
    for path in files:
        try:
            native = path.with_suffix(".png").exists()
            if path.suffix == ".png":
                target = out / (path.parent.name + "_" + path.name)
                if not target.exists():
                    target.write_bytes(path.read_bytes())
            elif native and path.suffix in (".svg", ".glb"):
                continue
            elif path.suffix == ".svg":
                target = out / (path.parent.name + "_" + path.stem + ".png")
                if not target.exists():
                    svg_text = path.read_text(encoding="utf-8")
                    # resvg ignores CSS var(--name, fallback): use the fallback so the picture is the default theme, not black
                    svg_text = re.sub(r"var\(\s*--[\w-]+\s*,\s*([^)]+?)\s*\)", lambda m: m.group(1), svg_text)
                    # CAD exporters write width="16.5mm"; resvg wants a plain number
                    svg_text = re.sub(r'(<svg[^>]*?\s(?:width|height)=")([0-9.]+)(?:mm|cm|in|pt|px)"', lambda m: m.group(1) + str(round(float(m.group(2)) * 8, 2)) + '"', svg_text, count=2)
                    target.write_bytes(svg_to_png(svg_text))
            elif path.suffix == ".pdf":
                import pypdfium2

                pdf = pypdfium2.PdfDocument(str(path))
                for i in range(min(len(pdf), 3)):
                    target = out / f"{path.parent.name}_{path.stem}_p{i + 1}.png"
                    if not target.exists():
                        pdf[i].render(scale=1.6).to_pil().save(target)
                    made.append(str(target))
                continue
            elif path.suffix == ".glb" and path.stat().st_size < 60_000_000:
                target = out / (path.parent.name + "_" + path.stem + ".png")
                if not target.exists():
                    # render_glb.py needs matplotlib, which the project venv does not have: UAT_RENDER_PYTHON, then this python, then PATH's
                    last = ""
                    for py in dict.fromkeys(p for p in (os.environ.get("UAT_RENDER_PYTHON"), sys.executable, shutil.which("python")) if p):
                        last = run([py, str(ROOT / "uat" / "render_glb.py"), str(path), str(target)], 180).stderr[-300:]
                        if target.exists():
                            break
                    if not target.exists():
                        (out / (path.parent.name + "_" + path.stem + ".error.txt")).write_text("GLB not rendered: " + last, encoding="utf-8")
            else:
                continue
            if target.exists():
                made.append(str(target))
        except Exception as exc:  # a render problem must not stop the run
            (out / (path.parent.name + "_" + path.stem + ".error.txt")).write_text(repr(exc), encoding="utf-8")
    return made


def skill_summary(turns):
    """Which Skill calls happened, and whether the first tool call of the whole conversation came before any skill load."""
    calls = [(t["turn"], x) for t in turns for x in t["tools"]]
    skills = []
    for turn, call in calls:
        if call["name"] == "Skill":
            m = re.search(r'"skill"\s*:\s*"([^"]+)"', call["input"])
            skills.append({"turn": turn, "skill": m.group(1) if m else call["input"][:80]})
    first = calls[0][1]["name"].replace("mcp__eng-toolkit__", "") if calls else None
    return {"skills_loaded": skills, "first_tool": first, "first_tool_before_any_skill_load": bool(calls) and first != "Skill",
            "tool_calls_before_first_skill": next((i for i, (_, c) in enumerate(calls) if c["name"] == "Skill"), len(calls))}


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

    # a relative path in `files` (e.g. uat/data/x.csv) means "inside this repository": make it absolute so stage() copies it to the inbox
    spec["files"] = {k: ((ROOT / v).as_posix() if not Path(v).is_absolute() and (ROOT / v).exists() else v) for k, v in spec.get("files", {}).items()}
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
    skills = skill_summary(turns)
    (run_dir / "transcript.json").write_text(json.dumps({"scenario": spec, "tag": args.tag, "assistant_model": args.assistant, "user_model": args.user, "skill_summary": skills, "turns": turns, "rendered": rendered}, ensure_ascii=False, indent=1), encoding="utf-8")
    lines = [f"# {spec['id']}", "", f"Persona: {spec['persona']}", f"Goal: {spec['goal']}", ""]
    loaded = ", ".join(f"{x['skill']} (turn {x['turn']})" for x in skills["skills_loaded"]) or "none"
    lines += [f"Run: tag={args.tag or '-'}, assistant={args.assistant}, user={args.user}, plugin={'off' if args.no_plugin else 'on'}",
              f"Skills loaded: {loaded}; first tool call: {skills['first_tool'] or 'none'}; first tool call before any skill load: {'yes' if skills['first_tool_before_any_skill_load'] else 'no'}", ""]
    for t in turns:
        lines += [f"## Turn {t['turn']}", "", f"**PERSON:** {t['person']}", "", f"**ASSISTANT** (tools: {', '.join(x['name'].replace('mcp__eng-toolkit__', '') for x in t['tools']) or 'none'}):", "", t["assistant"], ""]
        if t.get("ended"):
            lines += [f"_{t['ended']}_", ""]
    lines += ["## Rendered outputs", ""] + [f"- {r}" for r in rendered]
    (run_dir / "transcript.md").write_text("\n".join(lines), encoding="utf-8")
    return spec["id"], len(turns), len(rendered), skills


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("scenarios", nargs="*", help="scenario files or globs (default uat/scenarios/*.json)")
    ap.add_argument("--only", help="comma-separated scenario ids or id prefixes, e.g. H1,H3 or E2_ce_amplifier_bode")
    ap.add_argument("--tag", default="", help="label for this run, printed in the log and written to transcript.json and transcript.md")
    ap.add_argument("--out", required=True)
    ap.add_argument("--assistant", default="haiku")
    ap.add_argument("--user", default="sonnet")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--no-plugin", action="store_true", help="run the assistant without the plugin (baseline)")
    args = ap.parse_args()
    patterns = args.scenarios or [str(ROOT / "uat" / "scenarios" / "*.json")]
    files = [Path(p) for pattern in patterns for p in sorted(glob.glob(pattern))] or [Path(p) for p in patterns]
    if args.only:
        wanted = [w.strip() for w in args.only.split(",") if w.strip()]
        files = [p for p in files if any(p.stem == w or p.stem.startswith(w + "_") for w in wanted)]
        missing = [w for w in wanted if not any(p.stem == w or p.stem.startswith(w + "_") for p in files)]
        if missing:
            sys.exit(f"--only: no scenario matches {', '.join(missing)}")
    if not files:
        sys.exit("no scenarios selected")
    print(f"[uat_run] tag={args.tag or '-'} assistant={args.assistant} user={args.user} plugin={'off' if args.no_plugin else 'on'} scenarios={', '.join(p.stem for p in files)}", flush=True)
    with ThreadPoolExecutor(args.workers) as pool:
        for sid, turns, renders, skills in pool.map(lambda p: scenario_run(p, args), files):
            print(f"[{args.tag or '-'}] {sid}: {turns} turns, {renders} rendered outputs; skills: {', '.join(x['skill'] for x in skills['skills_loaded']) or 'none'}; first tool before skill: {skills['first_tool_before_any_skill_load']}", flush=True)


if __name__ == "__main__":
    main()
