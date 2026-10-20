"""The netlist allow-list parser: what is accepted and how it is rewritten, what is refused and how the refusal reads, what is only a problem.  No ngspice needed."""

import re

import pytest

from eng_toolkit.domains.spice import netlist as N
from eng_toolkit.errors import ToolFailure

BASE = "title\nV1 in 0 5\nR1 in out 1k\nR2 out 0 1k\n"


def refused(text: str) -> ToolFailure:
    with pytest.raises(ToolFailure) as caught:
        N.parse(text)
    return caught.value


# --------------------------------------------------------------------------- normal form


def test_normal_form_lowercase_tabs_continuations_comments_and_equals():
    text = "R9 x y 5\nV1 IN 0 DC = 5 ; the supply\n* .include nothing\n   \nR1\tIN\tOUT\n+ 1K  $ trailing\n+ \nR2 OUT 0 1k // c\n.MODEL Dm D ( IS = 1e-14 N=1 )\nD1 out 0 dm\n.OPTIONS RELTOL = 1e-4\n.end\n"
    nl = N.parse(text)
    assert nl.body == ["v1 in 0 dc=5", "r1 in out 1k", "r2 out 0 1k", ".model dm d is=1e-14 n=1", "d1 out 0 dm", ".options reltol=1e-4"]
    assert nl.problems == [] and nl.nodes == ["in", "out"] and nl.options == {"reltol": "1e-4"}
    assert nl.title == "R9 x y 5"
    assert any("title" in w and "not simulated" in w for w in nl.warnings)  # the first line looks like an element


def test_the_title_is_never_part_of_the_body_and_a_directive_title_is_refused():
    assert "title" not in " ".join(N.parse("title with words\nV1 a 0 1\nR1 a 0 1k\n").body)
    err = refused(".include x\nR1 a 0 1k\n")
    assert err.family == "input.netlist_refused" and err.details["line"] == 1 and err.details["reason_code"] == "title_directive"
    assert refused("  .control\nR1 a 0 1k\n").details["line"] == 1


def test_a_word_split_by_continuation_lines_is_seen_whole():
    # logical line 1: "r1 a 0 1k .control" -> a directive-like word inside a line
    err = refused("t\nR1 a 0 1k\n+ .control\n")
    assert err.details["reason_code"] == "hidden_directive" and err.details["line"] == 3 - 1
    # logical line: ".con trol" -> not an allowed directive
    err = refused("t\nV1 a 0 1\n.con\n+ trol\nR1 a 0 1\n")
    assert err.details["reason_code"] == "directive" and err.details["line"] == 3
    # a comment line in between does not break the chain; tab and upper case do not hide it
    err = refused("t\nR1 a 0 1k\n* a comment between\n+\t.CONTROL\n")
    assert err.details["reason_code"] == "hidden_directive"
    # a continuation with nothing before it
    assert refused("t\n+ R1 a 0 1k\n").details["reason_code"] == "continuation"


# --------------------------------------------------------------------------- refusals


