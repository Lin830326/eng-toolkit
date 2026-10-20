"""digital_check against the real Yosys.  The expected line of every finding is the line of the offending statement in the
source text of the test, counted by hand (line 1 is the ``module`` line)."""

from pathlib import Path

import pytest
from digital_fixtures import SCRIPT_LINE, VERSION, artifact, commands, fn, needs_yosys, refused, strict_json

from eng_toolkit.domains.digital import tools

pytestmark = needs_yosys
check = fn(tools.check)


def rules(result):
    return [f["rule"] for f in result.data["findings"]]


def one(result, rule):
    found = [f for f in result.data["findings"] if f["rule"] == rule]
    assert len(found) == 1, result.data["findings"]
    return found[0]


def test_a_clean_design_has_no_findings():
    r = check(source="module t(input a, input b, output y);\n  assign y = a & b;\nendmodule\n", top="t")
    assert r.ok and r.data["clean"] is True and r.data["findings"] == []
    assert r.values["errors"].quantity == 0 and r.values["warnings"].quantity == 0
    strict_json(r)


def test_a_latch_inferred_by_an_incomplete_if():
    text = (
        "module t(input a, input en, output reg q);\n"  # line 1
        "  always @* begin\n"  # 2
        "    if (en) q = a;\n"  # 3: q is not assigned when en is 0
        "  end\n"
        "endmodule\n"
    )
    r = check(source=text, top="t")
    assert r.ok and r.values["errors"].quantity == 0 and r.values["warnings"].quantity == 1
    f = one(r, "latch-inferred")
    assert f["severity"] == "warning" and f["module"] == "t" and f["signal"] == "q" and f["file"] == "source"
    assert f["line"] in (2, 3)  # Yosys names the always block (line 2) or its statement; both are inside the process
    assert "latch" in f["hint"]
    assert r.data["clean"] is False


def test_a_complete_if_is_not_a_latch():
    text = "module t(input a, input en, output reg q);\n  always @* begin\n    if (en) q = a; else q = 1'b0;\n  end\nendmodule\n"
    assert check(source=text, top="t").data["findings"] == []


def test_a_combinational_loop():
    text = (
        "module t(input a, output y);\n"  # 1
        "  wire w;\n"  # 2
        "  assign w = a & y;\n"  # 3
        "  assign y = ~w;\n"  # 4
        "endmodule\n"
    )
    r = check(source=text, top="t")
    f = one(r, "combinational-loop")
    assert f["severity"] == "error" and f["module"] == "t"
    assert set(f["lines"]) == {3, 4}
    assert r.values["errors"].quantity == 1


def test_a_loop_through_a_module_boundary_is_found_after_flattening():
    text = (
        "module inv(input a, output y);\n  assign y = ~a;\nendmodule\n"  # lines 1-3
        "module t(input x, output y);\n"  # 4
        "  wire w;\n"  # 5
        "  inv u(.a(w), .y(w));\n"  # 6: the inverter feeds itself through the port connections
        "  assign y = w & x;\n"
        "endmodule\n"
    )
    r = check(source=text, top="t")
    f = one(r, "combinational-loop")
    assert "flattening" in f["message"] and f["severity"] == "error"


def test_a_syntax_error_has_file_and_line():
    text = "module t(input a, output y);\n\n  assign y = a &;\nendmodule\n"
    r = check(source=text)
    assert r.ok  # the check ran; the error is the finding
    f = one(r, "syntax-error")
    assert (f["file"], f["line"], f["severity"]) == ("source", 3, "error")
    assert r.values["errors"].quantity == 1
    assert any("stopped at an error" in w for w in r.warnings)


def test_a_syntax_error_in_a_named_file(tmp_path):
    p = tmp_path / "alu.v"
    p.write_text("module t(input a, output y);\n  assign y = a;\n  wire ;\nendmodule\n", encoding="utf-8")
    r = check(files=str(p))
    f = one(r, "syntax-error")
    assert (f["file"], f["line"]) == ("alu.v", 3)


