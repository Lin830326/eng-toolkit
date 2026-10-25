"""The source gatekeeper on hostile Verilog.  No Yosys needed: every case here is decided before a program is started.

Each refusal names the construct and the line and echoes at most 80 characters of the caller's text.  Allowed constructs get a
case of their own, so the filter is shown to be narrow and not simply a wall.
"""

import pytest

from eng_toolkit.domains.digital import guard
from eng_toolkit.errors import ToolFailure

MOD = "module t(input a, output y);\n{body}\nassign y = a;\nendmodule\n"


def src(body: str) -> str:
    return MOD.format(body=body)


def check(text, name="s.v"):
    return guard.sanitize(text, label=name, name="src0.v")


def refuse(text, family="input.forbidden_construct", line=None):
    with pytest.raises(ToolFailure) as caught:
        check(text)
    exc = caught.value
    assert exc.family == family, exc.to_dict()
    if line is not None:
        assert exc.details["line"] == line, exc.to_dict()
        assert f"line {line}" in exc.message
    assert len(exc.details.get("snippet", "")) <= guard.SNIPPET_CHARS
    return exc


@pytest.mark.parametrize(
    "body",
    [
        '`include "C:/Windows/win.ini"',
        '`include "x.vh"',
        '`INCLUDE "x.vh"',
        '`Include "x.vh"',
        '`iNcLuDe   "x.vh"',
        '`include\t"x.vh"',
        '   `include<x.vh>',
    ],
)
def test_include_is_refused_in_any_case_and_spacing(body):
    exc = refuse(src(body), line=2)
    assert "include" in exc.message.lower()


@pytest.mark.parametrize("body", ["` include \"x.vh\"", "`\ninclude \"x.vh\"", "`", "` "])
def test_a_backtick_that_does_not_start_a_name_is_refused(body):
    refuse(src(body))


@pytest.mark.parametrize(
    "body, line",
    [
        ('initial $readmemh("x.hex", mem);', 2),
        ('initial $readmemb("x.bin", mem);', 2),
        ('initial $READMEMH("x.hex", mem);', 2),
        ('integer f; initial f = $fopen("out.txt");', 2),
        ('initial $fwrite(f, "x");', 2),
        ('initial $fdisplay(f, "x");', 2),
        ('initial $system("dir");', 2),
        ('initial $dumpfile("a.vcd");', 2),
        ('initial $dumpvars;', 2),
        ('initial $sformat(s, "x");', 2),
        ('initial $fscanf(f, "x");', 2),
        ('initial $value$plusargs("a=%d", x);', 2),
    ],
)
def test_system_tasks_that_touch_files_or_run_programs_are_refused(body, line):
    exc = refuse(src(body), line=line)
    assert "$" in exc.message


def test_a_system_name_assembled_by_a_macro_is_refused():
    # `A is "read" and `B is "memh": the preprocessor would paste them into $readmemh
    text = src("`define A read\n`define B memh\ninitial $`A`B(\"x.hex\", m);")
    refuse(text)
    text = src("`define A read\n`define B memh\ninitial $read`B(\"x.hex\", m);")
    refuse(text)


def test_an_allowed_system_name_cannot_be_extended_by_a_macro():
    refuse(src("`define F f\ninitial $display`F(\"x\");"))


@pytest.mark.parametrize(
    "body",
    [
        "`define I include\n`I \"x.vh\"",  # the macro is allowed, the body is plain text...
    ],
)
def test_a_macro_whose_body_is_the_word_include_is_harmless_without_a_backtick(body):
    # `include needs the backtick; a body "include" cannot supply one (bodies may not contain a backtick) and the use `I
    # expands to the plain word.  The use itself is allowed, the text after it is not a directive.
    check(src(body))


@pytest.mark.parametrize(
    "body, why",
    [
        ("`define X `include", "backtick"),
        ('`define X "a"', "quote"),
        ("`define X $readmemh", "$"),
        ("`define X a\\b", "backslash"),
        ("`define X /* open", "comment"),
        ("`define X a */ b", "comment"),
        ("`define X /", "starts or ends"),
        ("`define X a *", "starts or ends"),
        ("`define F(a) a", "arguments"),
        ("`define", "macro name"),
    ],
)
def test_unsafe_macro_definitions_are_refused(body, why):
    with pytest.raises(ToolFailure) as caught:
        check(src(body))
    assert caught.value.family.startswith("input."), caught.value.to_dict()


