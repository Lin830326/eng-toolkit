"""The digital tools on Verilog made to hurt them, called through the gateway's MCP client (what an LLM client does).

Two kinds of case.  Refusals that need no Yosys (the gatekeeper decides before a program starts): the job directory never gets a
``yosys`` folder, which is the proof that nothing was run.  Resource cases that need Yosys: a design that never finishes
elaborating, a memory of 2^30 words, a vector of 2 billion bits; each ends in seconds with a structured refusal and no process
left behind.
"""

import json
import time
from pathlib import Path

import pytest
from digital_fixtures import AND2, HAVE_YOSYS, job_dir, needs_yosys, refused, via_gateway

from eng_toolkit import external
from eng_toolkit.external_catalog import YOSYS

MOD = "module t(input a, output y);\n{body}\nassign y = a;\nendmodule\n"

REFUSED_BODIES = {
    "include_win_ini": '`include "C:/Windows/win.ini"',
    "include_relative": '`include "../../secret.v"',
    "include_upper": '`INCLUDE "C:/Windows/win.ini"',
    "include_mixed": '`InClUdE "C:/Windows/win.ini"',
    "include_tab": '`include\t"C:/Windows/win.ini"',
    "include_odd_spacing": '   `include      <C:/Windows/win.ini>',
    "include_split_tick": '`\ninclude "C:/Windows/win.ini"',
    "include_tick_space": '` include "C:/Windows/win.ini"',
    "include_in_define": '`define GRAB `include "C:/Windows/win.ini"\n`GRAB',
    "include_hidden_in_comment_define": '`define X /*\n`include "C:/Windows/win.ini"\n*/',
    "include_after_line_continuation": '// harmless \\\n`include "C:/Windows/win.ini"',
    "readmemh": 'reg [7:0] mem [0:3];\ninitial $readmemh("x.hex", mem);',
    "readmemb_upper": 'reg [7:0] mem [0:3];\ninitial $READMEMB("x.bin", mem);',
    "fopen": 'integer f;\ninitial f = $fopen("C:/Users/Public/x.txt", "w");',
    "fwrite": 'initial $fwrite(1, "x");',
    "fdisplay": 'initial $fdisplay(1, "x");',
    "system": 'initial $system("dir");',
    "dumpfile": 'initial begin $dumpfile("x.vcd"); $dumpvars; end',
    "macro_split_task": '`define P read\n`define Q memh\ninitial $`P`Q("x.hex", m);',
    "macro_split_task_tail": '`define Q memh\ninitial $read`Q("x.hex", m);',
}


def text_of(body):
    return MOD.format(body=body)


@pytest.mark.asyncio
@pytest.mark.parametrize("name", sorted(REFUSED_BODIES))
@pytest.mark.parametrize("tool", ["synthesize", "check", "equivalent"])
async def test_hostile_source_is_refused_before_anything_runs(name, tool):
    text = text_of(REFUSED_BODIES[name])
    args = {"synthesize": {"top": "t", "source": text}, "check": {"source": text}, "equivalent": {"top_a": "t", "source_a": AND2, "source_b": text}}[tool]
    r = await via_gateway(tool, args)
    refused(r, r.error.family)
    assert r.error.family in ("input.forbidden_construct", "input.invalid")  # a line continuation is input.forbidden_construct too
    assert len(r.error.message) < 250
    assert len(r.error.details.get("snippet", "")) <= 80
    assert "line" in r.error.message
    assert not (job_dir(r) / "yosys").exists()  # no program was started, no file written for it


