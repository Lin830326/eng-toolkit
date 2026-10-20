"""What the pcb tools hand to kicad-cli, and what they do when it is not there.  A fake ``run_cli`` records the argument list, so none of this needs
KiCad: the point is that the command line is made of fixed words, files in the job's work directory and layer names checked against the board."""

import json
import re
from pathlib import Path

import pytest

from eng_toolkit import external
from eng_toolkit.domains.pcb import program
from eng_toolkit.errors import ToolFailure
from tests.pcb_testing import call, fixture

FIXED_WORDS = {
    "pcb", "sch", "drc", "erc", "export", "gerbers", "drill", "pos", "pdf", "svg", "stats",
    "--format", "json", "csv", "excellon", "--severity-all", "--units", "mm", "-o", "--refill-zones", "--layers",
    "--excellon-units", "--side", "both", "--mode-single", "--page-size-mode", "2",
}  # fmt: skip
LAYER_LIST = re.compile(r"[A-Za-z0-9_.]+(,[A-Za-z0-9_.]+)*\Z")


@pytest.fixture
def recorder(monkeypatch):
    """Replace the program: remember every argument list, write the files a real run would, and say it took no time."""
    calls: list[list] = []

    def fake_run(prep, args, **_kw):
        calls.append(list(args))
        out = prep.work / "out"
        for i, a in enumerate(args):
            if a == "-o" and i + 1 < len(args):
                target = Path(args[i + 1])
                if target.suffix == "":
                    target.mkdir(exist_ok=True)
                    (target / "board.gtl").write_bytes(b"G04*\nM02*\n")
                else:
                    target.write_text(json.dumps({"kicad_version": "fake", "violations": [], "unconnected_items": [], "sheets": []}) if target.suffix == ".json" else "x", encoding="utf-8")
        files = tuple(
            external.FileRecord(p.relative_to(prep.work).as_posix(), p.stat().st_size, "0" * 64, "created") for p in sorted(out.rglob("*")) if p.is_file()
        )
        return external.Record("fake", "env", 0, 0.0, "", "", files, False, False, {})

    monkeypatch.setattr(program, "find_kicad", lambda: external.Located("fake-kicad-cli", "env", "ENG_TOOLKIT_KICAD_CLI"))
    monkeypatch.setattr(program, "kicad_version", lambda located: "10.0.6")
    monkeypatch.setattr(program, "run_cli", fake_run)
    return calls


def assert_fixed(args, work: Path):
    for a in args:
        if isinstance(a, Path):
            assert work in a.parents or a == work, f"{a} is outside the job's work directory"
        else:
            assert a in FIXED_WORDS or LAYER_LIST.match(a), f"{a!r} is neither a fixed word nor a layer list"


@pytest.mark.asyncio
async def test_every_command_line_is_fixed_words_work_files_and_checked_layers(recorder, tmp_path):
    odd = tmp_path / "x; calc & dir $(id) `id` %PATH% 板 -o --help.kicad_pcb"
    odd.write_bytes(Path(fixture("clean.kicad_pcb")).read_bytes())
    sch = tmp_path / "evil; calc.kicad_sch"
    sch.write_bytes(Path(fixture("erc_ok.kicad_sch")).read_bytes())
    results = [
        await call("pcb_drc", board_path=str(odd), min_clearance="0.3 mm", min_track_width="0.1 mm", refill_zones=True),
        await call("pcb_erc", schematic_path=str(sch)),
        await call("pcb_export", board_path=str(odd), export="gerbers", layers="F.Cu, B.Cu"),
        await call("pcb_export", board_path=str(odd), export="drill"),
        await call("pcb_export", board_path=str(odd), export="pos"),
        await call("pcb_export", board_path=str(odd), export="pdf", layers="F.Cu"),
        await call("pcb_export", board_path=str(odd), export="svg"),
        await call("pcb_info", board_path=str(odd), check_with_kicad=True),
    ]
    assert [r.ok for r in results] == [True, True, True, True, True, True, True, True], [r.error for r in results if not r.ok]
    # drc, erc, gerbers, drill, pos, pdf, svg, then stats and pos for the cross-check
    assert len(recorder) == 9
    for r, args in zip([results[0], results[1], results[2], results[3], results[4], results[5], results[6], results[7], results[7]], recorder):
        assert_fixed(args, Path(r.data["job_dir"]) / "work")
        joined = " ".join(str(a) for a in args)
        assert "calc" not in joined and "--help" not in joined and "板" not in joined and "%PATH%" not in joined
        assert str(odd) not in joined and str(sch) not in joined  # the caller's own path never reaches the program
        assert args[-1].name in ("board.kicad_pcb", "board.kicad_sch")  # the copy under its fixed name is the last argument
    assert recorder[2][recorder[2].index("--layers") + 1] == "F.Cu,B.Cu"  # a space after the comma was stripped
    drc_args = recorder[0]
    assert drc_args[:2] == ["pcb", "drc"] and "--refill-zones" in drc_args and "--severity-all" in drc_args


