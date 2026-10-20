# DWG input through LibreDWG's dwg2dxf

Slice S9 of `docs/geometry-drawing/design.md`. `drawing_read` and `drawing_convert` accept a DWG when the user has installed LibreDWG's `dwg2dxf`; the toolkit runs it as a separate process and feeds the DXF it writes through the existing DXF paths. No new tool: the drawing domain still has 3 of its budget of 6.

**Not verified against a real LibreDWG.** LibreDWG is not installed on the development machine and was not installed for this work. Every test uses a fake `dwg2dxf` (below). What the real program prints, how it exits on a damaged file, whether `-o out in` is the argument shape of every LibreDWG release, and how faithful its DXF is, are assumptions from its documentation. See "Not done, not verified".

## Code

| File | Contents |
|---|---|
| `eng_toolkit/external.py`, `external_catalog.py`, `licenses.ExternalTool` | the shared layer (`docs/external/design.md`): this slice only uses it. The descriptor is `external_catalog.DWG2DXF` |
| `eng_toolkit/domains/drawing/engines.py` | `EXTERNAL_TOOLS = (DWG2DXF,)`: registers the program with the doctor, no shared file edited |
| `eng_toolkit/domains/drawing/dwg.py` | everything specific to DWG: header and release, the argument list, error mapping, output validation, the report `data.dwg` |
| `eng_toolkit/domains/drawing/tools.py` | `read` and `convert` call `dwg` for a `.dwg` and for a DWG header inside a `.dxf`; descriptions and parameter text updated |
| `eng_toolkit/runner.py` | `JobHandle.job_dir` and `JobHandle.engine_versions` (a version of something that is not a Python distribution goes into `provenance.engine_versions`) |
| `tests/fake_dwg2dxf.py`, `tests/dwg_fixtures.py` | the fake program and its shim (the process helpers come from `tests/exe_fixtures.py`) |
| `tests/test_drawing_dwg.py` (54) | the tests of this slice; the existing DWG tests of `test_drawing_read.py`, `test_drawing_convert.py` and `test_drawing_convert_hostile.py` now hide any installed program |

`dxf.py` keeps a last guard: a DWG that reaches the DXF converter directly is still `engine.unavailable`, now with a message that no longer promises a future slice.

## The licence boundary

LibreDWG is GPL-3.0. The toolkit (Apache-2.0) does not contain, import, link or ship it. It starts a program the user installed, as a separate process, with a fixed argument list, and exchanges only files, the arrangement `licenses.TIER_GPL` (`gpl-isolated`) describes. Concretely:

* `licenses.EXTERNAL["dwg2dxf"]` declares it `GPL-3.0-or-later`, tier `gpl-isolated`, optional, with its project page and the variable that names it. It is not in `KNOWN` and not in `pyproject.toml`; no extra installs it. A test asserts that no module of the package imports a name containing `libredwg` or `dwg2dxf`, that no `.dll` or `dwg2dxf*.exe` is inside the package and that `pyproject.toml` does not mention it.
* The install instructions in the error name the project page and the environment variable. They never contain a shell command, a download URL or a package-manager line.
* The toolkit does not read LibreDWG's source or documentation into its own code. The argument list (`-o <output> <input>`) is the program's command-line interface, as documented for `dwg2dxf`.

## How a DWG is read