@pytest.mark.asyncio
async def test_a_hostile_file_is_refused_like_hostile_text(tmp_path):
    secret = tmp_path / "secret.v"
    secret.write_text("wire leaked;\n", encoding="utf-8")
    bad = tmp_path / "bad.v"
    bad.write_text(f'module t(input a, output y);\n`include "{secret.as_posix()}"\nassign y = a;\nendmodule\n', encoding="utf-8")
    r = await via_gateway("synthesize", {"top": "t", "files": str(bad)})
    refused(r, "input.forbidden_construct")
    assert r.error.details["file"] == "bad.v" and r.error.details["line"] == 2
    assert not (job_dir(r) / "yosys").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name",
    ["top; shell dir", "t && calc", "t\nread_verilog x", "t -o x", "../t", "t.v", "t'", "$t", "`t", "", "t" * 65, "t\x00"],
)
async def test_a_module_name_that_is_not_an_identifier_is_refused(name):
    for tool, args in (
        ("synthesize", {"top": name, "source": AND2}),
        ("check", {"top": name or "x", "source": AND2}) if name else ("check", {"top": "t\x01", "source": AND2}),
        ("equivalent", {"top_a": name, "source_a": AND2, "source_b": AND2}),
        ("equivalent", {"top_a": "t", "top_b": name or "t\x01", "source_a": AND2, "source_b": AND2}),
    ):
        r = await via_gateway(tool, args)
        refused(r, "input.invalid")
        assert not (job_dir(r) / "yosys").exists()


@pytest.mark.asyncio
async def test_a_five_megabyte_source_is_refused():
    big = "module t(input a, output y); assign y = a; endmodule\n// " + "x" * (5 * 1024 * 1024) + "\n"
    for tool, args in (("synthesize", {"top": "t", "source": big}), ("check", {"source": big}), ("equivalent", {"top_a": "t", "source_a": AND2, "source_b": big})):
        r = await via_gateway(tool, args)
        refused(r, "input.too_large")
        assert r.error.details["limit_bytes"] == 1_000_000
        assert not (job_dir(r) / "yosys").exists()


@pytest.mark.asyncio
async def test_a_five_megabyte_file_is_refused_before_it_is_copied(tmp_path):
    big = tmp_path / "big.v"
    big.write_bytes(b"module t(input a, output y); assign y = a; endmodule\n// " + b"x" * (5 * 1024 * 1024) + b"\n")
    r = await via_gateway("synthesize", {"top": "t", "files": str(big)})
    refused(r, "input.too_large")
    assert not (job_dir(r) / "yosys").exists()


@pytest.mark.asyncio
async def test_nul_bytes_are_refused_in_text_and_in_files(tmp_path):
    text = "module t(input a, output y);\nassign y = a;\x00\nendmodule\n"
    r = await via_gateway("synthesize", {"top": "t", "source": text})
    refused(r, "input.invalid")
    assert "NUL" in r.error.message and r.error.details["line"] == 2
    f = tmp_path / "nul.v"
    f.write_bytes(b"module t(input a, output y);\nassign y = a;\x00\nendmodule\n")
    r = await via_gateway("check", {"files": str(f)})
    refused(r, "input.invalid")
    assert r.error.details["file"] == "nul.v"
    r = await via_gateway("synthesize", {"top": "t", "files": "a.v\x00b.v"})
    refused(r, "input.invalid")


@pytest.mark.asyncio
async def test_non_utf8_bytes_are_refused(tmp_path):
    f = tmp_path / "latin.v"
    f.write_bytes(b"module t(input a, output y);\n// caf\xe9\nassign y = a;\nendmodule\n")
    for tool, args in (("synthesize", {"top": "t", "files": str(f)}), ("check", {"files": str(f)}), ("equivalent", {"top_a": "t", "files_a": str(f), "source_b": AND2})):
        r = await via_gateway(tool, args)
        refused(r, "input.invalid")
        assert "UTF-8" in r.error.message and r.error.details["line"] == 2
        assert not (job_dir(r) / "yosys").exists()


@pytest.mark.asyncio
async def test_a_wrong_extension_and_a_missing_file_are_refused(tmp_path):
    f = tmp_path / "x.txt"
    f.write_text(AND2, encoding="utf-8")
    refused(await via_gateway("synthesize", {"top": "t", "files": str(f)}), "input.unsupported_format")
    refused(await via_gateway("synthesize", {"top": "t", "files": str(tmp_path / "nope.v")}), "input.file_not_found")


