"""digital refusals that Yosys raises about the caller's Verilog name the argument to change (details.param, a JSON pointer).

Yosys only names the file the toolkit wrote (src0.v); the toolkit maps it back to source, files, source_a/b, files_a/b or the top
argument.  Each case is the exact hostile input, sent through the in-process gateway against the real Yosys (skipped when absent).
"""

import pytest
from digital_fixtures import needs_yosys
from fastmcp import Client

from eng_toolkit.gateway.server import gateway

pytestmark = needs_yosys

GOOD = "module g(input a, output y); assign y = a; endmodule\n"
SYNTAX_ERROR = "module m(; endmodule"
MISSING_MODULE = "module m(input a, output y); foo u(.a(a)); endmodule"
# `ifdef hides the module from Yosys but not from the toolkit's own scan, so only Yosys can say "Module `m' not found".
HIDDEN_TOP = "`ifdef NOPE\nmodule m(input a, output y); assign y = a; endmodule\n`endif\nmodule other(input a, output y); assign y = a; endmodule\n"


async def call(tool: str, **arguments):
    async with Client(gateway) as client:
        reply = await client.call_tool(tool, arguments, raise_on_error=False)
    payload = reply.structured_content
    assert payload is not None, reply.content
    return payload


def refusal(payload: dict, family: str) -> dict:
    assert payload["ok"] is False, payload
    assert payload["error"]["family"] == family, payload["error"]
    return payload["error"]["details"]


# --------------------------------------------------------------------------- digital_synthesize


async def test_synthesize_a_syntax_error_points_at_source():
    details = refusal(await call("digital_synthesize", source=SYNTAX_ERROR, top="m"), "digital.source_error")
    assert details["param"] == "/source"
    assert details["file"] == "source" and details["line"] == 1 and details["rule"] == "syntax-error"  # the existing details stay


async def test_synthesize_a_syntax_error_in_the_second_file_points_at_files_and_names_the_file(tmp_path):
    first, second = tmp_path / "good.v", tmp_path / "broken.v"
    first.write_text("module m(input a, output y); assign y = a; endmodule\n", encoding="utf-8")
    second.write_text("module n(; endmodule\n", encoding="utf-8")
    details = refusal(await call("digital_synthesize", files=f"{first};{second}", top="m"), "digital.source_error")
    assert details["param"] == "/files"
    assert details["file"] == "broken.v" and details["line"] == 1


async def test_synthesize_an_instance_of_a_module_nobody_defines_points_at_source():
    details = refusal(await call("digital_synthesize", source=MISSING_MODULE, top="m"), "digital.synthesis_error")
    assert details["param"] == "/source"
    assert details["rule"] == "hierarchy-missing-module"


async def test_synthesize_a_top_that_yosys_does_not_find_points_at_top():
    details = refusal(await call("digital_synthesize", source=HIDDEN_TOP, top="m"), "digital.synthesis_error")
    assert details["param"] == "/top"
    assert "not found" in details["yosys_message"]


async def test_synthesize_systemverilog_text_read_as_plain_verilog_points_at_source():
    text = "module m(input logic a, output logic y); always_comb y = a; endmodule\n"
    details = refusal(await call("digital_synthesize", source=text, top="m", language="verilog"), "digital.source_error")
    assert details["param"] == "/source"


# --------------------------------------------------------------------------- digital_check


async def test_check_a_syntax_error_is_a_finding_not_a_refusal():
    """digital_check reports what Yosys says as findings (ok=true); there is no refusal to carry a param."""
    payload = await call("digital_check", source=SYNTAX_ERROR)
    assert payload["ok"] is True
    assert payload["data"]["findings"][0]["rule"] == "syntax-error"


async def test_check_a_missing_top_that_yosys_does_not_find_is_a_finding():
    payload = await call("digital_check", source=HIDDEN_TOP, top="m")
    assert payload["ok"] is True
    assert payload["values"]["errors"]["quantity"] >= 1


async def test_check_a_language_that_is_not_one_points_at_language():
    details = refusal(await call("digital_check", source=GOOD, language="vhdl"), "input.invalid")
    assert details["param"] == "/language"


# --------------------------------------------------------------------------- digital_equivalent


async def test_equivalent_a_syntax_error_in_source_b_points_at_source_b():
    details = refusal(await call("digital_equivalent", source_a=GOOD, top_a="g", source_b="module g(; endmodule"), "digital.source_error")
    assert details["param"] == "/source_b"
    assert details["file"] == "source_b" and details["line"] == 1


async def test_equivalent_a_syntax_error_in_source_a_points_at_source_a():
    details = refusal(await call("digital_equivalent", source_a="module g(; endmodule", top_a="g", source_b=GOOD), "digital.source_error")
    assert details["param"] == "/source_a"


async def test_equivalent_a_syntax_error_in_a_file_of_design_b_points_at_files_b(tmp_path):
    broken = tmp_path / "gate.v"
    broken.write_text("module g(; endmodule\n", encoding="utf-8")
    details = refusal(await call("digital_equivalent", source_a=GOOD, top_a="g", files_b=str(broken)), "digital.source_error")
    assert details["param"] == "/files_b"
    assert details["file"] == "gate.v"


async def test_equivalent_a_top_b_that_yosys_does_not_find_points_at_top_b():
    details = refusal(await call("digital_equivalent", source_a=GOOD, top_a="g", source_b=HIDDEN_TOP, top_b="m"), "digital.synthesis_error")
    assert details["param"] == "/top_b"


async def test_equivalent_a_top_a_that_yosys_does_not_find_points_at_top_a():
    details = refusal(await call("digital_equivalent", source_a=HIDDEN_TOP, top_a="m", source_b=GOOD, top_b="g"), "digital.synthesis_error")
    assert details["param"] == "/top_a"


async def test_equivalent_a_port_missing_in_design_b_points_at_source_b():
    other_ports = "module g(input a, output z); assign z = a; endmodule\n"
    details = refusal(await call("digital_equivalent", source_a=GOOD, top_a="g", source_b=other_ports), "digital.interface_mismatch")
    assert details["param"] == "/source_b"
    assert "gate module" in details["yosys_message"]


async def test_equivalent_a_port_missing_in_design_a_points_at_source_a():
    more_ports = "module g(input a, input b, output y); assign y = a & b; endmodule\n"
    details = refusal(await call("digital_equivalent", source_a=GOOD, top_a="g", source_b=more_ports), "digital.interface_mismatch")
    assert details["param"] == "/source_a"
    assert "gold module" in details["yosys_message"]


async def test_equivalent_an_undefined_module_in_design_b_points_at_source_b():
    details = refusal(await call("digital_equivalent", source_a=GOOD, top_a="g", source_b="module g(input a, output y); foo u(a); endmodule"), "digital.synthesis_error")
    assert details["param"] == "/source_b"


async def test_equivalent_an_undefined_module_in_design_a_points_at_source_a():
    details = refusal(await call("digital_equivalent", source_a="module g(input a, output y); foo u(a); endmodule", top_a="g", source_b=GOOD), "digital.synthesis_error")
    assert details["param"] == "/source_a"
