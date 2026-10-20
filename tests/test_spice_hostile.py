"""Hostile netlists through the gateway.  A fake ngspice records every start: a refused netlist must never start a program, and an accepted one must
reach the program only as the deck this toolkit wrote.  No real ngspice needed."""

import json
import os
from pathlib import Path

import pytest

import exe_fixtures as F
from eng_toolkit.external_catalog import NGSPICE
from spice_fixtures import DIVIDER, call

SECRET = "TOPSECRET-4711"


@pytest.fixture
def fake(tmp_path, monkeypatch) -> F.Fake:
    return F.install_fake(tmp_path, monkeypatch, env_var=NGSPICE.env_var, name="ngspice_con", version_args=["-v"], version_text="ngspice-41 fake")


def hostile_cases(tmp_path: Path) -> dict[str, tuple[str, set[str]]]:
    secret = tmp_path / "secret.txt"
    secret.write_text(f"R9 a 0 1k * {SECRET}\n", encoding="utf-8")
    base = "hostile\nV1 a 0 1\nR1 a 0 1k\n"
    refused = {"input.netlist_refused"}
    return {
        "control shell": (base + ".control\nshell dir\n.endc\n", refused),
        "control upper tab": (base + "\t.CONTROL\n\tSHELL calc\n\t.ENDC\n", refused),
        "control split by continuation": (base + ".con\n+ trol\nshell dir\n.endc\n", refused),
        "control as a continuation of an element": (base + "R2 a 0 1k\n+ .control\n", refused),
        "control mixed case with spaces": (base + "  .CoNtRoL  \n", refused),
        "include windows file": (base + ".include C:\\Windows\\win.ini\n", refused),
        "include secret by absolute path": (base + f".include {secret}\n", refused),
        "inc": (base + ".inc models.lib\n", refused),
        "lib": (base + ".lib 'models.lib' typical\n", refused),
        "lib hidden by comment trick": (base + "* ok\n.lib x y ; comment\n", refused),
        "osdi": (base + ".osdi bsimbulk.osdi\n", refused),
        "pre_osdi": (base + ".pre_osdi bsimbulk.osdi\n", refused),
        "xspice a device": (base + "A1 a b c d amod\n.model amod d_and\n", refused),
        "osdi n device": (base + "N1 a b c d nmod\n", refused),
        "write": (base + ".write out.raw\n", refused),
        "save": (base + ".save v(a)\n", refused),
        "csparam": (base + ".csparam x=1\n", refused),
        "title looks like include": (".include x\nV1 a 0 1\nR1 a 0 1k\n", refused),
        "title looks like control": (".control\nV1 a 0 1\nR1 a 0 1k\n", refused),
        "external source": (base + "V2 b 0 dc 0 external\nR2 b 0 1k\n", refused),
        "file source": (base + "V2 b 0 pwl file=data.txt\nR2 b 0 1k\n", refused),
        "command named element": (base + "shell dir\n", refused),
        "echo element": (base + "echo hi\n", refused),
        "text after end": (base + ".end\nR9 a 0 1\n", refused),
        "backslash path in a value": (base + "R2 a 0 C:\\x\n", refused),
        "unicode kelvin sign": (base + "R2 a 0 1\u212a\n", refused),
        "huge component value": (base + "R2 a 0 1e308\n", {"input.out_of_range"}),
        "huge exponent": (base + "R2 a 0 1e999999999999\n", {"input.out_of_range"}),
        "huge model parameter": (base + ".model dm d is=1e40\nD1 a 0 dm\n", {"input.out_of_range"}),
        "option out of range": (base + ".options reltol=1e308\n", {"input.out_of_range"}),
        "5 MB netlist": ("big\n" + ("* " + "x" * 98 + "\n") * 50_000, {"input.too_large"}),
        "too many elements": ("many\nV1 n0 0 1\n" + "".join(f"R{i} n{i} 0 1k\n" for i in range(1001)), {"input.too_large"}),
        "expanding subcircuits": (
            "boom\n.subckt s0 a b\nR1 a b 1k\n.ends\n"
            + "".join(f".subckt s{k} a b\n" + "".join(f"X{j} a b s{k - 1}\n" for j in range(10)) + ".ends\n" for k in range(1, 9))
            + "V1 in 0 1\nX1 in 0 s8\n",
            {"input.too_large"},
        ),
        "NUL byte": (base + "R2 a 0 1k\x00\n", {"input.invalid"}),
        "control character": (base + "R2 a 0 1k\x1b[2J\n", {"input.invalid"}),
    }


async def test_every_hostile_netlist_is_refused_without_starting_a_program(tmp_path, fake):
    for label, (text, families) in hostile_cases(tmp_path).items():
        for tool, extra in (("spice_simulate", {"analysis": "op"}), ("spice_check_netlist", {})):
            r = await call(tool, {**extra, "netlist": text})
            assert not r.ok, (label, tool)
            assert r.error.family in families, (label, tool, r.error)
            assert r.review_status == "unreviewed" and not r.values and r.value is None
            blob = json.dumps(r.model_dump(mode="json"))
            assert SECRET not in blob, (label, tool)
            if r.error.family == "input.netlist_refused":
                assert r.error.message.startswith("line "), (label, r.error.message)
                assert r.error.details["line"] >= 1 and len(r.error.details["directive"]) <= 40
            assert len(r.error.message) < 600, label
    assert fake.runs() == [], "a refused netlist started a program"