@pytest.mark.asyncio
async def test_files_outside_the_allowed_directories_are_not_read(tmp_path, monkeypatch):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "outside.v"
    outside.write_text(AND2, encoding="utf-8")
    monkeypatch.setenv("ENG_TOOLKIT_READ_DIRS", str(allowed))
    r = await via_gateway("synthesize", {"top": "t", "files": str(outside)})
    refused(r, "input.path_not_allowed")
    assert r.provenance.input_files == []


@needs_yosys
@pytest.mark.asyncio
async def test_allowed_constructs_still_work_through_the_gateway():
    text = (
        "`timescale 1ns/1ps\n`define W 4\nmodule t(input [`W-1:0] a, input [`W-1:0] b, output [`W-1:0] y);\n"
        "  // `include \"C:/Windows/win.ini\" and $readmemh in a comment are only text\n"
        "  initial $display(\"`include $fopen\");\n  assign y = a & b;\nendmodule\n"
    )
    r = await via_gateway("synthesize", {"top": "t", "source": text, "target": "structural"})
    assert r.ok, r.error
    assert r.data["cells_by_type"] == {"$_AND_": 4}  # 4 bits, one AND each


# --------------------------------------------------------------------------- resources (real Yosys)


@needs_yosys
@pytest.mark.asyncio
@pytest.mark.parametrize("tool", ["synthesize", "check", "equivalent"])
async def test_a_generate_loop_of_a_billion_iterations_hits_the_time_limit(tool):
    bomb = (
        "module t(input a, output y);\n  genvar i;\n  generate for (i = 0; i < 1000000000; i = i + 1) begin : g\n    wire w = a;\n  end endgenerate\n"
        "  assign y = a;\nendmodule\n"
    )
    args = {"synthesize": {"top": "t", "source": bomb}, "check": {"source": bomb, "top": "t"}, "equivalent": {"top_a": "t", "source_a": bomb, "source_b": AND2}}[tool]
    started = time.monotonic()
    r = await via_gateway(tool, {**args, "timeout_seconds": 3})
    elapsed = time.monotonic() - started
    refused(r, "engine.timeout")
    assert elapsed < 30, elapsed
    assert "3 s" in r.error.message


@needs_yosys
@pytest.mark.asyncio
async def test_a_two_to_the_30_memory_is_stopped():
    text = (
        "module t(input clk, input [29:0] ad, input [7:0] d, output reg [7:0] q);\n  reg [7:0] m [0:(1<<30)-1];\n"
        "  always @(posedge clk) begin m[ad] <= d; q <= m[ad]; end\nendmodule\n"
    )
    started = time.monotonic()
    r = await via_gateway("synthesize", {"top": "t", "source": text, "timeout_seconds": 5})
    assert time.monotonic() - started < 30
    assert r.ok is False and r.error.family in ("engine.resource_limit", "engine.timeout"), r.error
    assert "too large" in r.error.message or "took longer" in r.error.message


@needs_yosys
@pytest.mark.asyncio
async def test_a_vector_of_two_billion_bits_is_refused():
    text = "module t(input a, output y);\n  wire [1999999999:0] w = {2000000000{a}};\n  assign y = w[0];\nendmodule\n"
    r = await via_gateway("synthesize", {"top": "t", "source": text, "timeout_seconds": 5})
    assert r.ok is False and r.error.family in ("digital.synthesis_error", "engine.resource_limit", "engine.timeout")
    assert "src0" not in r.error.message  # the toolkit's own file names never reach the caller


@needs_yosys
@pytest.mark.asyncio
async def test_a_huge_declared_width_is_stopped():
    text = "module t(input a, output y);\n  reg [999999999:0] x;\n  assign y = a;\nendmodule\n"
    r = await via_gateway("synthesize", {"top": "t", "source": text, "timeout_seconds": 5})
    assert r.ok is False and r.error.family in ("engine.resource_limit", "engine.timeout", "digital.synthesis_error")


# --------------------------------------------------------------------------- the hazard is real, and the discovery works


