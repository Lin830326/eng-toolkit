"""digital_equivalent against the real Yosys.  Counterexamples are checked by evaluating both functions in Python, and the
step at which a sequential difference first shows is derived by hand."""

import itertools

import pytest
from digital_fixtures import AND2, OR2, SCRIPT_LINE, commands, fn, needs_yosys, refused, strict_json

from eng_toolkit.domains.digital import tools

pytestmark = needs_yosys
equivalent = fn(tools.equivalent)

MUX_ASSIGN = "module m(input s, input a, input b, output y);\n  assign y = s ? a : b;\nendmodule\n"
MUX_IF = "module m(input s, input a, input b, output reg y);\n  always @* begin\n    if (s) y = a;\n    else y = b;\n  end\nendmodule\n"
MUX_LOGIC = "module m(input s, input a, input b, output y);\n  assign y = (s & a) | (~s & b);\nendmodule\n"
MUX_BROKEN = "module m(input s, input a, input b, output y);\n  assign y = (s & a) | (s & b);\nendmodule\n"


def test_a_mux_written_three_ways_is_equivalent():
    for other in (MUX_IF, MUX_LOGIC):
        r = equivalent(top_a="m", source_a=MUX_ASSIGN, source_b=other)
        assert r.ok, r.error
        assert r.data["verdict"] == "equivalent" and r.data["equivalent"] is True and r.data["proof"] == "complete"
        assert r.data["mode"] == "combinational" and r.data["bound_cycles"] is None and r.data["counterexample"] is None
        assert r.value.quantity == 0.0 and r.warnings == r.warnings[:1]
        strict_json(r)
    r = equivalent(top_a="m", source_a=MUX_IF, source_b=MUX_LOGIC)
    assert r.data["verdict"] == "equivalent"


def test_a_broken_mux_is_not_equivalent_and_the_counterexample_is_real():
    r = equivalent(top_a="m", source_a=MUX_ASSIGN, source_b=MUX_BROKEN)
    assert r.ok and r.data["verdict"] == "not_equivalent" and r.data["equivalent"] is False and r.value.quantity == 1.0
    cex = r.data["counterexample"]
    s, a, b = cex["inputs"]["s"], cex["inputs"]["a"], cex["inputs"]["b"]
    gold = a if s else b  # y = s ? a : b
    gate = (s & a) | (s & b)  # the broken version
    assert gold != gate, cex  # the reported inputs really separate the two functions
    assert cex["gold_outputs"]["y"] == gold and cex["gate_outputs"]["y"] == gate
    assert cex["differing_outputs"] == ["y"]
    # by hand: the two differ exactly when s = 0 and b = 1 (gold gives b = 1, broken gives 0) or s = 1 and b = 1, a = 0 (gold 0, broken 1)
    assert (s, b) == (0, 1) or (s, a, b) == (1, 0, 1)


def test_and_versus_or_is_not_equivalent_and_the_counterexample_is_checked():
    r = equivalent(top_a="t", source_a=AND2, source_b=OR2)
    assert r.data["verdict"] == "not_equivalent"
    cex = r.data["counterexample"]
    a, b = cex["inputs"]["a"], cex["inputs"]["b"]
    assert (a, b) in ((1, 0), (0, 1))  # a & b and a | b differ exactly when the inputs differ
    assert (a & b) != (a | b)
    assert cex["gold_outputs"] == {"y": a & b} and cex["gate_outputs"] == {"y": a | b}
    assert cex["widths"] == {"a": 1, "b": 1, "y": 1}
    assert "steps" not in cex  # a combinational counterexample has no steps


def test_a_wide_counterexample_has_the_bits_that_matter():
    gold = "module w(input [3:0] a, input [3:0] b, output [3:0] y);\n  assign y = a & b;\nendmodule\n"
    gate = "module w(input [3:0] a, input [3:0] b, output [3:0] y);\n  assign y = {a[3] & b[3], 1'b0, a[1:0] & b[1:0]};\nendmodule\n"
    r = equivalent(top_a="w", source_a=gold, source_b=gate)
    cex = r.data["counterexample"]
    a, b = cex["inputs"]["a"], cex["inputs"]["b"]
    assert (a >> 2) & (b >> 2) & 1 == 1  # only bit 2 is wrong, so both inputs must have bit 2 set
    assert cex["gold_outputs"]["y"] == a & b
    assert cex["gate_outputs"]["y"] == (((a >> 3) & (b >> 3) & 1) << 3) | ((a & b) & 3)
    assert cex["widths"]["a"] == 4 and cex["widths"]["y"] == 4