async def test_a_transient_of_a_trillion_points_never_starts_a_program(fake):
    r = await call("spice_simulate", {"analysis": "tran", "netlist": DIVIDER, "step": "1 us", "stop": "1e6 s"})
    assert not r.ok and r.error.family == "input.out_of_range" and r.error.details["points"] >= 1e12
    assert fake.runs() == []


async def test_hostile_files_are_refused_the_same_way(tmp_path, fake):
    cases = {
        "non-utf8 bytes": (b"t\nV1 a 0 1\nR1 a 0 \xff\xfe1k\n", "input.invalid"),
        "latin-1 title": (b"t \xe9\xe8\nV1 a 0 1\nR1 a 0 1k\n", "input.invalid"),
        "nul": (b"t\nV1 a 0 1\x00\nR1 a 0 1k\n", "input.invalid"),
        "control": (b"t\nV1 a 0 1\nR1 a 0 1k\n.control\nshell dir\n.endc\n", "input.netlist_refused"),
        "five megabytes": (b"t\n" + b"* " + b"x" * 5_000_000 + b"\n", "input.too_large"),
        "title include": (b".include C:\\Windows\\win.ini\nV1 a 0 1\nR1 a 0 1k\n", "input.netlist_refused"),
    }
    for label, (payload, family) in cases.items():
        path = tmp_path / f"{label.replace(' ', '_')}.cir"
        path.write_bytes(payload)
        r = await call("spice_simulate", {"analysis": "op", "netlist_file": str(path)})
        assert not r.ok and r.error.family == family, (label, r.error)
        if label.startswith("non-utf8") or label.startswith("latin"):
            assert "UTF-8" in r.error.message
    bad_ext = tmp_path / "x.spiceinit"
    bad_ext.write_text("shell dir", encoding="utf-8")
    r = await call("spice_simulate", {"analysis": "op", "netlist_file": str(bad_ext)})
    assert not r.ok and r.error.family == "input.unsupported_format"
    r = await call("spice_simulate", {"analysis": "op", "netlist_file": str(tmp_path / "missing.cir")})
    assert not r.ok and r.error.family == "input.file_not_found"
    assert fake.runs() == []


async def test_dangerous_words_in_comments_are_dropped_not_refused_and_never_sent(tmp_path, fake):
    text = "t .include x\n* .control shell dir .endc\nV1 a 0 1 ; .include C:\\Windows\\win.ini\nR1 a 0 1k $ .lib foo\nR2 a 0 1k // .osdi x\n"
    r = await call("spice_check_netlist", {"netlist": text})
    assert r.ok, r.error
    sent = Path(next(a.path for a in r.artifacts if a.path.endswith("netlist.cir"))).read_text(encoding="utf-8")
    assert sent.split("\n", 1)[1] == "v1 a 0 1\nr1 a 0 1k\nr2 a 0 1k\n"
    assert r.data["runnable"] is True and r.data["title_forwarded"] is False


async def test_the_deck_and_the_command_line_are_ours(tmp_path, fake, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-not-for-the-child")
    monkeypatch.setenv("ENG_TOOLKIT_SOMETHING", "internal")
    text = "T\nV1 IN 0 DC 5 ; $(whoami)\nR1 IN MID 1k\nR2 MID 0 1k\n"
    r = await call("spice_simulate", {"analysis": "op", "netlist": text, "outputs": "v(mid)"})
    assert not r.ok and r.error.family == "engine.failure"  # the fake wrote no table; what matters is what it was given
    (call_,) = fake.runs()
    job = Path(r.data["job_dir"])
    assert call_["argv"] == ["-b", "-o", str(job / "ngspice.log"), str(job / "deck.cir")]
    assert Path(call_["cwd"]).resolve() == job.resolve()
    env = call_["env"]
    assert "ANTHROPIC_API_KEY" not in env and not any(k.upper().startswith("ENG_TOOLKIT_") for k in env)
    assert Path(env["HOME"]).resolve().parent == job.resolve()
    deck = (job / "deck.cir").read_text(encoding="utf-8").split("\n")
    assert deck == [
        "eng-toolkit deck",
        "v1 in 0 dc 5",
        "r1 in mid 1k",
        "r2 mid 0 1k",
        ".control",
        "set wr_singlescale",
        "set wr_vecnames",
        "set numdgt=15",
        "op",
        "wrdata table.csv v(mid)",
        "quit",
        ".endc",
        ".end",
        "",
    ]
    assert not any(word in "\n".join(deck) for word in ("whoami", "supply"))


async def test_each_analysis_writes_only_numbers_that_came_from_validated_parameters(tmp_path, fake):
    base = {"netlist": "t\nV1 a 0 1\nR1 a b 1k\nC1 b 0 1u\n", "outputs": "v(b)"}
    cases = {
        "dc": ({"analysis": "dc", "sweep_source": "V1", "start": "0 V", "stop": "1 V", "step": "100 mV"}, "dc v1 0.0 1.0 0.1"),
        "ac": ({"analysis": "ac", "start": "1 kHz", "stop": "1 MHz", "points": 10}, "ac dec 10 1000.0 1000000.0"),
        "tran": ({"analysis": "tran", "step": "10 us", "stop": "1 ms", "use_initial_conditions": True}, "tran 1e-05 0.001 0.0 1e-05 uic"),
    }
    for name, (args, expected) in cases.items():
        r = await call("spice_simulate", {**base, **args})
        deck = (Path(r.data["job_dir"]) / "deck.cir").read_text(encoding="utf-8").split("\n")
        assert expected in deck, (name, deck)
        assert deck[deck.index(expected) + 1] == "wrdata table.csv v(b)"
    assert os.path.exists(fake.calls_path)
