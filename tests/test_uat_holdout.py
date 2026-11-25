"""The user-acceptance hold-out set (uat/scenarios/H*.json) and the runner helpers.

The hold-out scenarios exist to show that the skills and tool descriptions work on problems their authors never saw: nothing under
plugin/ or docs/ may quote their distinctive names or numbers (each scenario lists them in ``leak_tokens``).
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = sorted((ROOT / "uat" / "scenarios").glob("H*.json"))
DEV = sorted(p for p in (ROOT / "uat" / "scenarios").glob("*.json") if not p.name.startswith("H"))
REQUIRED = ("id", "persona", "goal", "opening", "facts", "files", "max_turns", "truth", "tool_check", "leak_tokens")


def load_runner():
    spec = importlib.util.spec_from_file_location("uat_run", ROOT / "scripts" / "uat_run.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["uat_run"] = module
    spec.loader.exec_module(module)
    return module


def test_there_are_seven_holdout_and_twenty_one_development_scenarios():
    assert [p.name[:2] for p in SCENARIOS] == ["H1", "H2", "H3", "H4", "H5", "H6", "H7"]
    assert len(DEV) == 21


@pytest.mark.parametrize("path", SCENARIOS, ids=lambda p: p.stem)
def test_holdout_scenario_is_complete(path):
    spec = json.loads(path.read_text(encoding="utf-8"))
    assert all(key in spec for key in REQUIRED), [k for k in REQUIRED if k not in spec]
    assert spec["id"] == path.stem and spec["set"] == "holdout"
    assert (ROOT / spec["truth"]).is_file()
    placeholders = set(re.findall(r"\{(\w+)\}", spec["opening"]))
    assert placeholders <= set(spec["files"]), "an opening placeholder has no file"
    assert spec["leak_tokens"]
    elsewhere = []
    for value in spec["files"].values():
        if Path(value).is_absolute():
            # A file of a program installed on the author's machine (a KiCad template or 3D model): it is not in the
            # repository, so on a machine without that installation the scenario cannot be staged and the check is skipped.
            if not Path(value).is_file():
                elsewhere.append(value)
            continue
        assert (ROOT / value).is_file(), f"missing input file {value}"
    if elsewhere:
        pytest.skip(f"input files of a local installation are not on this machine: {elsewhere}")


@pytest.mark.parametrize("path", SCENARIOS, ids=lambda p: p.stem)
def test_skills_and_docs_never_quote_holdout_numbers(path):
    spec = json.loads(path.read_text(encoding="utf-8"))
    hits = []
    for folder in ("plugin", "docs"):
        for file in (ROOT / folder).rglob("*"):
            if not file.is_file() or file.suffix not in (".md", ".json", ".txt", ".py", ".toml"):
                continue
            text = file.read_text(encoding="utf-8", errors="replace").lower()
            hits += [f"{file.relative_to(ROOT)}: {tok}" for tok in spec["leak_tokens"] if tok.lower() in text]
    assert not hits, hits


def test_runner_copies_native_pngs_and_skips_a_second_picture_of_the_same_name(tmp_path):
    runner = load_runner()
    job = tmp_path / "ws" / "20260101T000000Z-aaaa-plot_xy"
    job.mkdir(parents=True)
    (job / "plot.png").write_bytes(b"\x89PNG native")
    (job / "plot.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg' width='10' height='10'/>", encoding="utf-8")
    (job / "input").mkdir()
    (job / "input" / "1-photo.png").write_bytes(b"\x89PNG staged input, not a tool result")
    made = runner.rasterise(tmp_path)
    names = sorted(Path(m).name for m in made)
    assert names == ["20260101T000000Z-aaaa-plot_xy_plot.png"]
    assert (tmp_path / "rendered" / names[0]).read_bytes() == b"\x89PNG native"
    assert not list((tmp_path / "rendered").glob("*.error.txt"))


def test_svg_rasteriser_passes_a_cjk_font_when_resvg_takes_one(monkeypatch, tmp_path):
    runner = load_runner()
    calls = []

    class Fake:
        @staticmethod
        def svg_to_bytes(**kwargs):
            calls.append(kwargs)
            if "sans_serif_family" in kwargs:
                raise TypeError("unexpected keyword")  # an older resvg_py: the helper must fall back to fewer parameters
            return b"PNG"

    monkeypatch.setitem(sys.modules, "resvg_py", Fake)
    font = tmp_path / "msjh.ttc"
    font.write_bytes(b"not a real font")  # only the file name and existence matter to the helper
    monkeypatch.setattr(runner, "CJK_FONTS", [str(font)])
    assert runner.svg_to_png("<svg/>") == b"PNG"
    assert "sans_serif_family" in calls[0], "the full set of font parameters is tried first"
    assert "font_files" in calls[-1] and "sans_serif_family" not in calls[-1]
    monkeypatch.setattr(runner, "CJK_FONTS", [])
    assert runner.svg_to_png("<svg/>") == b"PNG" and "font_files" not in calls[-1]


def test_skill_summary_reports_the_first_tool_and_the_skills_loaded():
    runner = load_runner()
    turns = [{"turn": 1, "tools": [{"name": "Read", "input": "{}"}, {"name": "Skill", "input": '{"skill": "eng-toolkit:eng-circuit-sim"}'}]},
             {"turn": 2, "tools": [{"name": "mcp__eng-toolkit__spice_simulate", "input": "{}"}]}]
    summary = runner.skill_summary(turns)
    assert summary["skills_loaded"] == [{"turn": 1, "skill": "eng-toolkit:eng-circuit-sim"}]
    assert summary["first_tool"] == "Read" and summary["first_tool_before_any_skill_load"] is True
    assert summary["tool_calls_before_first_skill"] == 1
    skill_first = runner.skill_summary([{"turn": 1, "tools": [{"name": "Skill", "input": '{"skill": "x"}'}]}])
    assert skill_first["first_tool_before_any_skill_load"] is False
    none = runner.skill_summary([{"turn": 1, "tools": []}])
    assert none["first_tool"] is None and none["first_tool_before_any_skill_load"] is False


def test_persona_prompt_tells_the_person_how_pictures_reach_it():
    runner = load_runner()
    prompt = runner.SIM_PROMPT
    assert "PICTURES SHOWN TO YOU" in prompt and "bare file path" in prompt
    assert len(prompt) < 2600, "keep the persona prompt short"
