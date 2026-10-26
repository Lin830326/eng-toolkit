"""digital_synthesize against the real Yosys.  Every expected number is derived by hand in a comment; the unit-gate area is
the table of ``metrics.UNIT_GATE_AREA`` applied to hand-counted cells (NOT/AND/OR/NAND/NOR 1, XOR/XNOR 2, MUX 3, flip-flop 5)."""

import json
from pathlib import Path

import pytest
from digital_fixtures import AND2, FULL_ADDER, SCRIPT_LINE, VERSION, artifact, commands, fn, job_dir, needs_yosys, refused, strict_json

from eng_toolkit.domains.digital import tools

pytestmark = needs_yosys
synth = fn(tools.synthesize)
equivalent = fn(tools.equivalent)

ALL_TARGETS = ("structural", "optimized", "abc_simple", "abc_gates", "abc_aig", "abc_cmos2", "abc_nand")


def q(result, name):
    return result.values[name].quantity


def test_one_and_gate():
    # assign y = a & b;  ->  one $_AND_.  Wires: ports a, b, y and nothing else = 3 (3 wire bits).  Area 1.  Path a -> y crosses 1 gate.
    for target in ("structural", "optimized"):
        r = synth(top="t", source=AND2, target=target)
        assert r.ok, r.error
        assert r.data["cells_by_type"] == {"$_AND_": 1}
        assert q(r, "cells") == 1 and q(r, "wires") == 3 and q(r, "wire_bits") == 3
        assert q(r, "area_unit_gates") == 1.0 and q(r, "levels") == 1
        assert r.value.quantity == 1 and r.review_status == "unreviewed"
        strict_json(r)


def test_four_bit_bitwise_and_is_four_and_gates():
    # y = a & b on 4 bits: one AND per bit = 4 cells, area 4, still one level.  Wires: a, b, y (3 wires of 4 bits = 12 wire bits).
    text = "module t(input [3:0] a, input [3:0] b, output [3:0] y);\n  assign y = a & b;\nendmodule\n"
    for target in ("structural", "optimized"):
        r = synth(top="t", source=text, target=target)
        assert r.data["cells_by_type"] == {"$_AND_": 4}
        assert q(r, "cells") == 4 and q(r, "wires") == 3 and q(r, "wire_bits") == 12
        assert q(r, "area_unit_gates") == 4.0 and q(r, "levels") == 1


def test_full_adder_structural_count_is_exact():
    # sum = a ^ b ^ cin: two $_XOR_.  cout = (a&b) | (a&cin) | (b&cin): three $_AND_ and two $_OR_.  7 cells in all.
    # Area = 3 AND x 1 + 2 OR x 1 + 2 XOR x 2 = 9.
    # Wires: 5 ports (a, b, cin, sum, cout) + 5 internal (the three ANDs, the first OR, the first XOR) = 10.
    # Longest path: the OR chain ((ab | a.cin) | b.cin) is AND, OR, OR = 3 gates; the XOR chain is 2.
    r = synth(top="fa", source=FULL_ADDER, target="structural")
    assert r.data["cells_by_type"] == {"$_AND_": 3, "$_OR_": 2, "$_XOR_": 2}
    assert q(r, "cells") == 7 and q(r, "area_unit_gates") == 9.0 and q(r, "levels") == 3 and q(r, "wires") == 10


def test_full_adder_optimized_count_depends_on_abc_and_is_repeatable():
    # With ABC (Yosys 0.70) the same function is 4 $_NAND_ + 1 $_OR_ + 1 $_XNOR_ = 6 cells, area 4 + 1 + 2 = 7, 3 levels.
    # That count is ABC's choice, not arithmetic: it may differ in another Yosys or ABC.  What is exact for any version:
    # it is never more than the 7 of the structural form, and two runs on one install agree to the cell.
    first = synth(top="fa", source=FULL_ADDER, target="optimized")
    second = synth(top="fa", source=FULL_ADDER, target="optimized")
    assert first.data["cells_by_type"] == second.data["cells_by_type"]
    assert q(first, "area_unit_gates") == q(second, "area_unit_gates") and q(first, "levels") == q(second, "levels")
    assert artifact(first, "netlist.v").read_bytes() == artifact(second, "netlist.v").read_bytes()
    assert q(first, "cells") <= 7
    if VERSION and VERSION.startswith("0.70"):
        assert first.data["cells_by_type"] == {"$_NAND_": 4, "$_OR_": 1, "$_XNOR_": 1}
        assert q(first, "cells") == 6 and q(first, "area_unit_gates") == 7.0 and q(first, "levels") == 3


