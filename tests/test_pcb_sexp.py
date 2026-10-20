"""The S-expression reader of the pcb domain on its own: what it accepts, and every way a text can be wrong."""

import pytest

from eng_toolkit.domains.pcb import sexp
from eng_toolkit.domains.pcb.sexp import SexpError


def parse(text: str):
    return list(sexp.top_level(text))


def test_children_come_one_by_one_after_the_root_head():
    # (kicad_pcb (version 1) (a "x y" 1 2.5) (b (c (d)))): head, then three children; the root list itself is never kept
    got = parse('(kicad_pcb (version 1) (a "x y" 1 2.5) (b (c (d))))')
    assert got == [["kicad_pcb"], ["version", "1"], ["a", "x y", "1", "2.5"], ["b", ["c", ["d"]]]]


def test_root_without_children_and_bar_strings_and_escapes():
    assert parse("(kicad_pcb)") == [["kicad_pcb"]]
    assert parse('(r (s |abc def| "q\\"uote" "a\\nb"))')[1] == ["s", "abc def", 'q"uote', "a\nb"]


def test_a_bom_is_dropped_and_utf8_is_read():
    assert sexp.decode(b"\xef\xbb\xbf(a \"\xe6\x9d\xbf\")") == '(a "板")'


@pytest.mark.parametrize(
    "text, fragment",
    [
        ("", "no S-expression"),
        ("   \n ", "no S-expression"),
        ("abc", "does not start with '('"),
        ("(a", "unclosed"),
        ("(a (b)", "unclosed"),
        ("a)", "does not start"),
        ("(a))", "after the closing parenthesis"),
        ("(a) (b)", "after the closing parenthesis"),
        ('(a "never closed', "unterminated string"),
        ("(a |never closed", "unterminated |...| string"),
    ],
)
def test_malformed_text_is_a_syntax_error_with_a_position(text, fragment):
    with pytest.raises(SexpError) as info:
        parse(text)
    assert info.value.kind == "syntax"
    assert fragment in info.value.message


def test_a_nul_byte_and_text_that_is_not_utf8_are_refused():
    with pytest.raises(SexpError) as nul:
        sexp.decode(b"(kicad_pcb\x00)")
    assert nul.value.kind == "encoding" and nul.value.details["byte_offset"] == 10
    with pytest.raises(SexpError) as bad:
        sexp.decode(b"(kicad_pcb \xff\xfe)")
    assert bad.value.kind == "encoding"
    with pytest.raises(SexpError) as png:
        sexp.decode(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR")
    assert png.value.kind == "encoding"


def test_error_position_is_line_and_column():
    with pytest.raises(SexpError) as info:
        parse('(a\n  (b)\n  "x')
    assert (info.value.line, info.value.column) == (3, 3)


def test_nesting_deeper_than_the_limit_is_refused_and_never_recurses():
    # 100 000 opening parentheses would overflow a recursive parser (Python's limit is about 1000); here it ends at level 65
    with pytest.raises(SexpError) as info:
        parse("(" * 100_000)
    assert info.value.kind == "depth"
    assert info.value.details["limit"] == sexp.MAX_DEPTH
    ok = "(" * sexp.MAX_DEPTH + ")" * sexp.MAX_DEPTH  # exactly the limit is fine
    assert len(parse(ok)) == 2
    with pytest.raises(SexpError):
        parse("(" * (sexp.MAX_DEPTH + 1) + ")" * (sexp.MAX_DEPTH + 1))


def test_a_string_over_the_limit_is_too_large_and_one_under_it_is_kept(monkeypatch):
    monkeypatch.setattr(sexp, "MAX_STRING_CHARS", 1000)
    assert len(parse('(r (a "' + "x" * 1000 + '"))')[1][1]) == 1000
    with pytest.raises(SexpError) as info:
        parse('(r (a "' + "x" * 1001 + '"))')
    assert info.value.kind == "too_large"


def test_too_many_tokens_is_too_large(monkeypatch):
    monkeypatch.setattr(sexp, "MAX_TOKENS", 10)
    with pytest.raises(SexpError) as info:
        parse("(a " + "b " * 20 + ")")
    assert info.value.kind == "too_large"


def test_a_very_long_unterminated_string_is_not_a_regex_blowup():
    # the string pattern has no nested quantifier: 5 million characters without a closing quote fail at once
    with pytest.raises(SexpError):
        parse('(a "' + "\\" * 5_000_000)
