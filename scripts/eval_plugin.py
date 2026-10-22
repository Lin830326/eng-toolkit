"""Evaluate the eng-toolkit Claude Code plugin: the same tasks through `claude -p` with and without the plugin.

Each task is a natural-language engineering question with expected numbers or keywords.  The answer text is scored
automatically.  Needs the `claude` CLI, an MCP config that starts the eng-toolkit server, and (for the DFM tasks) the
`cad` extra.

    python scripts/eval_plugin.py --mcp-config mcp.json --plugin-dir plugin --model haiku --repeat 1 --out eval_out
"""

import argparse
import json
import re
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

NUMBER = re.compile(r"[-−+]?\d[\d,]*\.?\d*(?:[eE][-+]?\d+)?")


SUPERSCRIPT = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹⁻", "0123456789-")
POWER_OF_TEN = re.compile(r"(\d(?:[\d,]*\.?\d*)?)\s*[x×*]\s*10\s*\^?\s*(\{?[-+]?\d+\}?)")


def numbers(text: str) -> list[float]:
    """All numbers in the text, with '1.08 x 10^8' / '1.08x10⁸' forms read as 1.08e8."""
    text = text.replace("−", "-").translate(SUPERSCRIPT)
    text = POWER_OF_TEN.sub(lambda m: f"{m.group(1)}e{m.group(2).strip('{}')}", text)
    out = []
    for m in NUMBER.finditer(text):
        raw = m.group(0).replace(",", "")
        try:
            out.append(float(raw))
        except ValueError:
            pass
    return out


def has(found: list[float], want: float, tol: float) -> bool:
    return any(abs(v - want) <= tol * max(abs(want), 1e-12) for v in found)