@pytest.mark.parametrize("target", ALL_TARGETS)
def test_every_target_is_repeatable_and_the_netlist_is_the_same_function(target, tmp_path):
    a = synth(top="fa", source=FULL_ADDER, target=target)
    b = synth(top="fa", source=FULL_ADDER, target=target)
    assert a.ok and b.ok, (a.error, b.error)
    assert a.data["cells_by_type"] == b.data["cells_by_type"]
    assert q(a, "cells") == sum(a.data["cells_by_type"].values())
    assert a.data["unrated_cells"] == {}
    # the synthesised netlist, read back as a design, is equivalent to the source (all 8 input combinations, by SAT)
    netlist = artifact(a, "netlist.v")
    proof = equivalent(top_a="fa", source_a=FULL_ADDER, files_b=str(netlist), cycles=0)
    assert proof.ok, proof.error
    assert proof.data["verdict"] == "equivalent" and proof.data["equivalent"] is True


def test_the_library_targets_use_only_their_own_cells():
    allowed = {
        "abc_simple": {"$_AND_", "$_OR_", "$_XOR_", "$_MUX_", "$_NOT_"},
        "abc_gates": {"$_AND_", "$_NAND_", "$_OR_", "$_NOR_", "$_XOR_", "$_XNOR_", "$_ANDNOT_", "$_ORNOT_", "$_NOT_"},
        "abc_aig": {"$_AND_", "$_NAND_", "$_OR_", "$_NOR_", "$_ANDNOT_", "$_ORNOT_", "$_NOT_"},
        "abc_cmos2": {"$_NAND_", "$_NOR_", "$_NOT_"},
        "abc_nand": {"$_NAND_", "$_NOT_"},
    }
    for target, cells in allowed.items():
        r = synth(top="fa", source=FULL_ADDER, target=target)
        assert set(r.data["cells_by_type"]) <= cells, (target, r.data["cells_by_type"])
        assert r.data["target_meaning"]


def test_a_two_to_one_mux_is_one_mux_cell():
    # y = s ? a : b is one $_MUX_: 1 cell, area 3, 1 level, wires s a b y = 4.
    text = "module m(input s, input a, input b, output y);\n  assign y = s ? a : b;\nendmodule\n"
    r = synth(top="m", source=text, target="structural")
    assert r.data["cells_by_type"] == {"$_MUX_": 1}
    assert q(r, "area_unit_gates") == 3.0 and q(r, "levels") == 1 and q(r, "wires") == 4


def test_a_register_counts_as_a_flip_flop_and_cuts_the_path():
    # A 4-bit register that loads its input: 4 $_DFF_P_ and nothing else.  Area 4 x 5 = 20.
    # The longest combinational path is input -> flip-flop D, no gate between: 0 levels.
    text = "module r(input clk, input [3:0] d, output reg [3:0] q);\n  always @(posedge clk) q <= d;\nendmodule\n"
    r = synth(top="r", source=text, target="structural")
    assert r.data["cells_by_type"] == {"$_DFF_P_": 4}
    assert q(r, "area_unit_gates") == 20.0 and q(r, "levels") == 0


def test_the_hierarchy_is_flattened():
    # Two instances of an AND leave exactly two $_AND_ in the top module.
    text = (
        "module g(input a, input b, output y);\n  assign y = a & b;\nendmodule\n"
        "module top(input a, input b, input c, output y);\n  wire w;\n  g u1(.a(a), .b(b), .y(w));\n  g u2(.a(w), .b(c), .y(y));\nendmodule\n"
    )
    r = synth(top="top", source=text, target="structural")
    assert r.data["cells_by_type"] == {"$_AND_": 2} and q(r, "levels") == 2