def test_every_function_of_two_inputs_against_and():
    # exhaustive: all 16 truth tables as the second design; equivalent exactly when the table equals AND's (table 0b1000)
    for table in range(16):
        bits = [(table >> i) & 1 for i in range(4)]  # y(a=0,b=0), y(1,0), y(0,1), y(1,1)
        terms = [f"(a == {i & 1} && b == {i >> 1})" for i in range(4) if bits[i]]
        body = " || ".join(terms) if terms else "1'b0"
        text = f"module t(input a, input b, output y);\n  assign y = {body};\nendmodule\n"
        r = equivalent(top_a="t", source_a=AND2, source_b=text)
        assert r.ok, (table, r.error)
        assert (r.data["verdict"] == "equivalent") == (table == 0b1000), table
        if table != 0b1000:
            cex = r.data["counterexample"]
            a, b = cex["inputs"]["a"], cex["inputs"]["b"]
            assert bits[a | (b << 1)] != (a & b)


COUNTER = "module t(input clk, output [3:0] y);\n  reg [3:0] c;\n  always @(posedge clk) c <= c + 1;\n  assign y = c;\nendmodule\n"
# differs from COUNTER only while the count is 6: the output shows 0 there
COUNTER_GLITCH = COUNTER.replace("assign y = c;", "assign y = (c == 6) ? 4'd0 : c;")


def test_a_difference_after_cycle_5_is_found_with_eight_steps_and_not_with_three():
    # Step k (k = 0, 1, ...) shows the count after k clock edges, starting from 0: the count is k.  The glitch shows when the
    # count is 6, i.e. at step 6.  A bound of N checks steps 0 to N-1, so it finds the difference when N >= 7.
    found = equivalent(top_a="t", source_a=COUNTER, source_b=COUNTER_GLITCH, cycles=8)
    assert found.ok and found.data["verdict"] == "not_equivalent" and found.data["mode"] == "bounded" and found.data["bound_cycles"] == 8
    cex = found.data["counterexample"]
    assert cex["cycle"] == 6 and cex["first_mismatch_cycle"] == 6
    assert cex["gold_outputs"] == {"y": 6} and cex["gate_outputs"] == {"y": 0}
    assert [s["gold_outputs"]["y"] for s in cex["steps"][:7]] == [0, 1, 2, 3, 4, 5, 6]  # the counter, step by step
    assert [s["gate_outputs"]["y"] for s in cex["steps"][:7]] == [0, 1, 2, 3, 4, 5, 0]
    assert found.value.quantity == 1.0 and found.values["checked_cycles"].quantity == 8

    missed = equivalent(top_a="t", source_a=COUNTER, source_b=COUNTER_GLITCH, cycles=3)
    assert missed.ok and missed.data["mode"] == "bounded" and missed.data["bound_cycles"] == 3
    assert missed.data["verdict"] == "no_mismatch_within_bound"
    assert missed.data["equivalent"] is None and missed.data["proof"] == "bounded" and missed.data["counterexample"] is None
    assert any("BOUNDED" in w and "not a proof" in w for w in missed.warnings)
    assert missed.value.quantity == 0.0 and missed.values["checked_cycles"].quantity == 3
    strict_json(missed)


@pytest.mark.parametrize("cycles, found", [(1, False), (6, False), (7, True), (8, True), (20, True)])
def test_the_bound_is_exact(cycles, found):
    # steps 0 .. cycles-1 are checked; the first difference is at step 6, so 6 steps (0..5) miss it and 7 (0..6) find it
    r = equivalent(top_a="t", source_a=COUNTER, source_b=COUNTER_GLITCH, cycles=cycles)
    assert (r.data["verdict"] == "not_equivalent") is found
    if not found:
        assert r.data["equivalent"] is None


def test_identical_counters_show_no_mismatch_but_are_not_called_equivalent():
    r = equivalent(top_a="t", source_a=COUNTER, source_b=COUNTER, cycles=12)
    assert r.data["verdict"] == "no_mismatch_within_bound" and r.data["equivalent"] is None
    assert "equivalent" != r.data["verdict"]
    assert any("not a proof" in w for w in r.warnings)


def test_state_without_a_bound_is_refused_not_guessed():
    r = refused(equivalent(top_a="t", source_a=COUNTER, source_b=COUNTER_GLITCH, cycles=0), "input.invalid")
    assert "cycles" in r.error.message and "$dff" in r.error.details["state_cells"]
    latch = "module t(input a, input en, output reg q);\n  always @* if (en) q = a;\nendmodule\n"
    refused(equivalent(top_a="t", source_a=latch, source_b=latch), "input.invalid")