1. `read` or `convert` is called with a `.dwg`. For the extension `.dwg` the program is located **before the file is opened** (`dwg.require_tool`), so a machine without it costs nothing and leaves `provenance.input_files` empty.
2. `JobHandle.input_file` opens the file once, applies the read-directory allow-list and the size cap (the DXF cap, 50 MB by default) and keeps a copy in `input/`, exactly as for a DXF.
3. `needs_conversion`: true for the extension `.dwg` and for any `.dxf` whose first six bytes are `AC10` and two digits. A real DXF never starts that way (a text DXF starts with a group code and `SECTION`, a binary DXF with `AutoCAD Binary DXF`), so a real DXF is never sent to the program, and a DWG renamed `.dxf` is. A `.pdf` is never examined.
4. `release_of` reads the version from the first six bytes: `AC1009` R12, `AC1015` R2000, `AC1018` R2004, `AC1021` R2007, `AC1024` R2010, `AC1027` R2013, `AC1032` R2018. `input.invalid` when the file does not start with a DWG header (with a hint when it is really a DXF); `input.unsupported_format` for a DWG release that is older or unknown: `AC1006` R10, `AC1012` R13, `AC1014` R14 by name, anything else `AC10xx` as unknown. The message lists the supported releases and says to save as DWG 2000 or later or as DXF.
5. The input is copied to `<job>/dwg2dxf/input.dwg` and the program is started on it. The fixed names matter: the caller's file name (spaces, CJK characters, a leading dash) never reaches a C program that may not be able to open it.
6. The DXF at `<job>/dwg2dxf/output.dxf` is validated (below), its sha256 recorded, and then **the path of that DXF takes the place of the input** in the existing code: `drawing_read` hands it to `dxf_read.read` and `drawing_convert` to `dxf.convert_file`, both in their limited child processes, with every limit and hostile-file protection unchanged. `tests/test_drawing_dwg.py` shows a DXF with a 1e200 coordinate meeting the same refusal as a DXF file.

`data.dwg` (also in `drawing_read.json`, and `data.file` of `drawing_read` names the DWG with `source_format: "dwg"`):

```
source_format "dwg", dwg_version "AC1032", dwg_release "R2018",
converted_by "LibreDWG dwg2dxf, a separate program installed by the user (GPL-3.0)",
engine_version "<first dotted number dwg2dxf --version prints>" or "unknown", found_via ("env: ENG_TOOLKIT_DWG2DXF", "path: <dir>" or "location: <pattern>"), tool_path,
arguments ["-o", "<job>/dwg2dxf/output.dxf", "<job>/dwg2dxf/input.dwg"], seconds,
limits {timeout_s, memory_mb, max_output_bytes, memory_cap_applied},
converted_dxf {path, bytes, sha256}
```

The tool version also goes into `provenance.engine_versions["dwg2dxf"]`, and every successful result carries the warning that the drawing went through LibreDWG, which rebuilds the DXF from a reverse-engineered reading of a closed format, so content can be missing or different.

## Locating and running the program

Done by the layer (`docs/external/design.md`); what this slice adds is the descriptor `DWG2DXF` and how it is called.

* **Locating** (`external.locate`): `ENG_TOOLKIT_DWG2DXF` (an absolute path to a file; a wrong value is "not found" and `PATH` is not consulted), then `PATH` for `dwg2dxf` / `dwg2dxf.exe` (relative and empty entries skipped; on Windows only `.exe`/`.com`, so a `.cmd` shim is found only through the variable), then the places the descriptor lists (the usual Unix directories, `C:\Program Files\LibreDWG\bin`, `C:\libredwg\bin`). Never from the input file or anything in a document.
* **Call**: `external.run(DWG2DXF, ["-o", <job>/dwg2dxf/output.dxf, <job>/dwg2dxf/input.dwg], workdir=<job>, ...)` with `Path` arguments, so the layer checks they are inside the job directory. The environment is the layer's: no tokens, `PATH` = the program's folder and the system directories, `TEMP`/`HOME`/`APPDATA` inside the job directory; the descriptor keeps `LD_LIBRARY_PATH` (a LibreDWG in `/usr/local/lib`). Standard input is the null device.
* **Limits**: time 60 s (`ENG_TOOLKIT_DWG2DXF_TIMEOUT_S`), memory 2048 MB (`ENG_TOOLKIT_DWG2DXF_MEMORY_MB`), bytes written below the job directory = the DXF cap (50 MB; it follows `ENG_TOOLKIT_READ_MAX_MB` for `drawing_read` and `ENG_TOOLKIT_CONVERT_MAX_MB` for `drawing_convert`), checked every 50 ms while it runs, and stdout/stderr 1 MB each. Time and memory are not changed by the layer's defaults; memory is a Job Object on Windows, `prlimit` on Linux, none on macOS (`limits.memory_cap_applied` says which, with a warning).
* **Version**: `external.version(DWG2DXF, ...)` runs `dwg2dxf --version` and takes the first dotted number (`0.13.3`, `0.12.5.6012`); `unknown` when it prints none. It is in `data.dwg.engine_version` and `provenance.engine_versions["dwg2dxf"]`.