@pytest.mark.asyncio
async def test_options_that_could_change_what_kicad_does_are_never_passed(recorder):
    await call("pcb_drc", board_path=fixture("clean.kicad_pcb"))
    await call("pcb_export", board_path=fixture("clean.kicad_pcb"), export="svg")
    flat = {a for args in recorder for a in args if isinstance(a, str)}
    for forbidden in ("--define-var", "-D", "--drawing-sheet", "--theme", "--save-board", "--variant", "--schematic-parity", "--exit-code-violations", "--board-plot-params"):
        assert forbidden not in flat


@pytest.mark.asyncio
async def test_the_job_directory_gets_a_fixed_name_a_english_setting_and_no_caller_file_but_the_checked_copy(recorder):
    r = await call("pcb_drc", board_path=fixture("clean.kicad_pcb"))
    work = Path(r.data["job_dir"]) / "work"
    assert (work / "board.kicad_pcb").read_bytes() == Path(fixture("clean.kicad_pcb")).read_bytes()
    settings = list((work / "home").rglob("kicad_common.json"))
    assert settings and all(json.loads(s.read_text())["system"]["language"] == "English" for s in settings)
    assert not list(work.glob("*.kicad_pro")) and not list(work.glob("*.kicad_dru")) and not list(work.rglob("*lib-table"))


# --------------------------------------------------------------------------- without KiCad


@pytest.mark.asyncio
async def test_without_kicad_the_checks_say_so_and_info_still_works(monkeypatch, tmp_path):
    monkeypatch.setenv("ENG_TOOLKIT_KICAD_CLI", str(tmp_path / "no-such-kicad-cli.exe"))
    r = await call("pcb_drc", board_path=fixture("clean.kicad_pcb"))
    assert r.ok is False and r.error.family == "engine.unavailable"
    assert "https://www.kicad.org/" in r.error.message and "ENG_TOOLKIT_KICAD_CLI" in r.error.message
    for tool, args in (("pcb_erc", {"schematic_path": fixture("erc_ok.kicad_sch")}), ("pcb_export", {"board_path": fixture("clean.kicad_pcb"), "export": "pos"})):
        assert (await call(tool, **args)).error.family == "engine.unavailable"
    info = await call("pcb_info", board_path=fixture("clean.kicad_pcb"), check_with_kicad=True)
    assert info.ok and info.values["track_count"].quantity == 3
    assert info.data["kicad_crosscheck"]["checked"] is False and any("not installed" in w for w in info.warnings)
    # a file that is not a board is refused for what it is, before anybody looks for the program
    bad = tmp_path / "bad.kicad_pcb"
    bad.write_text("nonsense", encoding="utf-8")
    assert (await call("pcb_drc", board_path=str(bad))).error.family == "input.invalid"


# --------------------------------------------------------------------------- the project file filter


