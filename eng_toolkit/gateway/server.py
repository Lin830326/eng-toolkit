"""The eng-toolkit MCP gateway.

* Mounts each domain sub-server with FastMCP ``mount(server, namespace=...)``, so
  the ``calc`` domain's ``cylinder_force`` is exposed as ``calc_cylinder_force``.
  (MCP tool names may not contain ``.``; the design doc's ``calc.cylinder_force``
  maps to ``calc_cylinder_force``.)
* Adds two gateway-level tools:
  ``toolkit_list_tools`` -- installed domains, engines and versions;
  ``toolkit_doctor``     -- dependency / license-tier / version / workspace health check.

Run with ``eng-toolkit`` (console script) or ``python -m eng_toolkit`` -> stdio transport.
"""

from __future__ import annotations

import os
import platform
import re
import sys
from typing import Annotated, Any

from fastmcp import FastMCP
from fastmcp.exceptions import ValidationError as MCPValidationError
from fastmcp.server.middleware import Middleware
from fastmcp.tools import ToolResult as MCPToolResult
from pydantic import Field, ValidationError

from eng_toolkit import __version__
from eng_toolkit.contract import Provenance, ToolResult, failure, inputs_hash
from eng_toolkit.domains import all_domains
from eng_toolkit.external import probe as probe_external
from eng_toolkit.licenses import (
    EXTERNAL,
    KNOWN,
    POLICY,
    TIER_FORBIDDEN,
    TIER_GPL,
    TIER_PERMISSIVE,
    TIER_UNKNOWN,
    classify_license,
    installed_license,
    scan_environment,
    tier_explained_by_bundle,
    violates_policy,
)
from eng_toolkit.runner import ENV_READ_DIRS, clean_inputs, clean_text, read_dirs
from eng_toolkit.workspace import Workspace, json_safe

ENGINE = "eng_toolkit.gateway"
MIN_PYTHON = (3, 12)
_NUMBER_EXAMPLE = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
_QUANTITY_EXAMPLE = re.compile(
    rf"['\"]{_NUMBER_EXAMPLE}\s+(?:[A-Za-z°µμ%]|1/)[^'\";,\[\]{{}}]*['\"]"
    rf"|e\.g\.\s*{_NUMBER_EXAMPLE}\s+(?:[A-Za-z°µμ%]|1/)[^\s'\";,\[\]{{}}]*(?=[.;]|$)"
)

# The toolkit must never call an LLM. doctor reports if any of these are even installed.
LLM_SDK_DISTS: tuple[str, ...] = (
    "anthropic",
    "openai",
    "google-genai",
    "google-generativeai",
    "litellm",
    "langchain",
    "langchain-core",
    "ollama",
    "cohere",
    "mistralai",
)
LLM_API_KEY_ENV: tuple[str, ...] = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "GOOGLE_API_KEY",
    "GEMINI_API_KEY",
    "MISTRAL_API_KEY",
    "COHERE_API_KEY",
)


class ArgumentRefusals(Middleware):
    """Translate framework argument validation before it becomes an MCP error."""

    async def on_call_tool(self, context, call_next):
        try:
            return await call_next(context)
        except (MCPValidationError, ValidationError) as exc:
            validation = exc if isinstance(exc, ValidationError) else exc.__cause__
            if not isinstance(validation, ValidationError):
                raise
            issues = validation.errors(include_url=False, include_context=False, include_input=False)
            missing = {"missing", "missing_argument", "missing_keyword_only_argument"}
            issue = next((item for item in issues if item["type"] not in missing), issues[0])
            location = issue["loc"]
            param = clean_text("/" + "/".join(str(part).replace("~", "~0").replace("/", "~1") for part in location))
            # An unexpected argument name is caller text too; never echo an unbounded key.
            if len(param) > 256:
                param = "/"
            arguments = context.message.arguments or {}
            tool = await context.fastmcp_context.fastmcp.get_tool(context.message.name)
            properties = tool.parameters.get("properties", {}) if tool is not None else {}
            field = properties.get(location[0], {}) if location else {}
            expected = field.get("type", "declared argument type")
            kind = issue["type"]
            family = "input.invalid"
            if kind in missing:
                family = "input.missing"
                message = f"Required argument is missing; expected {expected}."
            elif kind in ("unexpected_keyword_argument", "extra_forbidden"):
                message = "Unknown argument; expected a declared tool parameter."
            else:
                value = arguments.get(location[0]) if location else None
                # Quantity fields document a numeric-with-unit example. Text choices,
                # paths and JSON records remain ordinary input errors.
                quantity = _QUANTITY_EXAMPLE.search(field.get("description", "")) or _QUANTITY_EXAMPLE.fullmatch(
                    repr(field.get("default", ""))
                )
                if kind == "string_type" and type(value) in (int, float) and quantity:
                    family = "units.bare_number"
                    expected = "string containing a number and its unit"
                message = f"Invalid argument; expected {expected}."
            result = failure(
                family=family, message=message, engine=ENGINE, version=__version__,
                inputs=clean_inputs(arguments),
                details={"param": param},
            )
            return MCPToolResult(structured_content=result.model_dump(mode="json"))