## Errors

| Family | When |
|---|---|
| `engine.unavailable` | not found, or the variable names no file. Message: what was looked at, the project page, `ENG_TOOLKIT_DWG2DXF` and `PATH`, and that a DXF can be given instead. Details: `tool`, `env_var`, `project`, `reason`, `path` |
| `engine.failure` | non-zero exit (message: the exit code and the end of stderr, at most 300 characters, control characters removed, the job directory path replaced by `<job>`); exit 0 without an output file; the program could not be started |
| `engine.timeout` | time limit; says which variable raises it |
| `drawing.too_large` | the DXF outgrew the cap |
| `drawing.corrupt` | the output is empty, is not a regular file, or does not start like a DXF (`SECTION` after group code 0, optional `999` comments, or `AutoCAD Binary DXF`) |
| `input.invalid` | the file named `.dwg` has no DWG header |
| `input.unsupported_format` | a DWG release that is older or unknown |
| `input.path_not_allowed`, `input.file_not_found`, `drawing.too_large` (the DWG itself over the cap) | as for any input file |

Text from the program's stderr is untrusted (it can echo names from the drawing): it is shown quoted, limited and cleaned, and it is data.

## Doctor

`drawing/engines.py` registers `DWG2DXF`, so `toolkit_doctor` lists it in `data.external_tools` (`found`, `path`, `found_via`, `version`, `status`, `reason`, `install`, tier `gpl-isolated`) with or without the program; an absent optional program is not a failure.

## Tests

`tests/fake_dwg2dxf.py` is the fake: a small Python script run by the Python interpreter. `tests/dwg_fixtures.install_fake` writes a shim and points `ENG_TOOLKIT_DWG2DXF` at it: on Windows a `.cmd` file (`"python" "fake_dwg2dxf.py" "config.json" %*`), elsewhere a `/bin/sh` script with `exec`. The first argument is a JSON configuration (`mode`, the DXF to copy, the version text, ...); everything after it is what the toolkit sent, written with the working directory, the full environment, the process id, standard input and the state of the input file to a log the tests read. Modes: `copy`, `fail`, `sleep`, `garbage`, `empty`, `nothing`, `huge`, `notdxf_big`, `version_broken`.

Covered (`tests/test_drawing_dwg.py`, 54 tests; the layer has its own in `tests/test_external*.py`):

* success for `drawing_read` and `drawing_convert` (PDF: 100 mm line = 283.465 pt, page 120 x 20 mm) directly and through `fastmcp.Client` on the gateway, the converted DXF recorded with a sha256, provenance names the DWG and the tool version;
* the argument list received equals `["-o", <job>/dwg2dxf/output.dxf, <job>/dwg2dxf/input.dwg]` and nothing else, one `--version` call precedes it; the working directory is the job directory; the input it sees is a byte-for-byte copy; standard input is empty; the original DWG is unchanged (hash and mtime);
* the environment: five secret variables, an `ENG_TOOLKIT_*` variable and everything else are absent, only the layer's variables remain (system variables, `PATH`, and `TMP`/`TEMP`/`HOME`/`APPDATA` pointing into the job directory); a file named `圖面 -x 設計.dwg` arrives as `input.dwg`;
* all seven releases accepted and named; `AC1006`, `AC1012`, `AC1014`, `AC1002`, `AC1033`, `AC1099` refused by name without starting the program; non-DWG content under `.dwg` is `input.invalid`; a DXF named `.dwg` is refused with a hint;
* routing: a DWG renamed `.dxf` is converted, a real DXF (text and binary) and a PDF never reach the program (the log is empty, not even a version query);
* absence: no variable and an empty `PATH`, a variable naming nothing, a relative variable, a program in the working directory on a `PATH` of `""` and `"."`: `engine.unavailable`, DWG not opened, DXF still works, the gateway answers;
* failure: exit code and stderr tail, a 5000-character stderr cut and free of the job directory path in all three spellings, exit 0 with no file, garbage, empty file, a fake ELF: `drawing.corrupt`; timeout (2 s, a 40 s sleeper) stops it and its process is gone; a program writing 80 MB is stopped near 1 MB while still writing; a DXF with an absurd coordinate meets the same refusal through a DWG as a file;
* limits and version: defaults and environment overrides (bad values fall back), memory cap applied on Windows and Linux, a version from the first dotted number, a program found in a known location, `unknown` when it prints nothing or exits 3;
* the doctor row with and without the program; the descriptions mention the program, the variable, the releases, `engine.unavailable` and no longer say DWG is not read;
* the layer's own behaviour (locating, environment, process-tree kill, written-byte cap, memory cap) is tested in `tests/test_external.py` and, with the real ngspice, Yosys, Gmsh, CalculiX and KiCad, in `tests/test_external_real.py`.