def test_source_from_files_and_labels(tmp_path):
    lib = tmp_path / "lib.v"
    lib.write_text("module g(input a, input b, output y);\n  assign y = a & b;\nendmodule\n", encoding="utf-8")
    top = tmp_path / "top.v"
    top.write_text("module top(input a, input b, output y);\n  g u(.a(a), .b(b), .y(y));\nendmodule\n", encoding="utf-8")
    r = synth(top="top", files=f"{top};{lib}", target="structural")
    assert r.ok, r.error
    assert r.data["cells_by_type"] == {"$_AND_": 1}
    assert [s["file"] for s in r.data["sources"]] == ["top.v", "lib.v"]
    assert [Path(f.path).name for f in r.provenance.input_files] == ["top.v", "lib.v"]
    assert all(len(f.sha256) == 64 for f in r.provenance.input_files)
    # a JSON list and a newline list name the same files
    again = synth(top="top", files=json.dumps([str(top), str(lib)]), target="structural")
    assert again.data["cells_by_type"] == r.data["cells_by_type"]
    broken = tmp_path / "broken.v"
    broken.write_text("module b(input a, output y);\n\nassign y = a &;\nendmodule\n", encoding="utf-8")
    bad = synth(top="b", files=str(broken))
    refused(bad, "digital.source_error")
    assert bad.error.details["file"] == "broken.v" and bad.error.details["line"] == 3 and "broken.v line 3" in bad.error.message


def test_artifacts_and_the_script_that_ran():
    marker = "UNIQUE_MARKER_91c4"
    text = f"// {marker}\n" + AND2
    r = synth(top="t", source=text)
    names = {Path(a.path).name for a in r.artifacts}
    assert {"inputs.json", "yosys-script.ys", "yosys-log-tail.txt", "netlist.v", "netlist.json", "stat.json"} <= names
    assert "module t" in artifact(r, "netlist.v").read_text(encoding="utf-8")
    assert json.loads(artifact(r, "netlist.json").read_text(encoding="utf-8"))["modules"]["t"]
    assert "Executing" in artifact(r, "yosys-log-tail.txt").read_text(encoding="utf-8")
    lines = commands(r)
    assert lines and all(SCRIPT_LINE.match(line) for line in lines), lines
    assert marker not in "\n".join(lines)  # the caller's text is the content of src0.v and is in no command
    assert marker in (job_dir(r) / "yosys" / "src0.v").read_text(encoding="utf-8")
    assert all(len(a.sha256) == 64 for a in r.artifacts)
    log = artifact(r, "yosys-log-tail.txt").read_text(encoding="utf-8")
    assert str(job_dir(r)) not in log and "<job>" in log or "Parsing Verilog" in log


def test_provenance_and_data_name_the_yosys_that_ran():
    r = synth(top="t", source=AND2)
    assert r.data["yosys"]["version"] == VERSION
    assert r.data["yosys"]["limits"]["timeout_s"] == 30.0 and r.data["yosys"]["limits"]["memory_mb"] == 1024
    assert r.provenance.engine == "eng_toolkit.digital.synthesize"
    assert any("not a sign-off" in w for w in r.warnings)
    assert r.data["area_model"]["cells"]["$_XOR_"] == 2.0
    assert r.data["longest_path"][0] == "a" or r.data["longest_path"][0] == "b"


def test_a_cell_type_outside_the_area_table_is_counted_and_reported():
    # A black-box module is a cell the table cannot rate: 1 $_AND_ (rated, area 1) + 1 box = 2 cells, area 1, the box listed.
    text = (
        "(* blackbox *) module bb(input a, output y);\nendmodule\n"
        "module z(input a, input b, output y);\n  wire w;\n  bb u(.a(a), .y(w));\n  assign y = w & b;\nendmodule\n"
    )
    r = synth(top="z", source=text, target="structural")
    assert r.ok, r.error
    assert r.data["unrated_cells"] == {"bb": 1}
    assert q(r, "cells") == 2 and q(r, "area_unit_gates") == 1.0
    assert any("area table does not rate" in w and "bb" in w for w in r.warnings)


