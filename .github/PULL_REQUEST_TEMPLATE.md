## What this changes

<!-- One or two sentences. Link the issue: Fixes #... -->

## How it was verified

<!-- The independent check behind each new number: hand calculation, named reference, or second engine. Say what is NOT verified. -->

## Checklist

- [ ] `uv run --no-sync python -m pytest -q` passes locally.
- [ ] `uv run --no-sync python -m eng_toolkit doctor` exits 0 locally.
- [ ] Everything here is my own work or comes from a permissively licensed source that I name above. Nothing belongs to an employer or a client.
- [ ] No new dependency, or: its license and tier are registered in the domain's `engines.py` and `uv.lock` was regenerated with `uv lock`.
- [ ] Every physical input is parsed with `units.parse_quantity`, and each tool body runs inside `runner.run_tool`.
- [ ] The tool's section in `README.md` and the domain's design note are updated.