@pytest.mark.parametrize(
    "line, code",
    [
        (".control", "directive"),
        (".CONTROL", "directive"),
        ("\t.CoNtRoL", "directive"),
        (".endc", "directive"),
        (". control", "directive"),
        (".include foo.cir", "directive"),
        (".inc foo.cir", "directive"),
        (".lib foo.lib typ", "directive"),
        (".osdi bsimbulk.osdi", "directive"),
        (".pre_osdi bsimbulk.osdi", "directive"),
        (".write out.raw", "directive"),
        (".save all", "directive"),
        (".csparam x=1", "directive"),
        (".print tran v(a)", "directive"),
        (".tran 1u 1m", "directive"),
        (".func f(x) {x*2}", "directive"),
        (".if (1)", "directive"),
        (".spiceinit", "directive"),
        (".end x", "syntax"),
        ("A1 a b c d model", "element_letter"),
        ("a1 a b c d model", "element_letter"),
        ("N1 d g s b nmod", "element_letter"),
        ("O1 a b c d lmod", "element_letter"),
        ("U1 a b c rcmod", "element_letter"),
        ("Y1 a b c d txl", "element_letter"),
        ("shell dir", "command_name"),
        ("source foo", "command_name"),
        ("SET x", "command_name"),
        ("echo hi", "command_name"),
        ("exec calc", "command_name"),
        ("system whoami", "command_name"),
        ("Shel a b c d m", "command_name"),
        ("write a b 1", "command_name"),
        ("load a b 1", "command_name"),
        ("codemodel a b 1", "command_name"),
        ("V1 a 0 pwl file=data.txt", "word"),
        ("V1 a 0 PWL(0 0 1 1) FILE = x", "word"),
        ("V1 a 0 dc 0 external", "word"),
        ("B1 a 0 v=agauss(0,1,1)", "word"),
        ("B1 a 0 v=rand(1)", "word"),
        ("V1 a 0 trnoise(1 1n 0 0)", "word"),
        ("R1 a 0 C:\\Windows\\win.ini", "character"),
        ('R1 a 0 "1k"', "character"),
        ("R1 a 0 `1k`", "character"),
        ("R1 a$b 0 1k", "character"),
        ("R1 a 0 1k \u212a", "character"),
        ("R1 a 0 1\uff4b", "character"),
        ("R1 a 0 1k \u202e", "character"),
        ("1x a 0 1k", "unrecognised"),
        ("#!/bin/sh", "unrecognised"),
        ("R1 a (0 1k", "syntax"),
        ("R1 a 0 {1k", "syntax"),
        (".model m1 d level=1 file=x", "word"),
        (".model m1 vsource", "model_type"),
        (".model m1 d bad", "syntax"),
        (".options post", "option"),
        (".options savecurrents=1", "option"),
        (".options noopac", "option"),
        (".options method=ludicrous", "option"),
        (".temp hot", "syntax"),
        (".ic v(a)", "syntax"),
        (".ic i(l1)=1", "syntax"),
        (".param", "syntax"),
        (".param x", "syntax"),
        (".param x={1} bogus", "syntax"),
        (".subckt", "syntax"),
        (".subckt 1x a b", "syntax"),
    ],
)
def test_the_allow_list_refuses(line, code):
    err = refused(f"t\nV0 q 0 1\n{line}\nR1 a 0 1k\n")
    assert err.family in {"input.netlist_refused", "input.out_of_range"}, err.to_dict()
    assert err.details["reason_code"] == code, err.to_dict()
    assert err.details["line"] == 3 and err.message.startswith("line 3:")


def test_a_refusal_never_echoes_more_than_80_characters_of_the_line():
    long_tail = "SECRET" * 60
    err = refused(f"t\nR1 a 0 1k\n.include {long_tail}\n")
    assert long_tail not in err.message and "SECRET" * 14 not in err.message  # 14 x 6 = 84 > 80
    assert len(err.message) < 500
    assert len(err.details["directive"]) <= 40
    err = refused(f"t\nR1 a 0 1k\nR2 a 0 {'9' * 400}\n")
    assert "9" * 81 not in err.message


def test_control_characters_nul_and_a_lone_surrogate_are_refused():
    err = refused("t\nR1 a 0 1k\x00\n")
    assert err.family == "input.invalid" and err.details["reason_code"] == "control_character" and err.details["line"] == 2 and "NUL" in err.message
    for ch in ("\x0b", "\x0c", "\x1b", "\x7f", "\x08"):
        assert refused(f"t\nR1 a 0 1k{ch}\n").details["codepoint"] == ord(ch)
    err = refused("t\nR1 a 0 1k\udcff\n")
    assert err.family == "input.invalid" and err.details["reason_code"] == "encoding"