A real-binary test exists (`test_the_real_dwg2dxf_converts_a_sample_drawing`) and is skipped unless `ENG_TOOLKIT_DWG2DXF` or `PATH` finds a program **and** `ENG_TOOLKIT_TEST_DWG` names a DWG; the repository holds no DWG sample.

## Not done, not verified

* **Never run against a real LibreDWG.** Unverified assumptions: the argument shape `-o <out> <in>` for the releases users have; the exit code on damaged or partly unsupported files (the toolkit treats any non-zero code as failure, so a LibreDWG run that exits non-zero but writes a usable DXF is refused); what `--version` prints; that stderr warnings on success are harmless (they are ignored); the fidelity of its DXF. The DXF it writes is whatever release LibreDWG chooses; `dxf_read` and `convert_file` accept R12 to R2018.
* R13 and R14 DWG (`AC1012`, `AC1014`) are refused although LibreDWG can read them: the slice was specified for R12 and R2000 to R2018 and nothing could be tested against real files. Adding them is a one-line change in `dwg.SUPPORTED` once someone has verified them with the real program.
* AutoCAD 2018 to 2025 drawings are all `AC1032`; the toolkit cannot tell them apart and LibreDWG's support for the newest features may lag. DWG written by tools other than AutoCAD is whatever LibreDWG accepts.
* The Linux and macOS branches of the layer were written but not run (see `docs/external/design.md`); the POSIX shim of the fake did not run either.
* A program can still write anywhere the server's user can write: there is no file-system sandbox, only the scrubbed environment, the working directory and the limits. Install LibreDWG from its project and keep it up to date; it parses an untrusted binary format in C.
* DWG writing, DWG to DWG, external references of a DWG, and drawing the DWG's paper-space layouts are out of scope, as for a DXF.
* The limits of the layer apply as documented there: the memory cap holds a few milliseconds after the start, and nothing is a sandbox.

## What the layer did not fit (feedback for `docs/external/design.md`)

This slice is the first user of the layer. Nothing needed a change of its public API. Points that came up:

* **Version probe.** The layer returns the matched number (`0.13.3`), not the program's first line, so `engine_version` lost the program name and any build text. The regex is the descriptor's; if the real `--version` prints something the default dotted-number regex cannot read, the descriptor needs a regex, not code.
* **Arguments.** `Path` arguments must lie inside the job directory, which fits (the DWG is copied to `dwg2dxf/input.dwg`). The fixed ASCII file names are this module's own choice, because a C program on Windows may not open a non-ASCII path.
* **Exit codes.** The layer's `check=True` message quotes at most 300 characters of stderr; this module uses `check=False` instead so its message can also say which DWG it was and how to read the exit code. A real LibreDWG that exits non-zero with a usable output is refused here; that decision is this module's (see below), not the layer's.
* **Output cap.** The layer counts bytes written below the job directory, so `tmp/` and `home/` files of the program count too; for dwg2dxf they are empty. The cap has no way to say "only this file".
* **Doctor row.** `found_via` is `"env: ENG_TOOLKIT_DWG2DXF"` and the row has `install` text when absent; nothing missing.
* **Test isolation.** A test that expects the program to be absent must hide the descriptor's `locations` as well as the variable and `PATH`, or a LibreDWG installed in a known place is found; `tests/dwg_fixtures.remove_tool` does that for the DWG code and the doctor. Other domains' tests will need the same; a shared helper in `tests/exe_fixtures.py` would avoid each copying it.
