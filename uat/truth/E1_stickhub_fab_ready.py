"""Truth for E1: is the KiCad demo board StickHub ready for a fab with 0.15 mm / 0.3 mm limits?

Everything is read from kicad-cli run directly in a copy of the project folder (so the project's own library tables are
visible) and from plain text counting in the .kicad_pcb file. No eng-toolkit code is used.
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
SRC = KICAD / "share" / "kicad" / "demos" / "stickhub"


def cli(*args, cwd):
    subprocess.run([CLI, *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600, stdin=subprocess.DEVNULL)


def drc(work, clearance=None):
    dru = work / "StickHub.kicad_dru"
    if clearance:
        dru.write_text(f'(version 1)\n(rule "fab_min_clearance" (constraint clearance (min {clearance}mm)))\n')
    elif dru.exists():
        dru.unlink()
    out = work / "drc.json"
    cli("pcb", "drc", "--format", "json", "--severity-all", "--units", "mm", "-o", str(out), "StickHub.kicad_pcb", cwd=work)
    data = json.loads(out.read_text(encoding="utf-8"))
    return dict(Counter(v["type"] + "/" + v["severity"] for v in data["violations"])), len(data["unconnected_items"])


def main():
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp) / "stick"
        shutil.copytree(SRC, work, ignore=shutil.ignore_patterns("3dmodels"))
        project_drc, unconnected = drc(work)
        at_015, _ = drc(work, 0.15)
        at_020, _ = drc(work, 0.2)
        (work / "StickHub.kicad_dru").unlink(missing_ok=True)
        cli("sch", "erc", "--format", "json", "--severity-all", "-o", "erc.json", "StickHub.kicad_sch", cwd=work)
        erc = json.loads((work / "erc.json").read_text(encoding="utf-8"))
        erc_counts = dict(Counter(v["type"] + "/" + v["severity"] for s in erc["sheets"] for v in s["violations"]))
        cli("pcb", "export", "stats", "--format", "json", "-o", "stats.json", "StickHub.kicad_pcb", cwd=work)
        stats = json.loads((work / "stats.json").read_text(encoding="utf-8"))
        (work / "g").mkdir()
        cli("pcb", "export", "gerbers", "-o", "g/", "StickHub.kicad_pcb", cwd=work)
        cli("pcb", "export", "drill", "-o", "g/", "StickHub.kicad_pcb", cwd=work)
        files = sorted(p.name for p in (work / "g").iterdir())
        fab_layers = ("F_Cu", "B_Cu", "F_Mask", "B_Mask", "F_Silkscreen", "B_Silkscreen", "F_Paste", "B_Paste", "Edge_Cuts")
        fab_files = [f for f in files if any(f"-{k}." in f for k in fab_layers)]
        text = (work / "StickHub.kicad_pcb").read_text(encoding="utf-8")
        nets = [n for n in re.findall(r'^\t\(net (\d+) "', text, re.M) if n != "0"]
        copper = sorted(set(re.findall(r'\(\d+ "([FB]\.Cu|In\d+\.Cu)" signal', text)))
        holes = {h["x_size"]: h["count"] for h in stats["drill_holes"] if h["plated"]}
        board = stats["board"]
        answers = {
            "board_file": "StickHub.kicad_pcb", "schematic_file": "StickHub.kicad_sch",
            "other_files": "StickHub.kicad_pro is the project/rules file, RobotProtos.kicad_sym a symbol library; neither is checked",
            "drc_with_project_rules": {"violations_by_type": project_drc, "unconnected_items": unconnected,
                                       "note": "kicad-cli run inside the project folder, library tables visible: no violations at all"},
            "drc_clearance_0.15mm": {"violations_by_type": at_015},
            "drc_clearance_0.20mm": {"violations_by_type": at_020,
                                     "note": "a fab that really needed 0.2 mm would reject this board: the layout sits exactly on 0.15 mm"},
            "erc": erc_counts,
            "footprints": stats["components"]["total"]["total"],
            "nets_excluding_unconnected_net0": len(nets),
            "copper_layers": len(copper), "copper_layer_names": copper,
            "outline_mm": [float(board["width"].split()[0]), float(board["height"].split()[0])],
            "min_track_clearance_mm": float(board["min_track_clearance"].split()[0]),
            "min_track_width_mm": float(board["min_track_width"].split()[0]),
            "min_drill_mm": float(board["min_drill_diameter"].split()[0]),
            "plated_holes_by_diameter_mm": holes, "via_count": sum(holes.values()),
            "board_thickness_mm": float(board["board_thickness"].split()[0]),
            "gerber_files_for_fab": fab_files, "gerber_file_count_for_fab": len(fab_files),
            "all_gerber_and_drill_files_kicad_writes_by_default": len(files),
            "drill_files": [f for f in files if f.endswith(".drl")],
        }
    print(json.dumps({"answers": answers,
                      "how": "kicad-cli 10 pcb drc / sch erc / pcb export stats|gerbers|drill run directly in a copy of the demo project; a .kicad_dru rule 'clearance min X mm' added for the 0.15 and 0.2 mm runs; net and layer counts by text counting in the .kicad_pcb.",
                      "tolerance": "Counts exact. Violation counts at 0.2 mm may differ by a few percent between KiCad builds or whether zones are refilled; the point is: 0 at 0.15 mm, hundreds at 0.2 mm. Warnings about missing footprint/symbol libraries (e.g. 94 lib_footprint_issues, 234 ERC lib/footprint link warnings) are NOT board problems: they appear only when the libraries are not visible, and a correct answer says so. The board is a 2-layer board (a person's belief of 4 layers is wrong). Gerber set for a fab: 9 layer files plus a job file (and a zip); one Excellon drill file."},
                     ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