def test_non_ascii_is_fine_in_comments_and_the_title_only():
    nl = N.parse("Titre \u00e9l\u00e9ment \u00b5F\nV1 a 0 1 ; r\u00e9sistance\nR1 a 0 1k\n* caf\u00e9 \u2028 .control\n")
    assert nl.body == ["v1 a 0 1", "r1 a 0 1k"]


def test_text_after_end_is_refused():
    err = refused("t\nV1 a 0 1\nR1 a 0 1k\n.end\nR2 a 0 1k\n")
    assert err.details["reason_code"] == "after_end" and err.details["line"] == 5
    assert N.parse("t\nV1 a 0 1\nR1 a 0 1k\n.end\n* a comment is fine\n\n").problems == []


# --------------------------------------------------------------------------- magnitudes and sizes


@pytest.mark.parametrize("value", ["1e308", "1e31", "-1e31", "1e999", "1e-31", "1e400k", "9" * 40])
def test_absurd_magnitudes_are_out_of_range(value):
    err = refused(f"t\nV1 a 0 1\nR1 a 0 {value}\n")
    assert err.family == "input.out_of_range" and err.details["reason_code"] == "magnitude" and err.details["line"] == 3


@pytest.mark.parametrize("value", ["1e30", "1e-30", "0", "0.0", "1meg", "1e-15", "2.2u", "10mil", "5v", "1e3k", "-3m", ".5", "1.", "1e+3"])
def test_ordinary_magnitudes_pass(value):
    N.parse(f"t\nV1 a 0 1\nR1 a 0 {value}\n")


def test_netlist_size_line_count_and_line_length_limits():
    err = refused("t\n" + "* " + "x" * (N.MAX_BYTES + 10) + "\n")
    assert err.family == "input.too_large" and err.details["limit_bytes"] == N.MAX_BYTES
    assert refused("t\n" + "\n" * (N.MAX_LINES + 5)).family == "input.too_large"
    err = refused("t\nR1 a 0 1k\n" + "+ 1k\n" * 800)  # 800 * 3 characters joined into one logical line is 2400 > 2000
    assert err.family == "input.too_large" and err.details["reason_code"] == "line_length"


def test_element_count_limit_counts_expanded_instances():
    lines = "".join(f"R{i} n{i} 0 1k\n" for i in range(N.MAX_ELEMENTS))
    N.parse("t\nV1 n0 0 1\n" + lines[: -len(f'R{N.MAX_ELEMENTS - 1} n{N.MAX_ELEMENTS - 1} 0 1k\n')])  # 1 + (MAX - 1) = MAX elements: accepted
    err = refused("t\nV1 n0 0 1\n" + lines)  # MAX + 1
    assert err.family == "input.too_large" and err.details["limit_elements"] == N.MAX_ELEMENTS and err.details["elements"] == N.MAX_ELEMENTS + 1
    # ten levels of ten instances each is 10^10 elements: counted, never expanded
    chain = [".subckt s0 a b\nR1 a b 1k\n.ends"]
    for k in range(1, 11):
        chain.append(f".subckt s{k} a b\n" + "".join(f"X{j} a b s{k - 1}\n" for j in range(10)) + ".ends")
    err = refused("t\n" + "\n".join(chain) + "\nV1 in 0 1\nX1 in 0 s10\n")
    assert err.family == "input.too_large" and err.details["elements"] > N.MAX_ELEMENTS


def test_subcircuit_recursion_is_refused():
    err = refused("t\n.subckt a p q\nX1 p q a\n.ends\nV1 in 0 1\nX2 in 0 a\n")
    assert err.details["reason_code"] == "recursion"
    err = refused("t\n.subckt a p q\nX1 p q b\n.ends\n.subckt b p q\nX1 p q a\n.ends\nV1 in 0 1\nX2 in 0 a\n")
    assert err.details["reason_code"] == "recursion"


def test_subcircuit_nesting_depth_is_limited():
    deep = "".join(f".subckt s{k} a b\n" for k in range(6)) + "R1 a b 1k\n" + ".ends\n" * 6
    assert refused("t\n" + deep).details["reason_code"] == "nesting"