def test_a_missing_module_and_a_wrong_port_name():
    missing = "module t(input a, output y);\n  nothere u(.a(a), .y(y));\nendmodule\n"
    r = check(source=missing, top="t")
    f = one(r, "hierarchy-missing-module")
    assert f["severity"] == "error" and "nothere" in f["message"]
    wrong = "module s(input a, output y); assign y = a; endmodule\nmodule t(input a, output y);\n  s u(.a(a), .zz(y));\nendmodule\n"
    r = check(source=wrong, top="t")
    f = one(r, "hierarchy-port-mismatch")
    assert "zz" in f["message"]


def test_undriven_and_misspelt_signals():
    text = (
        "module t(input a, output y, output z);\n"  # 1
        "  assign y = a & enabel;\n"  # 2: `enabel` was never declared: an implicit wire with no driver
        "  wire u;\n"  # 3
        "  assign z = u & a;\n"  # 4: u is declared but nothing drives it
        "endmodule\n"
    )
    r = check(source=text, top="t")
    implicit = one(r, "implicit-declaration")
    assert implicit["line"] == 2 and implicit["severity"] == "warning"
    undriven = {f["signal"]: f for f in r.data["findings"] if f["rule"] == "undriven-signal"}
    assert set(undriven) == {"enabel", "u"}
    assert undriven["enabel"]["line"] == 2 and undriven["u"]["line"] == 3  # the line of the wire's declaration or first use
    assert r.values["errors"].quantity == 0 and r.values["warnings"].quantity == 3


def test_a_wire_with_two_drivers():
    text = (
        "module t(input a, input b, output y);\n"  # 1
        "  assign y = a & b;\n"  # 2
        "  assign y = a | b;\n"  # 3
        "endmodule\n"
    )
    r = check(source=text, top="t")
    f = one(r, "multiple-drivers")
    assert f["severity"] == "error" and set(f["lines"]) == {2, 3}


def test_a_port_width_mismatch():
    text = (
        "module s(input [3:0] a, output [3:0] y); assign y = a; endmodule\n"  # 1
        "module t(input [7:0] a, output [3:0] y);\n"  # 2
        "  s u(.a(a), .y(y));\n"  # 3: an 8-bit signal into a 4-bit port
        "endmodule\n"
    )
    r = check(source=text, top="t")
    f = one(r, "width-mismatch")
    assert f["line"] == 3 and "8 bits to 4 bits" in f["message"]


def test_findings_are_sorted_errors_first():
    text = (
        "module t(input a, input en, output reg q, output y);\n"  # 1
        "  always @* if (en) q = a;\n"  # 2: latch (warning)
        "  wire w;\n"  # 3
        "  assign w = a & y;\n"  # 4
        "  assign y = ~w;\n"  # 5: loop (error)
        "endmodule\n"
    )
    r = check(source=text, top="t")
    severities = [f["severity"] for f in r.data["findings"]]
    assert severities == sorted(severities, key=lambda s: s != "error")
    assert severities[0] == "error" and "warning" in severities


def test_the_script_has_only_fixed_commands_and_no_caller_text():
    r = check(source="// UNIQUE_MARKER_5d1e\nmodule t(input a, output y);\n  assign y = a;\nendmodule\n", top="t")
    lines = commands(r)
    assert all(SCRIPT_LINE.match(line) for line in lines), lines
    assert "UNIQUE_MARKER" not in "\n".join(lines)
    assert any(line.startswith("check") for line in lines)
    assert artifact(r, "findings.json").read_text(encoding="utf-8").strip() == "[]"


def test_top_is_optional_and_validated():
    text = "module a(input x, output y); assign y = x; endmodule\nmodule b(input x, output y); assign y = ~x; endmodule\n"
    assert check(source=text).data["clean"] is True
    refused(check(source=text, top="zzz"), "input.invalid")
    refused(check(source=text, top="a; shell dir"), "input.invalid")
    refused(check(), "input.invalid")


def test_the_yosys_that_checked_is_named():
    r = check(source="module t; endmodule\n", top="t")
    assert r.data["yosys"]["version"] == VERSION
    assert any("not a sign-off" in w for w in r.warnings)