def test_a_define_that_hides_the_next_lines_in_a_comment_is_refused():
    # If the checker treated "/*" in a macro body as a comment, `include on the next line would look commented out
    # to the checker but would run in Yosys, whose define ends at the end of the line.
    refuse(src('`define X /*\n`include "C:/Windows/win.ini"\n*/'), line=2)


def test_the_macro_use_of_an_undefined_name_is_refused():
    exc = refuse(src("wire w = `UNKNOWN;"), line=2)
    assert "UNKNOWN" in exc.message


@pytest.mark.parametrize("directive", ["line", "pragma", "__FILE__", "undefineall", "begin_keywords"])
def test_other_directives_are_refused(directive):
    refuse(src(f"`{directive} 1"))


def test_directives_are_case_sensitive():
    exc = refuse(src("`DEFINE X 1"))
    assert "case-sensitive" in exc.message
    refuse(src("`Timescale 1ns/1ps"), line=2)


@pytest.mark.parametrize(
    "text",
    [
        "module t(input a, output y);\nassign y = a; // `include \"x\" and $readmemh(\"f\")\nendmodule\n",
        'module t(input a, output y);\nassign y = a; /* `include "x"\n$fopen */\nendmodule\n',
        'module t(input a, output y);\ninitial $display("`include $readmemh $fopen");\nassign y = a;\nendmodule\n',
        'module t(input a, output y);\nparameter S = "// not a comment `include";\nassign y = a;\nendmodule\n',
    ],
)
def test_text_in_comments_and_strings_is_data_and_not_refused(text):
    assert check(text).modules == ("t",)


def test_allowed_directives_functions_and_macros_pass():
    text = (
        "`timescale 1ns/1ps\n`default_nettype none\n`define WIDTH 8\n`define MASK (1 << 3) - 1\n"
        "`ifdef SYNTHESIS\n`else\n`endif\n"
        "module t #(parameter N = 16) (input [`WIDTH-1:0] a, output [$clog2(N)-1:0] y);\n"
        "  wire signed [3:0] s = $signed(a[3:0]);\n  assign y = a[$clog2(N)-1:0];\nendmodule\n`default_nettype wire\n"
    )
    assert check(text).modules == ("t",)


def test_a_dollar_inside_an_identifier_is_not_a_system_task():
    assert check("module t(input a, output y);\nwire a$readmemh;\nassign y = a;\nendmodule\n").modules == ("t",)


def test_escaped_identifiers_from_a_synthesised_netlist_pass():
    text = "module t(a, y);\ninput a;\noutput y;\nwire \\$abc$123$new_n7 ;\nassign \\$abc$123$new_n7 = a;\nassign y = \\$abc$123$new_n7 ;\nendmodule\n"
    assert check(text).modules == ("t",)


@pytest.mark.parametrize("ident", ["\\a`include ", '\\a"b ', "\\a//b ", "\\a/*b "])
def test_an_escaped_identifier_cannot_smuggle_a_marker(ident):
    refuse(f"module t(input a, output y);\nwire {ident};\nassign y = a;\nendmodule\n")


@pytest.mark.parametrize(
    "text",
    [
        "module t(input a, output y); /* never closed\nassign y = a;\nendmodule\n",
        'module t(input a, output y);\ninitial $display("never closed);\nendmodule\n',
        "module t(input a, output y);\nassign y = a; // comment \\\n`include \"x\"\nendmodule\n",
        "module t(input a, output y);\n`define X 1 \\\n`include \"x\"\nassign y = a;\nendmodule\n",
        "module t(input a, output y);\nassign y = a \\\n;\nendmodule\n",
        "module t(input a, output y);\nassign y = a; \\ \nendmodule\n",
    ],
)
def test_unterminated_comments_and_strings_and_line_continuation_are_refused(text):
    with pytest.raises(ToolFailure) as caught:
        check(text)
    assert caught.value.family in ("input.invalid", "input.forbidden_construct")


def test_line_continuation_names_the_line():
    exc = refuse("module t(input a, output y);\nassign y = a \\\n;\nendmodule\n", line=2)
    assert "continuation" in exc.message