def test_a_combinational_loop_is_synthesised_but_its_path_length_is_withheld():
    text = "module l(input a, output y);\n  wire w;\n  assign w = a & y;\n  assign y = ~w;\nendmodule\n"
    r = synth(top="l", source=text, target="structural")
    assert r.ok, r.error
    assert "levels" not in r.values
    assert any("combinational loop" in w for w in r.warnings)
    assert r.data["longest_path"] == []


def test_input_errors():
    refused(synth(top="nope", source=AND2), "input.invalid")
    refused(synth(top="t", source=AND2, target="best"), "input.invalid")
    refused(synth(top="t", source=AND2, language="vhdl"), "input.invalid")
    refused(synth(top="t"), "input.invalid")
    refused(synth(top="t", source=AND2, timeout_seconds=0), "input.out_of_range")
    refused(synth(top="t", source=AND2, timeout_seconds=100000), "input.out_of_range")
    refused(synth(top="t", files=str(Path("no-such-file.v"))), "input.file_not_found")
    refused(synth(top="t", files="x.txt"), "input.file_not_found")
    refused(synth(top="t", files=";".join(f"f{i}.v" for i in range(21))), "input.out_of_range")
    refused(synth(top="t", files="[not json"), "input.invalid")


def test_a_wrong_file_type_is_refused(tmp_path):
    p = tmp_path / "x.txt"
    p.write_text(AND2, encoding="utf-8")
    refused(synth(top="t", files=str(p)), "input.unsupported_format")


def test_a_syntax_error_names_the_line():
    r = synth(top="t", source="module t(input a, output y);\n\n  assign y = a &;\nendmodule\n")
    refused(r, "digital.source_error")
    assert r.error.details["file"] == "source" and r.error.details["line"] == 3
    assert "source line 3" in r.error.message


def test_systemverilog_subset_is_selectable():
    text = "module t(input logic a, input logic b, output logic y);\n  assign y = a & b;\nendmodule\n"
    r = synth(top="t", source=text, language="systemverilog")
    assert r.ok, r.error
    assert r.data["language"] == "systemverilog" and r.data["cells_by_type"] == {"$_AND_": 1}
    assert "read_verilog -sv src0.v" in commands(r)


def test_a_200_character_workspace_path_does_not_break_abc(tmp_path, monkeypatch):
    # UAT E3-1: ABC writes <TEMP>/yosys-abc-XXXXXX/stdcells.genlib; with TEMP inside a long job folder the name passed the 260-character limit
    # of Windows and the failure was blamed on /source.  TEMP is now a short scratch folder, and a long job folder is reached through a short alias.
    long_ws = tmp_path / ("w" * (200 - len(str(tmp_path)) - 1))
    assert len(str(long_ws)) == 200
    monkeypatch.setenv("ENG_TOOLKIT_WS", str(long_ws))
    for target in ("optimized", "abc_gates"):
        r = synth(top="fa", source=FULL_ADDER, target=target)
        assert r.ok, r.error
        assert q(r, "cells") > 0 and r.review_status == "unreviewed"


def test_a_relative_workspace_path_works_for_yosys(tmp_path, monkeypatch):
    # UAT item 1: "Can't open log file <job>\yosys.log" when ENG_TOOLKIT_WS was relative
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ENG_TOOLKIT_WS", "relative-ws")
    r = synth(top="t", source=AND2, target="optimized")
    assert r.ok, r.error
    assert Path(r.data["job_dir"]).is_absolute() and (tmp_path / "relative-ws").is_dir()


def test_an_engine_that_cannot_write_its_own_file_is_not_blamed_on_the_source():
    from eng_toolkit.domains.digital.flow import _WRITE_FAILURE, _write_failure

    said = "Opening <job>/tmp/yosys-abc-ltfvTV/stdcells.genlib for writing failed: No such file or directory"
    assert _WRITE_FAILURE.search(said) and _WRITE_FAILURE.search("Can't open log file `<job>/yosys.log' for writing!")
    failure = _write_failure(said, 1)
    assert failure.family == "engine.failure" and "param" not in failure.details
    assert "not an error in the source" in failure.message and "ENG_TOOLKIT_WS" in failure.message
