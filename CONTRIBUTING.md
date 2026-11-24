# Contributing to eng-toolkit

Thank you for helping. eng-toolkit is a set of MCP servers that lets an AI agent call open-source engineering engines and
get results that carry units, can be reproduced, and name their source. Issues and pull requests are welcome in English
or 繁體中文.

## Rules that every change must keep

These are what make the results trustworthy. The tests enforce most of them.

1. **Clean room.** Do not add code, data, models, drawings or branding that belong to an employer or a client. Add only
   what you wrote yourself or what a permissive license lets you add, and say where it came from.
2. **License tiers.** A dependency imported in-process must be permissive (Apache, MIT, BSD, ISC, PSF). LGPL is allowed
   only as an unmodified dynamic link. GPL programs run only as a separate process, exchanging files. AGPL,
   non-commercial and source-available code is never installed. `eng_toolkit/licenses.py` holds the registry, and
   `toolkit_doctor` and `tests/test_licenses.py` check it.
3. **Units.** Every physical input is a string with a unit (`"100 mm"`). Parse it with `units.parse_quantity`. Bare
   numbers are refused.
4. **One return contract.** Wrap each tool body in `runner.run_tool`. Results say `review_status: "unreviewed"`;
   refusals name the argument in `error.details.param` as a JSON pointer.
5. **No LLM calls.** The toolkit computes; it never calls a model.
6. **Numbers are checked against something independent.** A test compares the tool with a hand calculation, a formula
   from a named reference, or a second engine. A test that only repeats the tool's own output proves nothing.

## Set up

You need [uv](https://docs.astral.sh/uv/). It installs Python 3.12 for you.

```bash
git clone https://github.com/Lin830326/eng-toolkit.git
cd eng-toolkit
uv sync --all-groups --extra scan --extra drawing --extra png
uv run --no-sync python -m pytest -q
uv run --no-sync python -m eng_toolkit doctor
```

Some domains drive programs you install yourself (KiCad, ngspice, Gmsh, CalculiX, Yosys). Without them the tools still
list, calls return `engine.unavailable` with the install hint, and their tests skip.

## Ways to help

| Contribution | Where to start |
|---|---|
| Report a wrong number or a confusing refusal | Open a bug report. Include the tool name, the exact arguments, the result, and the value you expected with its source. |
| Add a verification case | Add a test with a hand calculation or a textbook example (cite the book, edition and example number; do not copy its text). |
| Add a tool to an existing domain | Read that domain's `docs/<domain>/` design notes, then the rules in [docs/adding-a-domain.md](docs/adding-a-domain.md). A domain holds at most 6 tools. |
| Add a domain | Follow [docs/adding-a-domain.md](docs/adding-a-domain.md). Domains are discovered, so a new one lives in its own files. Open an issue first so the scope and the engine's license are agreed before you write code. |
| Improve a workflow skill | The Claude Code plugin is in `plugin/`. `tests/` checks every skill and command frontmatter. |
| Improve the documentation | Fix what misled you. The README is in 繁體中文; design notes are in English or 繁體中文. |

## Pull requests

1. Branch from `main`. Keep one pull request to one topic.
2. Run the tests and the doctor locally (commands above). Both must pass before you open the pull request.
3. If you changed `pyproject.toml`, run `uv lock` and commit `uv.lock`. Never edit `uv.lock` by hand.
4. Add or update the tool's section in `README.md` and the domain's design note. Say what is not verified.
5. Write the commit subject as one English line that says what changed and why it matters, as the existing history does.
6. Fill in the pull request template.

A maintainer reviews every pull request. Reviews check the six rules above first, then the numbers, then the code.

## License of contributions

The repository has two licenses, split by directory:

| Path | License |
|---|---|
| `plugin/` (the Claude Code plugin: skills, commands, hooks) | MIT, see [plugin/LICENSE](plugin/LICENSE) |
| everything else | Apache-2.0, see [LICENSE](LICENSE) |

By submitting a contribution you agree that it is licensed under the license of the directory it lands in, and you
confirm that you have the right to submit it.

## Security

Do not open a public issue for a vulnerability. See [SECURITY.md](SECURITY.md).