# Read by every client at connect time; assistants that never open a skill still see these rules.
# Keep it under MAX_INSTRUCTIONS_BYTES (tests/test_gateway_instructions.py enforces the cap and the seven rules).
MAX_INSTRUCTIONS_BYTES = 2500
INSTRUCTIONS = (
    "AI-callable open-source engineering toolkit. Tools are named <domain>_<tool>. Results are NOT engineering-approved: "
    "every result has review_status='unreviewed'.\n"
    "Hard rules:\n"
    "1. Every physical input needs a unit string ('100 mm', '210 bar'); bare numbers are refused.\n"
    "2. ASK before computing when an input that decides the verdict is missing: limit temperature, safety factor, loads, "
    "service factor, reliability, material grade, datasheet values. Never invent them. Label any value you recall "
    "'from memory, not a tool result'.\n"
    "3. Every number that decides a result comes from a tool call. Your own unit scaling or sums are allowed but must be "
    "labelled 'my arithmetic'.\n"
    "4. Before the first call, if you have a Skill tool, load the `eng-toolkit` skill and the domain skill that matches the job: "
    "eng-structural-check, eng-dfm-review, eng-fatigue-check, eng-motor-drive-sizing, eng-optimize, eng-plot-results, "
    "eng-3d-view, eng-thermal-check, eng-control-tuning, eng-signal-analysis, eng-flow-network, eng-drawing, eng-pcb-review, "
    "eng-circuit-sim, eng-digital-synthesis, eng-machine-design, eng-tolerancing.\n"
    "5. Charts and 3D pictures: pass format=\"png\" (or \"both\"), or keep the default preview. Give the person the exact PNG path "
    "and describe the picture in words. Never hand-write SVG, HTML or ASCII charts.\n"
    "6. Read the result's own fields before you say 'passed': warnings, data.assumptions, not_performed, counts that sit at a cap "
    "(such as at_kicad_limit), and measurements that are 0 or null, which mean 'not checked'.\n"
    "7. A refusal (ok=false) is a result: explain it, fix the model or the input, never bypass it.\n"
    "Result contract: {ok, value, values, artifacts, provenance, warnings, error, review_status}."
)

gateway = FastMCP(
    "eng-toolkit",
    version=__version__,
    instructions=INSTRUCTIONS,
)
gateway.add_middleware(ArgumentRefusals())

DOMAINS = all_domains()
for _domain in DOMAINS:
    gateway.mount(_domain.server, namespace=_domain.name)


def _engine_rows(dists: tuple[str, ...]) -> list[dict[str, Any]]:
    rows = []
    for dist in dists:
        version, text = installed_license(dist)
        known = KNOWN.get(dist)
        rows.append(
            {
                "dist": dist,
                "version": version,
                "installed": version is not None,
                "declared_license": known.license if known else None,
                "bundled_license": (known.bundled_license or None) if known else None,
                "detected_license": text,
                "tier": known.tier if known else classify_license(text),
                "optional": known.optional if known else False,
            }
        )
    return rows


def _file_reads_note(dirs: list[Any] | None) -> str:
    if dirs is None:
        return f"unrestricted: tools read any path this user can read; set {ENV_READ_DIRS} to limit them, always when serving over http"
    if not dirs:
        return (
            f"{ENV_READ_DIRS} is set but empty, which allows the workspace only: no other directory can be read "
            f"(unset the variable to remove the limit, or list the directories to allow)"
        )
    return f"reads are limited to the {len(dirs)} configured director(ies) and the workspace"


def _gateway_result(*, ok: bool, data: dict[str, Any], warnings: list[str], inputs: dict[str, Any]) -> ToolResult:
    return ToolResult(
        ok=ok,
        data=data,
        provenance=Provenance(engine=ENGINE, version=__version__, inputs_hash=inputs_hash(inputs)),
        warnings=warnings,
    )


