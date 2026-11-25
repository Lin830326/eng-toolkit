# Adding a domain

A domain is one FastMCP sub-server mounted on the gateway under a namespace (`calc` gives `calc_<tool>`). Domains and the
engines they bring are **discovered**, so a new domain lives in its own files and edits none of the shared ones. Several
people can add domains on separate branches and the branches stay disjoint.

## What you create (all new files)

| File | Contents |
|---|---|
| `eng_toolkit/domains/<name>/__init__.py` | `DOMAIN = DomainInfo(name=..., title=..., description=..., engines=(<dist names>), server=..., tools=(<tool names without the namespace>))` |
| `eng_toolkit/domains/<name>/tools.py` (any module names) | the FastMCP server and its tools; wrap each tool body in `runner.run_tool`, parse every quantity with `units.parse_quantity` |
| `eng_toolkit/domains/<name>/engines.py` | `ENGINES = (Engine("<dist>", "<license>", <tier>, "<role>", ("<name>",), optional=True, extra="<extra>"), ...)` for each engine the domain adds; imports only `eng_toolkit.licenses`; `EXTERNAL_TOOLS = (NGSPICE, ...)` for each program the user installs that the domain starts as a separate process (descriptors in `eng_toolkit.external_catalog`, or an inline `ExternalTool`; see `docs/external/design.md`) |
| `tests/test_<name>_*.py` | tests; keep numbers in hand calculations, not in the tool under test |
| `docs/tools/<name>.md` | the tool reference: what each tool computes, its inputs, an example call with the expected numbers, what it does not cover |
| `docs/<name>-design.md` | decisions, alternatives, what is not verified |

Nothing else needs to change for the tool to appear: the gateway mounts every package under `eng_toolkit/domains/` that exposes a
`DOMAIN`, and `toolkit_list_tools`, `toolkit_doctor`, the license check and the extras check read the discovered registries.

## What you may still touch, and how to keep it conflict-free

Two shared files cannot be avoided, both because they are lists of the whole project:

* `pyproject.toml`: add your own extra under `[project.optional-dependencies]` (and pin versions there). Put it in alphabetical
  position, one block per extra, so two branches do not edit the same lines. A core dependency (rare) goes in `dependencies`.
* `uv.lock`: never resolve it by hand. After any merge that touched `pyproject.toml`, run `uv lock` and commit the result.

The tool reference of a domain is its own file, `docs/tools/<name>.md`: one `## ` section per tool. The two READMEs
(`README.md` in English, `README.zh-TW.md`) each take **one table row** per domain under "Domains" / "領域與工具", in the
order of `toolkit_list_tools`; add your row and do not edit the rows of other domains. `tests/test_docs_tools.py` fails
when a domain or a tool is missing from either place, so the READMEs cannot fall behind the code.

## Budgets and rules the tests enforce

* At most 6 tools per domain (the gateway adds 2). Few coarse tools, every parameter a basic JSON type with a description.
* An engine's license tier must be `permissive` for in-process use; `lgpl-dynamic` only for dynamic linking; `gpl-isolated` only as a
  separate process; `forbidden` never installed (`tests/test_licenses.py`, `toolkit_doctor`).
* Every file the tool reads goes through `JobHandle.input_file` (opens once, checks the open file, tools read a copy).
* Results follow the one return contract and say `review_status: unreviewed`.

## Checks before you hand a branch over

```
uv run --no-sync python -m pytest -q
uv run --no-sync python -m eng_toolkit doctor
```

(`python -m`, because a new worktree's environment often lacks the project's console script.)