@needs_yosys
def test_negative_control_yosys_itself_would_read_the_file(tmp_path):
    """Without the gatekeeper, Yosys reads whatever an include names.  This is why the gatekeeper exists."""
    from eng_toolkit.external import run

    secret = tmp_path / "work" / "secret.vh"
    secret.parent.mkdir()
    secret.write_text("wire marker_read_from_the_included_file;\n", encoding="utf-8")
    top = secret.parent / "top.v"
    top.write_text(f'module t(input a, output y);\n`include "{secret.as_posix()}"\nassign y = a;\nendmodule\n', encoding="utf-8")
    (secret.parent / "s.ys").write_text("read_verilog top.v\ndump\n", encoding="utf-8")
    record = run(YOSYS, ["-q", "-l", secret.parent / "y.log", "-s", secret.parent / "s.ys"], workdir=secret.parent, check=False, timeout_s=20)
    log = (secret.parent / "y.log").read_text(encoding="utf-8", errors="replace")
    assert record.returncode == 0 and "marker_read_from_the_included_file" in log


@pytest.mark.asyncio
async def test_the_domain_is_discovered_and_yosys_is_a_registered_program():
    from fastmcp import Client

    from eng_toolkit.contract import ToolResult
    from eng_toolkit.gateway.server import gateway

    async with Client(gateway) as client:
        tools = await client.list_tools()
        listed = ToolResult.model_validate((await client.call_tool("toolkit_list_tools", {})).structured_content)
        doctor = ToolResult.model_validate((await client.call_tool("toolkit_doctor", {"scan_env": False}, raise_on_error=False)).structured_content)
    names = {t.name for t in tools}
    assert {"digital_synthesize", "digital_check", "digital_equivalent"} <= names
    domain = next(d for d in listed.data["domains"] if d["name"] == "digital")
    assert set(domain["tools"]) == {"digital_synthesize", "digital_check", "digital_equivalent"}
    (row,) = [t for t in doctor.data["external_tools"] if t["name"] == "yosys"]
    assert row["domains"] == ["digital"] and row["declared_license"] == "ISC" and row["declared_tier"] == "permissive"
    assert row["found"] is HAVE_YOSYS
    if HAVE_YOSYS:
        assert row["status"] == "ok" and row["version"]
    json.dumps(doctor.model_dump(mode="json"), allow_nan=False)


@pytest.mark.asyncio
async def test_without_yosys_the_tools_say_where_to_get_it(monkeypatch, tmp_path):
    monkeypatch.setenv("ENG_TOOLKIT_YOSYS", str(tmp_path / "no-yosys.exe"))
    r = await via_gateway("synthesize", {"top": "t", "source": AND2})
    refused(r, "engine.unavailable")
    assert "yosyshq.net" in r.error.message and "ENG_TOOLKIT_YOSYS" in r.error.message


@needs_yosys
@pytest.mark.asyncio
async def test_a_chain_that_overflows_the_parser_stack_is_a_structured_failure():
    text = "module t(input a, output y);\n  assign y = a" + (" & a" * 100 + "\n") * 1_400 + ";\nendmodule\n"  # 140 000 terms on 1 400 lines, about 560 KB: under the size and line limits
    r = await via_gateway("synthesize", {"top": "t", "source": text, "timeout_seconds": 20})
    refused(r, "engine.failure")
    assert "stack overflow" in r.error.message and "too deeply" in r.error.message


@pytest.mark.asyncio
async def test_more_than_200_modules_are_refused():
    many = "".join(f"module m{i}(input a, output y); assign y = a; endmodule\n" for i in range(201))
    r = await via_gateway("check", {"source": many})
    refused(r, "input.out_of_range")
    assert r.error.details["max_modules"] == 200 and not (job_dir(r) / "yosys").exists()
    if not HAVE_YOSYS:  # the refusal above needs no program; the accepted design below runs Yosys
        return
    two = many.split("module m100")[0]
    spread = await via_gateway("equivalent", {"top_a": "m0", "source_a": two, "source_b": two})
    assert spread.ok, spread.error  # 100 modules per design is within the limit
    assert spread.data["verdict"] == "equivalent"