def test_an_asynchronous_reset_is_handled():
    a = "module t(input clk, input rst, input d, output reg q);\n  always @(posedge clk or posedge rst) if (rst) q <= 0; else q <= d;\nendmodule\n"
    b = "module t(input clk, input rst, input d, output reg q);\n  always @(posedge clk or posedge rst) if (rst) q <= 0; else q <= d | 1'b0;\nendmodule\n"
    r = equivalent(top_a="t", source_a=a, source_b=b, cycles=4)
    assert r.ok, r.error
    assert r.data["verdict"] == "no_mismatch_within_bound"
    c = a.replace("q <= d;", "q <= ~d;")
    r = equivalent(top_a="t", source_a=a, source_b=c, cycles=4)
    assert r.data["verdict"] == "not_equivalent"


def test_two_tops_of_one_source_and_the_same_module_names_in_both_designs():
    both = MUX_ASSIGN + MUX_LOGIC.replace("module m(", "module m2(")
    r = equivalent(top_a="m", top_b="m2", source_a=both)
    assert r.ok and r.data["verdict"] == "equivalent"
    # two separate designs that both call their top "m"
    assert equivalent(top_a="m", source_a=MUX_ASSIGN, source_b=MUX_LOGIC).data["verdict"] == "equivalent"
    # a design against itself under another top name
    refused(equivalent(top_a="m", source_a=MUX_ASSIGN), "input.invalid")


def test_the_designs_may_be_files(tmp_path):
    a = tmp_path / "a.v"
    b = tmp_path / "b.v"
    a.write_text(MUX_ASSIGN, encoding="utf-8")
    b.write_text(MUX_BROKEN, encoding="utf-8")
    r = equivalent(top_a="m", files_a=str(a), files_b=str(b))
    assert r.data["verdict"] == "not_equivalent"
    assert [f.path.split("\\")[-1].split("/")[-1] for f in r.provenance.input_files] == ["a.v", "b.v"]


def test_different_ports_are_a_refusal_that_says_so():
    other = "module t(input a, input b, output y, output z);\n  assign y = a & b;\n  assign z = a;\nendmodule\n"
    r = refused(equivalent(top_a="t", source_a=AND2, source_b=other), "digital.interface_mismatch")
    assert "port" in r.error.message


def test_a_syntax_error_in_the_second_design_names_its_file():
    r = refused(equivalent(top_a="t", source_a=AND2, source_b="module t(input a, input b, output y);\n\nassign y = a &;\nendmodule\n"), "digital.source_error")
    assert r.error.details["file"] == "source_b" and r.error.details["line"] == 3


def test_input_errors():
    refused(equivalent(top_a="t", source_a=AND2, source_b=OR2, cycles=-1), "input.out_of_range")
    refused(equivalent(top_a="t", source_a=AND2, source_b=OR2, cycles=101), "input.out_of_range")
    refused(equivalent(top_a="t", source_a=AND2, source_b=OR2, cycles=10**9), "input.out_of_range")
    refused(equivalent(top_a="nope", source_a=AND2, source_b=OR2), "input.invalid")
    refused(equivalent(top_a="t", source_a=AND2, source_b=OR2, top_b="nope"), "input.invalid")
    refused(equivalent(top_a="t", source_b=OR2), "input.invalid")
    refused(equivalent(top_a="t; shell dir", source_a=AND2, source_b=OR2), "input.invalid")


def test_the_script_has_only_fixed_commands():
    r = equivalent(top_a="t", source_a="// UNIQUE_MARKER_77aa\n" + AND2, source_b=OR2, cycles=5)
    lines = commands(r)
    assert all(SCRIPT_LINE.match(line) for line in lines), [x for x in lines if not SCRIPT_LINE.match(x)]
    assert "UNIQUE_MARKER" not in "\n".join(lines)
    assert any(line.endswith("-seq 5 -set-init-zero miter") for line in lines)
    comb = equivalent(top_a="t", source_a=AND2, source_b=OR2)
    assert not any("-seq" in line for line in commands(comb))
    assert comb.data["yosys"]["version"]


@pytest.mark.parametrize("a, b", [("gold", "gate"), ("gate", "gold"), ("miter", "miter"), ("gold", "gold")])
def test_tops_named_like_the_internal_names_do_not_collide(a, b):
    first = f"module {a}(input x, input y, output z);\n  assign z = x & y;\nendmodule\n"
    second = f"module {b}(input x, input y, output z);\n  assign z = y & x;\nendmodule\n"
    r = equivalent(top_a=a, top_b=b, source_a=first, source_b=second)
    assert r.ok, r.error
    assert r.data["verdict"] == "equivalent"