def build_fixtures(directory: Path) -> dict[str, Path]:
    """STEP files for the DFM tasks, built with the repository's own test fixtures (needs the cad extra)."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
    import cad_fixtures as cad
    import dfm_fixtures as dfm

    paths = {
        "wall_block": cad.write_step(dfm.wall_block(1.0), directory / "wall_block.step"),
        "sheet_edge_hole": cad.write_step(dfm.sheet_with_hole_near_edge(2.0, 3.5), directory / "sheet_edge_hole.step"),
        "clean_part": cad.write_step(dfm.clean_part(), directory / "clean_part.step"),
    }
    return {k: Path(v) for k, v in paths.items()}


def tasks(fx: dict[str, Path] | None) -> list[dict]:
    t = [
        dict(id="S1_cantilever", prompt="A 4 m steel cantilever (E = 200 GPa, A = 10000 mm^2, I = 1e8 mm^4) carries a 10 kN downward point load at the free end. What is the tip deflection in mm and the fixed-end moment in kN*m?",
             nums=[([10.667, 10.67, 0.010667], 0.01), ([40.0, 40000.0], 0.01)]),
        dict(id="S2_two_span_beam", prompt="A continuous steel beam rests on three supports (pin at x=0, roller at x=4 m, roller at x=8 m) with a uniform downward load of 5 kN/m over both spans. E = 200 GPa, I = 1e8 mm^4, A = 10000 mm^2. What are the three support reactions in kN?",
             nums=[([25.0, 25000.0], 0.01), ([7.5, 7500.0], 0.01)]),
        dict(id="S3_truss", prompt="A planar truss has pins at A (0,0), B (4 m,0) and C (2 m,2 m). Bars AB, AC and BC all have E = 200 GPa and A = 500 mm^2. A is pinned, B is a roller (vertical support only), and a 10 kN load acts downward at C. Give the axial force in each bar in kN and say tension or compression.",
             nums=[([7.071, 7071.0], 0.01), ([5.0, 5000.0], 0.01)], words=["compress", "tension|tensile"]),
        dict(id="S4_column", prompt="A pin-ended steel column is 3 m long, E = 200 GPa, yield strength 250 MPa, A = 1000 mm^2, I = 1e6 mm^4. What is its Euler buckling load and which formula governs its capacity?",
             nums=[([219.3, 219324.0], 0.01), ([178.8, 178758.0], 0.01)], words=["johnson"]),
        dict(id="S5_stress", prompt="At a point sigma_x = 80 MPa, sigma_y = -20 MPa and tau_xy = 30 MPa (plane stress). What are the principal stresses and the maximum in-plane shear stress?",
             nums=[([88.3, 88.31], 0.005), ([-28.3, -28.31], 0.01), ([58.3, 58.31], 0.005)]),
        dict(id="S6_section", prompt="A welded I-section has flanges 150 mm wide and 15 mm thick, total height 300 mm and a 10 mm web. What is its second moment of area about the strong axis (in mm^4) and its area in mm^2?",
             nums=[([1.0787e8, 1.0787e-4, 107865000.0], 0.002), ([7200.0, 0.0072], 0.002)]),
        dict(id="S7_frequency", prompt="A steel cantilever 1 m long (E = 200 GPa, density 7850 kg/m^3, A = 1000 mm^2, I = 1e6 mm^4) is clamped at one end. What is its first bending natural frequency in Hz?",
             nums=[([89.32, 89.3], 0.003)]),
        dict(id="S8_3d_axis", prompt="A 2 m steel cantilever along global x has E = 200 GPa, G = 80 GPa, A = 1000 mm^2, Iy = 1e6 mm^4, Iz = 2e6 mm^4, J = 1e6 mm^4 (default local axes: local y = global y). A 1 kN force acts at the tip in global +y. What is the tip deflection in global y in mm?",
             nums=[([6.667, 6.67], 0.003)]),
        dict(id="S9_buckle_refusal", prompt="A 2 m steel pin-ended bar (E = 200 GPa, A = 1000 mm^2, Iy = 1e6 mm^4, Iz = 2e6 mm^4, J = 1e6 mm^4, G = 80 GPa) is loaded in TENSION by 1 kN. What is its linear buckling load factor?",
             words=["no compress|not compress|tension|cannot buckle|does not buckle|no buckling|refus"]),
    ]
    t += [
        dict(id="H1_portal_sway", prompt="A steel portal frame: columns 3 m high fixed at the base (E = 200 GPa, A = 2000 mm^2, I = 8e6 mm^4), a 5 m beam rigidly connected at the top (A = 3000 mm^2, I = 2e7 mm^4). A 10 kN horizontal load acts at the top of the left column. What is the horizontal sway of the left column top in mm, the horizontal reaction at the left base in kN, and the left base moment in kN*m?",
             nums=[([9.17, 9.172], 0.005), ([5.008, 5.01], 0.005), ([8.27, 8.270], 0.005)], tools=True),
        dict(id="H2_portal_modal", prompt="The same steel portal frame (columns 3 m, E = 200 GPa, A = 2000 mm^2, I = 8e6 mm^4, fixed bases; beam 5 m, A = 3000 mm^2, I = 2e7 mm^4; density 7850 kg/m^3, self mass only) - what are its first two natural frequencies in Hz? Use a converged mesh.",
             nums=[([13.63, 13.6], 0.01), ([34.41, 34.4], 0.01)], tools=True),
        dict(id="H3_plate", prompt="A simply supported square steel plate, 1 m x 1 m, 10 mm thick, E = 200 GPa, nu = 0.3, carries a uniform pressure of 1 kPa. What is the centre deflection in mm?",
             nums=[([0.2218, 0.222], 0.003)], tools=True),
        dict(id="H4_pipe_network", prompt="Water network (dynamic viscosity 1 mPa*s, density 1000 kg/m^3): reservoir R at 100 m head feeds node A through pipe P1 (300 m, 200 mm, roughness 0.1 mm). A connects to B by P2 (250 m, 150 mm) and to C by P3 (300 m, 150 mm); B connects to D by P4 (200 m, 150 mm), C to D by P5 (250 m, 150 mm); all with roughness 0.1 mm, all nodes at elevation 0. Demands: A 30 L/s, B 40 L/s, C 20 L/s, D 30 L/s. Use Darcy-Weisbach with Colebrook. What are the flows in P2 and P3 in L/s, and the head at D?",
             nums=[([49.1, 49.10], 0.005), ([40.9, 40.90], 0.005), ([67.93, 67.9], 0.003)], tools=True),
        dict(id="H5_oblique_shock", prompt="Air (gamma 1.4) at Mach 2 is turned by a 10 degree wedge. What is the attached oblique shock angle (weak solution) in degrees and the Mach number behind it?",
             nums=[([39.31, 39.3], 0.003), ([1.641, 1.64], 0.003)], tools=True),
        dict(id="H6_channel_profile", prompt="A rectangular channel 3 m wide, Manning n = 0.013, bed slope 0.001, carries 6 m^3/s. The depth at a downstream control is 1.8 m. What is the water depth 500 m upstream, and what is the profile type?",
             nums=[([1.45, 1.450], 0.003)], words=["M1"], tools=True),
        dict(id="H7_pump_point", prompt="A pump has the measured curve (Q, H, efficiency): (0, 20 m, 50%), (0.05 m^3/s, 15 m, 70%), (0.1 m^3/s, 5 m, 60%), interpolate linearly. It feeds a system with static head 5 m and friction H = 1000*Q^2 (Q in m^3/s, H in m). What is the operating flow in m^3/s and head in m?",
             nums=[([0.0732, 0.07321], 0.003), ([10.36, 10.359], 0.003)], tools=True),
    ]
    if fx:
        t += [
            dict(id="D1_milling", prompt=f"Please do a DFM review for CNC milling of the part in {fx['wall_block']} (STEP, millimetres). We have a 3 mm radius end mill and want at least 2 mm walls. Which problems do you find?",
                 words=["wall", "corner"], nums=[([1.0], 0.01)]),
            dict(id="D2_sheet", prompt=f"Check the sheet metal part {fx['sheet_edge_hole']} for manufacturability. Any problems with the hole near the edge?",
                 words=["hole", "edge"]),
            dict(id="D3_unsupported", prompt=f"Can you check {fx['clean_part']} for injection molding manufacturability (draft, undercuts)?",
                 words=["not supported|unsupported|cannot|can't|not available|isn't supported|not able|沒有|不支援|not (tool-)?checked|not covered|no tool|outside"]),
        ]
    return t


def score(task: dict, answer: str) -> tuple[int, int, list[str]]:
    found = numbers(answer)
    got = total = 0
    notes = []
    for alternatives, tol in task.get("nums", []):
        total += 1
        if any(has(found, a, tol) for a in alternatives):
            got += 1
        else:
            notes.append(f"missing {alternatives[0]}")
    for pattern in task.get("words", []):
        total += 1
        if re.search(pattern, answer, re.I):
            got += 1
        else:
            notes.append(f"missing word /{pattern}/")
    return got, total, notes


def run_one(task: dict, condition: str, args, rep: int) -> dict:
    cmd = ["claude", "-p", "--model", args.model, "--mcp-config", args.mcp_config, "--strict-mcp-config",
           "--allowedTools", "mcp__eng-toolkit,Skill,Read", "--output-format", "stream-json", "--verbose"]
    if condition == "plugin":
        cmd += ["--plugin-dir", args.plugin_dir]
    cmd += [task["prompt"]]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=args.timeout, stdin=subprocess.DEVNULL, encoding="utf-8", errors="replace")
        data, used = {}, []
        for line in proc.stdout.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if obj.get("type") == "result":
                data = obj
            elif obj.get("type") == "assistant":
                for block in (obj.get("message") or {}).get("content") or []:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        used.append(block.get("name", ""))
        answer = data.get("result", proc.stdout[-2000:])
        turns, cost = data.get("num_turns"), data.get("total_cost_usd")
    except Exception as exc:  # noqa: BLE001 - report every failure as a failed run
        answer, turns, cost, used = f"RUN FAILED: {exc}", None, None, []
    got, total, notes = score(task, answer)
    if task.get("tools"):
        total += 1
        if any(n.startswith("mcp__eng-toolkit__") for n in used):
            got += 1
        else:
            notes.append("did not call any eng-toolkit tool")
    return dict(task=task["id"], condition=condition, rep=rep, got=got, total=total, notes=notes, turns=turns, cost=cost, tools=sorted(set(used)), answer=answer)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mcp-config", required=True)
    ap.add_argument("--plugin-dir", required=True)
    ap.add_argument("--model", default="haiku")
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--timeout", type=int, default=400)
    ap.add_argument("--only", default="", help="comma-separated task id prefixes")
    ap.add_argument("--no-dfm", action="store_true")
    ap.add_argument("--out", default="eval_out")
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    fx = None if args.no_dfm else build_fixtures(Path(tempfile.mkdtemp(prefix="eng_eval_")))
    selected = [t for t in tasks(fx) if not args.only or any(t["id"].startswith(p) for p in args.only.split(","))]
    jobs = [(t, c, r) for t in selected for c in ("plain", "plugin") for r in range(args.repeat)]
    with ThreadPoolExecutor(args.workers) as pool:
        results = list(pool.map(lambda j: run_one(j[0], j[1], args, j[2]), jobs))
    (out / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"{'task':22s} {'plain':>8s} {'plugin':>8s}   turns plain/plugin   tools used plain/plugin")
    sums = {"plain": [0, 0], "plugin": [0, 0]}
    for t in selected:
        row = {}
        for c in ("plain", "plugin"):
            rs = [r for r in results if r["task"] == t["id"] and r["condition"] == c]
            got, tot = sum(r["got"] for r in rs), sum(r["total"] for r in rs)
            sums[c][0] += got
            sums[c][1] += tot
            turns = [r["turns"] for r in rs if r["turns"]]
            ntool = sum(1 for r in rs if any(n.startswith("mcp__eng-toolkit__") for n in r["tools"]))
            row[c] = (f"{got}/{tot}", f"{sum(turns) / len(turns):.1f}" if turns else "-", f"{ntool}/{len(rs)}")
        print(f"{t['id']:22s} {row['plain'][0]:>8s} {row['plugin'][0]:>8s}   {row['plain'][1]:>5s}/{row['plugin'][1]:<5s}        {row['plain'][2]}/{row['plugin'][2]}")
    print(f"{'TOTAL':22s} {sums['plain'][0]}/{sums['plain'][1]:>3} {sums['plugin'][0]}/{sums['plugin'][1]:>3}")
    for r in results:
        if r["notes"]:
            print(f"  [{r['condition']}] {r['task']}: {'; '.join(r['notes'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