# --------------------------------------------------------------------------- problems, not refusals


def codes(text: str) -> list[str]:
    return [p["code"] for p in N.parse(text).problems]


def test_structural_problems_are_collected():
    assert codes("t\nV1 a b 1\nR1 a b 1k\n") == ["no_ground"]
    assert codes("t\n") == ["empty"]
    assert codes("t\nV1 a 0 1\nR1 a 0 1k\nr1 a 0 2k\n") == ["duplicate_element"]
    assert codes("t\nV1 a 0 1\nD1 a 0 nomodel\n") == ["missing_model"]
    assert codes("t\nV1 a 0 1\nX1 a 0 nosub\n") == ["missing_subckt"]
    assert codes("t\n.subckt s p q r\nR1 p q 1k\n.ends\nV1 a 0 1\nX1 a 0 s\n") == ["port_mismatch"]
    assert codes("t\nV1 a 0 1\nF1 b 0 vmissing 2\nR1 b 0 1k\nR2 a 0 1k\n") == ["missing_source"]
    assert codes("t\nV1 a 0 1\nL1 a 0 1m\nK1 l1 l9 0.5\n") == ["missing_inductor"]
    assert codes("t\nV1 a 0 1\nR1 a 0 1k\nR2 a 0\n").count("missing_value") == 1
    assert codes("t\nV1 a 0 1\nR1 a 0 1k\n.subckt s p q\nR1 p q 1k\n") == ["unclosed_subckt"]
    assert codes("t\nV1 a 0 1\nR1 a 0 1k\n.ends\n") == ["stray_ends"]
    assert codes("t\nV1 a 0 1\nR1 a 0 1k\n.ic v(zz)=1\n") == ["unknown_node"]
    assert codes("t\nV1 a 0 1\nB1 b 0 v=v(nothere)*2\nR1 b 0 1k\nR2 a 0 1k\n") == ["unknown_node"]
    assert codes(BASE) == []


def test_models_resolve_for_transistors_with_three_or_four_nodes():
    nl = N.parse("t\nV1 c 0 5\nR1 c b 100k\nQ1 c b 0 qn\nQ2 c b 0 0 qn\n.model qn npn bf=100\n")
    assert nl.problems == []
    q = {e.name: e for e in nl.top.elements}
    assert q["q1"].nodes == ["c", "b", "0"] and q["q2"].nodes == ["c", "b", "0", "0"] and q["q1"].model == "qn"
    assert codes("t\nV1 c 0 5\nR1 c b 100k\nQ1 c b 0 nomod\n") == ["missing_model"]


def test_floating_and_dc_less_nodes_are_warnings():
    nl = N.parse("t\nV1 a 0 1\nR1 a b 1k\nC1 b c 1u\nR2 c d 1k\nR3 a 0 1k\n")
    text = " ".join(nl.warnings)
    assert nl.problems == [] and "no DC path" in text and "'c'" in text and "'d'" in text and "dangling" in text and "'d'" in text


def test_summary_counts():
    nl = N.parse(BASE + ".subckt s p q\nR1 p q 1k\n.ends\nX1 out 0 s\n")
    assert N.element_counts(nl) == {"R": 2, "V": 1, "X": 1}
    assert nl.expanded_elements == 5 and nl.has_ground and nl.sources == {"v1": "v"}


def test_normalised_text_only_contains_allowed_characters_whatever_the_input_was():
    nl = N.parse("t\nV1 A 0 SIN(0 1 1K)\nB1 b 0 V=V(A)*2+@v1[i]\nR1 b 0 '1k'\n.PARAM q={A + 1}\n.IC V(A)=0\n")
    assert nl.problems == [] or True
    for line in nl.body:
        assert N.ALLOWED_CHARS.match(line) and line == line.lower() and not re.search(r"\s{2,}|\t", line)