@gateway.tool(
    name="toolkit_list_tools",
    description="List installed domains, the engines (with versions and license tiers) behind them, and every tool name.",
)
async def toolkit_list_tools() -> ToolResult:
    tools = await gateway.list_tools()
    tool_names = sorted(t.name for t in tools)
    domains = [
        {
            "name": d.name,
            "title": d.title,
            "description": d.description,
            "engines": _engine_rows(d.engines),
            "tools": [f"{d.name}_{t}" for t in d.tools],
        }
        for d in DOMAINS
    ]
    data = {
        "toolkit_version": __version__,
        "domains": domains,
        "gateway_tools": [n for n in tool_names if n.startswith("toolkit_")],
        "tools": tool_names,
    }
    return _gateway_result(ok=True, data=data, warnings=[], inputs={})


@gateway.tool(
    name="toolkit_doctor",
    description=(
        "Health check: Python version, installed engine versions vs. declared licenses and policy "
        "tiers, workspace writability, pint sanity, and (optionally) a license scan of the whole "
        "environment flagging GPL/AGPL/unknown distributions."
    ),
)
def toolkit_doctor(
    scan_env: Annotated[
        bool,
        Field(description="Also scan every installed package for GPL/AGPL/unknown licenses (slower). Default true."),
    ] = True,
) -> ToolResult:
    warnings: list[str] = []
    hard_failures: list[str] = []

    # Python
    py_ok = sys.version_info[:2] >= MIN_PYTHON
    if not py_ok:
        hard_failures.append(f"Python {platform.python_version()} < {'.'.join(map(str, MIN_PYTHON))}")

    # Declared engines
    deps = []
    for engine in KNOWN.values():
        version, text = installed_license(engine.dist)
        detected = classify_license(text)
        status = "ok"
        if version is None and engine.optional:
            status = "not-installed (optional)"
        elif version is None:
            status = "missing"
            hard_failures.append(f"dependency '{engine.dist}' is not installed")
        elif violates_policy(engine):
            status = "policy-violation"
            hard_failures.append(
                f"'{engine.dist}' {version} is installed but declared {engine.tier} ({engine.license}): "
                f"{POLICY[engine.tier]}"
            )
        elif detected != engine.tier and not tier_explained_by_bundle(engine, detected):
            status = "license-mismatch"
            warnings.append(
                f"'{engine.dist}': declared {engine.license} ({engine.tier}) but metadata says "
                f"{text!r} ({detected}); review before use"
            )
        deps.append(
            {
                "dist": engine.dist,
                "role": engine.role,
                "domains": list(engine.domains),
                "version": version,
                "declared_license": engine.license,
                "declared_tier": engine.tier,
                "bundled_license": engine.bundled_license or None,
                "optional": engine.optional,
                "detected_license": text,
                "detected_tier": detected,
                "status": status,
            }
        )

    # Programs the user installs themselves and the toolkit starts as separate processes
    external_tools = []
    for tool in EXTERNAL.values():
        row = probe_external(tool)
        if not row["found"] and not tool.optional:
            hard_failures.append(f"program '{tool.name}' was not found: {row.get('reason')}")
        external_tools.append(row)

    # Workspace
    ws = Workspace()
    ws_ok = ws.is_writable()
    if not ws_ok:
        hard_failures.append(f"workspace {ws.root} is not writable")

    # Units sanity
    try:
        from eng_toolkit.units import parse_quantity

        pa = float(parse_quantity("1 bar", name="probe", dimension="pressure").to("Pa").magnitude)
        units_ok = abs(pa - 1e5) < 1e-6
    except Exception as exc:  # noqa: BLE001
        units_ok = False
        hard_failures.append(f"pint sanity check failed: {exc}")

    # LLM-free guarantee: the toolkit is a pure tool server, usable from any MCP client
    # (Claude, Codex, Cursor, LM Studio, Ollama...) without API keys.
    llm_sdks_found = [d for d in LLM_SDK_DISTS if installed_license(d)[0] is not None]
    if llm_sdks_found:
        warnings.append(
            "LLM SDKs present in the environment: " + ", ".join(llm_sdks_found)
            + " -- the toolkit must not call them"
        )
    api_keys_set = [k for k in LLM_API_KEY_ENV if os.environ.get(k)]

    # Environment license scan
    scan: dict[str, Any] | None = None
    if scan_env:
        rows = scan_environment()
        by_tier: dict[str, int] = {}
        for r in rows:
            by_tier[r["tier"]] = by_tier.get(r["tier"], 0) + 1
        flagged = [r for r in rows if r["tier"] in (TIER_GPL, TIER_FORBIDDEN)]
        unknown = [r for r in rows if r["tier"] == TIER_UNKNOWN]
        for r in flagged:
            msg = f"'{r['dist']}' {r['version']} is {r['tier']} ({r['license']})"
            if r["tier"] == TIER_FORBIDDEN:
                hard_failures.append(msg)
            else:
                hard_failures.append(msg + " -- GPL code must not be installed in the toolkit environment")
        if unknown:
            warnings.append(
                "license could not be determined for: " + ", ".join(r["dist"] for r in unknown)
            )
        scan = {
            "distributions": len(rows),
            "by_tier": by_tier,
            "flagged": flagged,
            "unknown": unknown,
            "permissive_count": by_tier.get(TIER_PERMISSIVE, 0),
        }

    data = {
        "toolkit_version": __version__,
        "python": {"version": platform.python_version(), "executable": sys.executable, "ok": py_ok},
        "platform": platform.platform(),
        "dependencies": deps,
        "external_tools": external_tools,
        "workspace": {"root": str(ws.root), "writable": ws_ok},
        "file_reads": {
            "restricted": read_dirs() is not None,
            "directories": None if read_dirs() is None else len(read_dirs() or []),
            "env": ENV_READ_DIRS,
            "note": _file_reads_note(read_dirs()),
        },
        "units": {"pint_ok": units_ok},
        "llm_free": {
            "calls_llm": False,
            "requires_api_key": False,
            "llm_sdks_installed": llm_sdks_found,
            "llm_api_keys_in_env": api_keys_set,
        },
        "transports": ["stdio", "http"],
        "license_policy": POLICY,
        "license_scan": scan,
        "domains": [d.name for d in DOMAINS],
        "failures": hard_failures,
    }
    return _gateway_result(
        ok=not hard_failures,
        data=data,
        warnings=warnings + hard_failures,
        inputs={"scan_env": scan_env},
    )