def test_project_filter_keeps_numbers_severities_and_net_classes_only():
    data = {
        "meta": {"filename": "../../x.kicad_pro", "version": 3},
        "board": {
            "design_settings": {
                "rules": {"min_clearance": 0.15, "min_track_width": True, "min_via_diameter": float("nan"), "min_hole_to_hole": -1, "min_resolved_spokes": 2, "min_silk_clearance": 5000, "evil": 1},
                "rule_severities": {"clearance": "error", "track_dangling": "ignore", "x": "launch", "Bad Key!": "error", "": "error"},
                "drc_exclusions": ["clearance|1|2|abc", "bad\nline", 7, "x" * 2000],
                "defaults": {"anything": 1},
            }
        },
        "net_settings": {
            "classes": [{"name": "Default", "clearance": 0.2, "track_width": 0.25, "pcb_color": "red", "via_diameter": "big"}, {"name": 3}, "text", {"name": "ok\nname"}],
            "netclass_patterns": [{"netclass": "Default", "pattern": "GND*"}, {"netclass": 1, "pattern": "x"}],
        },
        "pcbnew": {"page_layout_descr_file": "C:/x.kicad_wks"},
        "libraries": {},
    }
    project, summary = program.sanitize_project(data, schematic=False)
    design = project["board"]["design_settings"]
    assert design["rules"] == {"min_clearance": 0.15, "min_resolved_spokes": 2}  # a bool, NaN, a negative, 5000 mm and an unknown name are dropped
    assert design["rule_severities"] == {"clearance": "error", "track_dangling": "ignore"}
    assert design["drc_exclusions"] == ["clearance|1|2|abc"]
    assert project["net_settings"]["classes"] == [{"name": "Default", "clearance": 0.2, "track_width": 0.25}]
    assert project["net_settings"]["netclass_patterns"] == [{"netclass": "Default", "pattern": "GND*"}]
    assert project["meta"] == {"filename": "board.kicad_pro", "version": 3}  # our name, not the file's
    assert set(project) == {"meta", "board", "net_settings"}
    assert summary["left_out"] == ["libraries", "pcbnew"]
    json.dumps(project, allow_nan=False)


def test_project_filter_for_a_schematic_keeps_the_erc_part_only():
    pin_map = [[0] * 12 for _ in range(12)]
    pin_map[3][4] = 2
    project, summary = program.sanitize_project(
        {"erc": {"rule_severities": {"pin_not_connected": "warning"}, "pin_map": pin_map, "erc_exclusions": ["a"]}, "board": {"design_settings": {"rules": {"min_clearance": 1}}}, "sheets": [["x", "y"]]},
        schematic=True,
    )
    assert project["erc"] == {"meta": {"version": 0}, "rule_severities": {"pin_not_connected": "warning"}, "pin_map": pin_map, "erc_exclusions": ["a"]}
    assert "board" not in project
    short, _ = program.sanitize_project({"erc": {"pin_map": [[0] * 11] * 12}}, schematic=True)
    assert "pin_map" not in short["erc"]
    with pytest.raises(ToolFailure):
        program.sanitize_project([1, 2], schematic=False)


def test_custom_rules_text_check():
    assert program.check_rules_text('(version 1)\n(rule "a" (constraint clearance (min 0.2mm)))\n# c\n(rule "b (not a list)" (condition "A.NetClass == \'x\'") (constraint track_width (min 1mm)))') == 2
    for bad, fragment in [
        ("(version 1) (import other)", "import"),
        ("(rule \"a\"", "unclosed"),
        ("(rule \"a\"))", "unbalanced"),
        ("(version 1) stray", "outside"),
        ("(version 1) (" + "(" * 100 + ")" * 100 + ")", "not valid"),
    ]:
        with pytest.raises(ToolFailure) as info:
            program.check_rules_text(bad)
        assert fragment in info.value.message


def test_a_non_json_constant_in_a_project_file_is_refused():
    with pytest.raises(ValueError):
        json.loads('{"a": NaN}', parse_constant=program._reject_constant)  # noqa: SLF001
    assert program.format_mm(0.3) == "0.3" and program.format_mm(2.0) == "2" and program.format_mm(0.0) == "0" and program.format_mm(1 / 3) == "0.333333"