def test_nul_bytes_and_other_control_characters_are_refused():
    for bad in ("module t;\x00endmodule\n", "module t;\x01endmodule\n", "module t;\x7fendmodule\n", "module t;\x1bendmodule\n"):
        with pytest.raises(ToolFailure) as caught:
            check(bad)
        assert caught.value.family == "input.invalid"
    with pytest.raises(ToolFailure) as caught:
        guard.sanitize(b"module t;\x00endmodule\n", label="f.v", name="src0.v")
    assert "NUL" in caught.value.message and caught.value.details["line"] == 1


def test_bytes_that_are_not_utf8_are_refused_with_the_line():
    with pytest.raises(ToolFailure) as caught:
        guard.sanitize(b"module t;\nwire \xff\xfe;\nendmodule\n", label="f.v", name="src0.v")
    assert caught.value.family == "input.invalid"
    assert caught.value.details["line"] == 2 and "UTF-8" in caught.value.message


def test_a_lone_surrogate_in_text_is_refused():
    with pytest.raises(ToolFailure) as caught:
        check("module t;\ud800endmodule\n")
    assert caught.value.family == "input.invalid"


def test_non_ascii_is_allowed_in_comments_and_strings_only():
    assert check('module t; // été\ninitial $display("é");\nendmodule\n').modules == ("t",)
    exc = refuse("module t;\nwire é;\nendmodule\n", family="input.invalid", line=2)
    assert "non-ASCII" in exc.message


def test_sizes():
    big = "// " + "x" * guard.MAX_SOURCE_BYTES + "\n"
    with pytest.raises(ToolFailure) as caught:
        check(big)
    assert caught.value.family == "input.too_large" and caught.value.details["limit_bytes"] == guard.MAX_SOURCE_BYTES
    with pytest.raises(ToolFailure) as caught:
        check("// " + "x" * (5 * 1024 * 1024))
    assert caught.value.family == "input.too_large"
    ok = "module t; endmodule\n" + ("// " + "x" * 997 + "\n") * ((guard.MAX_SOURCE_BYTES - 100) // 1001)  # lines are limited too
    assert check(ok).modules == ("t",)


def test_the_number_of_modules_is_capped():
    many = "".join(f"module m{i}; endmodule\n" for i in range(guard.MAX_MODULES + 1))
    with pytest.raises(ToolFailure) as caught:
        check(many)
    assert caught.value.family == "input.out_of_range" and caught.value.details["modules"] == guard.MAX_MODULES + 1
    assert len(check(many.split("module m200")[0]).modules) == guard.MAX_MODULES


def test_line_endings_are_normalised_and_line_numbers_kept():
    checked = check("module t;\r\nwire a;\rwire b;\r\nendmodule\r\n")
    assert checked.text == "module t;\nwire a;\nwire b;\nendmodule\n"
    exc = refuse("module t;\r\nwire a;\r\n`include \"x\"\r\nendmodule\r\n", line=3)
    assert exc.details["file"] == "s.v"


def test_a_message_never_echoes_more_than_80_characters_of_the_line():
    long_line = '`include "' + "A" * 5000 + '"'
    exc = refuse(src(long_line))
    assert len(exc.details["snippet"]) <= 80
    assert len(exc.message) < 200
    assert "AAAA" not in exc.message


@pytest.mark.parametrize(
    "name",
    ["top; shell dir", "t op", "", "1abc", "a-b", "a.b", "\\esc", "a" * 65, "t\nread", "t`", "$x", "t;", "t\x00", "é"],
)
def test_module_names_must_be_plain_identifiers(name):
    with pytest.raises(ToolFailure) as caught:
        guard.plain_identifier(name, param="top")
    assert caught.value.family == "input.invalid" and "top" in caught.value.message
    assert len(str(caught.value.details)) < 300


@pytest.mark.parametrize("name", ["t", "_x", "Top_1", "a" * 64])
def test_plain_identifiers_pass(name):
    assert guard.plain_identifier(f"  {name}  ", param="top") == name


def test_find_module_lists_what_exists():
    sources = [check(src(""), "a.v")]
    guard.find_module(sources, "t", param="top")
    with pytest.raises(ToolFailure) as caught:
        guard.find_module(sources, "u", param="top")
    assert caught.value.family == "input.invalid" and "t" in caught.value.details["modules"]