DEFAULT_HTTP_HOST = "127.0.0.1"
DEFAULT_HTTP_PORT = 8765
DEFAULT_HTTP_PATH = "/mcp"


def main(argv: list[str] | None = None) -> None:
    """Console entry point.

    ``eng-toolkit serve --transport stdio``                 (default) for clients that launch a subprocess
    ``eng-toolkit serve --transport http [--host --port]``  streamable HTTP at http://host:port/mcp
    ``eng-toolkit doctor [--no-scan]``                      print the toolkit_doctor report, exit 0/1

    Plain ``eng-toolkit`` with no arguments serves stdio, so ``.mcp.json`` stays a one-liner.
    The toolkit never calls an LLM and needs no API key: it is a pure tool server.
    """
    import argparse
    import json

    parser = argparse.ArgumentParser(prog="eng-toolkit", description="eng-toolkit MCP gateway")
    sub = parser.add_subparsers(dest="command")

    serve = sub.add_parser("serve", help="run the MCP gateway")
    serve.add_argument("--transport", choices=["stdio", "http"], default="stdio")
    serve.add_argument("--host", default=DEFAULT_HTTP_HOST, help=f"http only (default {DEFAULT_HTTP_HOST})")
    serve.add_argument("--port", type=int, default=DEFAULT_HTTP_PORT, help=f"http only (default {DEFAULT_HTTP_PORT})")
    serve.add_argument("--path", default=DEFAULT_HTTP_PATH, help=f"http only (default {DEFAULT_HTTP_PATH})")

    doctor = sub.add_parser("doctor", help="run toolkit_doctor and exit 0 (healthy) / 1")
    doctor.add_argument("--no-scan", action="store_true", help="skip the environment license scan")

    # Backward-compatible flags from the first commit.
    parser.add_argument("--doctor", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--no-scan", action="store_true", help=argparse.SUPPRESS)

    args = parser.parse_args(argv)

    if args.command == "doctor" or args.doctor:
        # FastMCP >= 4 returns the plain function from @tool; older versions return a FunctionTool with .fn
        doctor_fn = getattr(toolkit_doctor, "fn", toolkit_doctor)
        report = doctor_fn(scan_env=not args.no_scan)
        print(json.dumps(json_safe(report.model_dump(mode="json")), indent=2, ensure_ascii=False, allow_nan=False))
        raise SystemExit(0 if report.ok else 1)

    transport = getattr(args, "transport", "stdio")
    if transport == "http":
        gateway.run(
            transport="http",
            host=args.host,
            port=args.port,
            path=args.path,
            show_banner=False,
        )
    else:
        gateway.run(transport="stdio", show_banner=False)


if __name__ == "__main__":  # pragma: no cover
    main()
