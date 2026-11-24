"""Truth for H1: is KiCad's Arduino_Nano template board ready for a fab with a 0.2 mm clearance rule?

Everything comes from kicad-cli run directly in a copy of the template folder (so the project's own footprint library table is
visible) and from plain-text reading of the .kicad_pcb file. The tightest copper gap is found by bisecting a custom clearance
rule in kicad-cli (no geometry code of ours). No eng-toolkit code is used.
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
from collections import Counter
from pathlib import Path

KICAD = Path(os.environ.get("KICAD_ROOT", r"C:/Users/USER/AppData/Local/Programs/KiCad/10.0"))
CLI = str(KICAD / "bin" / "kicad-cli.exe")
SRC = KICAD / "share" / "kicad" / "template" / "Arduino_Nano"
NAME = "Arduino_Nano"


def cli(*args, cwd):
    subprocess.run([CLI, *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600, stdin=subprocess.DEVNULL)


def drc(work, clearance=None):
    dru = work / f"{NAME}.kicad_dru"
    if clearance:
        dru.write_text(f'(version 1)\n(rule "fab_min_clearance" (constraint clearance (min {clearance}mm)))\n')
    elif dru.exists():
        dru.unlink()
    out = work / "drc.json"
    out.unlink(missing_ok=True)
    cli("pcb", "drc", "--format", "json", "--severity-all", "--units", "mm", "-o", str(out), f"{NAME}.kicad_pcb", cwd=work)
    data = json.loads(out.read_text(encoding="utf-8"))
    return dict(Counter(v["type"] + "/" + v["severity"] for v in data["violations"])), len(data["unconnected_items"]), data


def tightest_gap(work):
    """Largest clearance rule that still gives zero 'clearance' errors, bisected to 0.01 mm."""
    lo, hi = 0.0, 3.0
    for _ in range(9):
        mid = (lo + hi) / 2
        counts, _, _ = drc(work, round(mid, 4))
        if any(k.startswith("clearance/") for k in counts):
            hi = mid
        else:
            lo = mid
    return round(lo, 2)


def main():
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp) / "nano"
        shutil.copytree(SRC, work)
        project_drc, unconnected, raw = drc(work)
        at_020, _, _ = drc(work, 0.2)
        gap = tightest_gap(work)
        (work / f"{NAME}.kicad_dru").unlink(missing_ok=True)
        cli("sch", "erc", "--format", "json", "--severity-all", "-o", "erc.json", f"{NAME}.kicad_sch", cwd=work)
        erc = json.loads((work / "erc.json").read_text(encoding="utf-8"))
        erc_counts = dict(Counter(v["type"] + "/" + v["severity"] for s in erc["sheets"] for v in s["violations"]))
        cli("pcb", "export", "stats", "--format", "json", "-o", "stats.json", f"{NAME}.kicad_pcb", cwd=work)
        stats = json.loads((work / "stats.json").read_text(encoding="utf-8"))
        (work / "g").mkdir()
        cli("pcb", "export", "gerbers", "-o", "g/", f"{NAME}.kicad_pcb", cwd=work)
        cli("pcb", "export", "drill", "-o", "g/", f"{NAME}.kicad_pcb", cwd=work)
        files = sorted(p.name for p in (work / "g").iterdir())
        text = (work / f"{NAME}.kicad_pcb").read_text(encoding="utf-8")
        copper = sorted(set(re.findall(r'\(\d+ "([FB]\.Cu|In\d+\.Cu)" signal', text)))
        nets = [n for n in re.findall(r'^\t\(net (\d+) "', text, re.M) if n != "0"]
        tracks = len(re.findall(r"^\t\((?:segment|arc|via)\b", text, re.M))
        zones = len(re.findall(r"^\t\(zone\b", text, re.M))
        holes = {h["x_size"]: {"count": h["count"], "plated": h["plated"]} for h in stats["drill_holes"]}
        board = stats["board"]
        unconnected_pairs = [[i["description"] for i in u["items"]] for u in raw["unconnected_items"]]
        answers = {
            "board_file": f"{NAME}.kicad_pcb", "schematic_file": f"{NAME}.kicad_sch",
            "other_files": f"{NAME}.kicad_pro is the project/rules file; not checked",
            "drc_default_project_rules": {"violations_by_type": project_drc, "unconnected_items": unconnected,
                                          "note": "no errors, but 2 missing connections (GND pair and RESET pair between the two headers); the 4 footprint-library warnings and 5 silkscreen-clipped-by-edge warnings are not copper problems"},
            "unconnected_pairs": unconnected_pairs,
            "drc_clearance_0.20mm": {"violations_by_type": at_020, "unconnected_items": unconnected,
                                     "note": "no clearance error: the only copper is 30 header pads at 2.54 mm pitch"},
            "tightest_copper_gap_mm_by_bisection": gap,
            "erc": erc_counts,
            "footprints": stats["components"]["total"]["total"],
            "nets_excluding_net0": len(nets),
            "copper_layers": len(copper), "copper_layer_names": copper,
            "tracks_vias_in_file": tracks, "zones_in_file": zones,
            "tracks_note": "the board has no tracks, no vias and no copper pour: it is an unrouted template, copper is pads only",
            "outline_mm": [float(board["width"].split()[0]), float(board["height"].split()[0])],
            "board_thickness_mm": float(board["board_thickness"].split()[0]),
            "drill_holes": holes,
            "min_drill_mm": float(board["min_drill_diameter"].split()[0]),
            "pad_size_mm": [1.7, 1.7], "pad_drill_mm": 1.0, "annular_ring_mm": 0.35,
            "gerber_and_drill_files_kicad_writes_by_default": files, "file_count": len(files),
            "verdict": "Mechanically and by the fab's numbers it can be made (0.2 mm clearance met with a 0.84 mm gap, 2 layers, 43.18 x 17.78 mm, smallest drill 1.0 mm), but it is an unrouted template: no tracks at all and 2 connections missing (GND and RESET each link a pin on J1 to a pin on J2 with no copper between them), so as a working board it is NOT ready. 0 DRC errors does not mean the nets are connected: 2 unconnected items must be reported.",
        }
    print(json.dumps({"answers": answers,
                      "how": "kicad-cli 10 pcb drc/export and sch erc inside a copy of the template folder; the tightest copper gap by bisecting a .kicad_dru clearance rule until clearance errors appear; copper layers, nets, tracks counted in the .kicad_pcb text",
                      "tolerance": "Counts exact. Outline 43.18 x 17.78 mm within 0.05 mm. Tightest gap 0.84 mm within 0.05 mm. A verdict of 'ready' that does not mention the 2 unconnected items, or that says 0 DRC errors proves the board is wired, is wrong. Library and silkscreen warnings are noise and must not be called copper violations."},
                     indent=1))


if __name__ == "__main__":
    main()
