"""A source line longer than Yosys's scanner can take is refused by the guard (input.too_large), before any program starts."""

import pytest
from digital_fixtures import AND2, job_dir, refused, via_gateway

from eng_toolkit.domains.digital import guard

HUGE_LINES = {
    "letters": "A" * 5_000_000,
    "parens": "(" * 60_000 + "1" + ")" * 60_000,
    "digits": "9" * 200_000,
    "just_over": "module t(input a, output y);\nassign y = a; //" + "x" * guard.MAX_LINE_CHARS + "\nendmodule\n",
}


def arguments(tool: str, source: str) -> tuple[dict, str]:
    return {
        "synthesize": ({"top": "t", "source": source}, "/source"),
        "check": ({"source": source}, "/source"),
        "equivalent": ({"top_a": "t", "source_a": AND2, "source_b": source}, "/source_b"),
    }[tool]


@pytest.mark.parametrize("tool", ["synthesize", "check", "equivalent"])
@pytest.mark.parametrize("name", sorted(HUGE_LINES))
async def test_a_huge_line_is_input_too_large_naming_the_source(name, tool):
    args, param = arguments(tool, HUGE_LINES[name])
    r = await via_gateway(tool, args)
    refused(r, "input.too_large")
    assert r.error.details["param"] == param
    assert len(r.error.message) < 300
    assert not (job_dir(r) / "yosys").exists()  # nothing ran


@pytest.mark.parametrize("tool", ["synthesize", "check"])
async def test_a_huge_line_in_a_file_names_files(tool, tmp_path, monkeypatch):
    monkeypatch.setenv("ENG_TOOLKIT_READ_DIRS", str(tmp_path))
    path = tmp_path / "wide.v"
    path.write_text("module t(input a, output y); assign y = a;" + " " * 100_000 + "endmodule\n", encoding="utf-8")
    args = {"synthesize": {"top": "t", "files": str(path)}, "check": {"files": str(path)}}[tool]
    r = await via_gateway(tool, args)
    refused(r, "input.too_large")
    assert r.error.details["param"] == "/files"


def test_the_limit_is_on_one_line_not_on_the_whole_text():
    line = "// " + "x" * (guard.MAX_LINE_CHARS - 3)
    text = "module t(input a, output y);\n" + (line + "\n") * 50 + "assign y = a;\nendmodule\n"
    assert guard.sanitize(text, label="s", name="s.v").modules == ("t",)
    with pytest.raises(Exception) as caught:
        guard.sanitize(text.replace("assign", "assign " + " " * (guard.MAX_LINE_CHARS + 1)), label="s", name="s.v", param="source")
    assert caught.value.family == "input.too_large" and caught.value.details["param"] == "/source"
    assert caught.value.details["line"] == 52
