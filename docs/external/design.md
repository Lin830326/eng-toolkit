# External programs: finding and running software the user installed

The shared layer under every domain that starts a native program: LibreDWG's `dwg2dxf` (drawing), ngspice (spice), Yosys (digital), Gmsh and CalculiX (fem), KiCad (pcb). Code: `eng_toolkit/external.py` (find, run, version, doctor row), `eng_toolkit/external_catalog.py` (the descriptors, data only), `ExternalTool` and `EXTERNAL` in `eng_toolkit/licenses.py` (the descriptor type and the registry). Tests: `tests/test_external.py` (fake program, 34), `tests/test_external_registry.py` (registry, catalog, doctor, 11), `tests/test_external_real.py` (the real programs, skipped when absent, 15). No tool uses it yet; the DWG slice is the first.

## The licence boundary

A program is never imported, linked, bundled or fetched by the toolkit. It is started the way a person starts it from a shell, with files in and files out. That is what the tier `gpl-isolated` in `licenses.py` means, and the layer is the only place that starts one. The `ExternalTool` descriptor carries `license` and `tier`: `gpl-isolated` for dwg2dxf, Gmsh, CalculiX and kicad-cli; `permissive` for ngspice (BSD-3-Clause) and Yosys (ISC), which are native programs we still only start as separate processes. Install hints give the project page and the two ways to say where the program is, never a download or install command. A test asserts that no module of the package imports a name that belongs to one of these programs and that no `.dll`/`.exe` is shipped and `pyproject.toml` does not list them.

## The descriptor (`licenses.ExternalTool`, frozen)

| Field | Meaning |
|---|---|
| `name`, `license`, `tier`, `role`, `project`, `domains`, `optional` | identity, licence and tier as declared, one line of purpose, the project page, who uses it; `optional=False` makes a missing program fail the doctor |
| `env_var` | `ENG_TOOLKIT_<NAME>`: the full path of the program |
| `executables` | exact file names, tried in this order in each directory (`ngspice_con.exe` before `ngspice`) |
| `locations` | well-known install directories probed after `PATH` |
| `path_additions` | directories put on the child's `PATH`, relative to the folder holding the program: `{bin}`, `{bin}/../lib` |
| `keep_env`, `env` | names copied from the server's environment into the child; fixed variables for the child |
| `version_args`, `version_regex` | how the version is asked for and read from the output |

A domain registers its descriptors in its own `eng_toolkit/domains/<name>/engines.py`: `EXTERNAL_TOOLS = (NGSPICE,)` (import them from `eng_toolkit.external_catalog`, or write one inline). `licenses.register_domain_engines()` adds them to `licenses.EXTERNAL`, like Python distributions to `KNOWN`; declaring the same program twice is fine when the declaration is identical, a different one is a `ValueError`. No shared file is edited. `toolkit_doctor` reads `EXTERNAL`.

## Finding it (`external.locate(tool)`)

1. `tool.env_var` set: exactly that file. It must be an absolute path to an existing file; otherwise the answer is "not found" with the reason, and nothing else is consulted, so a wrong setting is not hidden by another copy.
2. `PATH`, in order, for the exact names. Empty and relative entries (the current directory) are skipped, so a program planted in the working directory is never started.
3. `tool.locations`, in order. `~` and environment variables are expanded; a pattern with an unset variable or a relative pattern is skipped; `*` matches one path component and the highest version (numbers, not text: `10.0` before `9.0`) wins.

On Windows a file found in 2 and 3 must end in `.exe` or `.com` (a `.cmd`/`.bat` is started only through the variable); on POSIX it must have the executable bit. The answer is `Located(path, source, via)` with `source` `env`, `path` or `location` and `via` the variable name, the `PATH` directory or the pattern that matched. Nothing is ever taken from a document or from the arguments of a call.

## Running it (`external.run(tool, args, workdir=..., ...)`)

| Aspect | Behaviour |
|---|---|
| Arguments | a list used as given: no shell, no splitting, no expansion. Every `os.PathLike` argument must lie inside `workdir` (`input.path_not_allowed` otherwise, and nothing starts); a string is passed as it is. A NUL is refused |
| Working directory | `workdir` (the job directory) |
| Environment | built from scratch: `SYSTEMROOT`, `SYSTEMDRIVE`, `WINDIR`, `COMSPEC`, `PATHEXT` and two processor variables on Windows, `LANG`/`LC_*` elsewhere; `PATH` = the existing `path_additions`, the program's own folder, the system directories (the server's `PATH` is **not** passed); `TEMP`/`TMP`/`TMPDIR`, `HOME`, `USERPROFILE`, `APPDATA`, `LOCALAPPDATA` and the XDG directories point into `workdir/tmp` and `workdir/home` (KiCad writes its settings there, not into the user's profile); then `keep_env` and `env`. API keys, tokens, `ENG_TOOLKIT_*` and the rest are dropped |
| Standard input | the null device |
| Time | `timeout_s` (60 s): the program and everything it started are killed (`taskkill /T` on Windows, `killpg` elsewhere): `engine.timeout` |
| Written files | the bytes written below `workdir` since the start (new files plus growth of old ones) are counted every 50 ms; over `max_output_bytes` (50 MB) the program is killed: `engine.resource_limit`. A file written faster than the polling interval is caught when the program ends. On Linux `RLIMIT_FSIZE` also applies |
| Printed text | each of stdout and stderr is capped at 1 MB (`engine.resource_limit`, stream named); the last 3000 characters of each are kept, control characters removed, the job directory replaced by `<job>` and the home directory by `<home>` (either slash, doubled backslashes, any case) |
| Memory | `memory_mb` (2048; `None` for no cap): Windows Job Object with a per-process limit and kill-on-close; Linux `prlimit(RLIMIT_AS)`; macOS none. The record says whether it was applied |
| Result | a `Record`: program path, how it was found, return code, seconds, the two tails, `files` (name relative to `workdir`, bytes, sha256, `created`/`modified`; at most 500), the limits used. `to_dict()` is JSON |
| Failures | `engine.unavailable` (not found; the message has the project page and the variable), `engine.failure` (could not start; or, with `check=True`, a non-zero exit: the message quotes at most 300 characters of stderr, the details hold both tails), `engine.timeout`, `engine.resource_limit`. No retry: a program is started once |

`check=False` returns the exit code instead of raising, for programs whose non-zero code carries meaning (CalculiX prints its version and exits 201).

## Version and the doctor

`external.version(tool, located)` runs `version_args` (10 s, 512 MB, same environment rules) and returns group 1 of `version_regex` searched in stdout and stderr, whatever the exit code; `None` if nothing matches. `external.probe(tool)` is the row `toolkit_doctor` puts in `data.external_tools`: `name`, `found`, `path`, `found_via`, `version`, `status` (`ok`, `not-installed (optional)`, `missing`), declared licence and tier, `env_var`, `project`, and for a program that is absent `reason` and `install`. It never raises. A missing optional program is not a failure; a missing program with `optional=False` is.

## The catalog and what was checked on the real programs

Windows 11, programs installed by the user; all of it by `tests/test_external_real.py`:

| Program | Found at | Verified |
|---|---|---|
| ngspice 41 (`ngspice_con.exe`) | `miniconda3\envs\*\Library\bin` | batch run (`-b`) of 5 V over 1 kOhm + 1 kOhm: `v(mid) = 2.5`; starts with no extra PATH |
| Yosys 0.70 | `tools\oss-cad-suite-root\oss-cad-suite\bin` | `read_verilog`, `synth`, `stat` of `y = a & b`: one `$_AND_`. **Needs `bin` and `..\lib` on PATH** (`path_additions`); with `bin` alone, or none, it exits 0xC0000135 |
| Gmsh 4.15.2 | conda `Library\bin` | `-2` on a unit square, `-format msh2`: 5 nodes |
| CalculiX 2.23 (`ccx.exe`) | conda `Library\bin` | one T3D2 truss under 1000 N: displacement 1000 / (210000 x 100) = 4.7619e-05 in the `.frd`. **Needs `Library\mingw-w64\bin` on PATH** (the conda root, `Library\usr\bin` and `Scripts` do not help); without it 0xC0000135. `ccx -v` prints the version and exits 201 |
| KiCad 10.0.6 (`kicad-cli.exe`) | `%LOCALAPPDATA%\Programs\KiCad\10.0\bin` | `--version`; with the scrubbed environment and `APPDATA` in the job directory it writes two small `.json` settings files there and prints no error. With no `APPDATA` it complains about creating a directory under `C:\WINDOWS` |
| dwg2dxf | not installed | fake only |

Two negative controls (`path_additions=()` for ccx and Yosys) prove the additions are what makes them start.

## Not done, not verified

* **Not a sandbox.** A started program runs with the server's user rights. What the layer does: no shell, fixed arguments, only job-directory paths in `PathLike` arguments, a working directory and temp/home directories inside the job, a scrubbed environment, limits on time, memory, bytes written and bytes printed, and a report of the files that changed in the job directory. It cannot stop a program from reading or writing elsewhere (no AppContainer, no chroot, no low-integrity token), nor see a write outside `workdir`. A program that parses untrusted files in C is a risk the user accepts by installing it; keep it updated. A string argument is not checked: a domain must pass `Path` objects for files and must not build arguments from document text.
* Linux and macOS code paths (`prlimit`, `killpg`, `RLIMIT_FSIZE`, the executable-bit rule, the POSIX locations in the catalog, the `/bin/sh` shim of the fake) were written but not run: the machine is Windows 11. The memory cap on macOS does not exist.
* The memory cap and the Job Object are applied just after the process starts; for a few milliseconds the program runs without them. A grandchild created before that is killed by `taskkill /T` at a timeout but is not inside the Job Object.
* The `.cmd` shim the fake program uses on Windows lets `cmd.exe` expand `%VAR%` in arguments; the tests therefore do not claim "no expansion" for that character. A real `.exe` receives the argument as given.
* The polling of the job directory walks it every 50 ms; a job directory with very many files makes that slower. `files` is cut at 500 entries (`files_truncated`).
* Version regexes and `-v`/`--version` behaviour of ngspice, Yosys, Gmsh, CalculiX and KiCad were checked on one version each; other versions may print differently.
* Locations on Linux and macOS (`/usr/bin`, Homebrew, snap, `/opt/oss-cad-suite`, the KiCad application bundle) are the usual ones and were not run. `locations` entries for conda cover `miniconda3`, `anaconda3` and `miniforge3` under the user profile only.
* No call has used the layer inside a tool yet; the DWG slice is the first to, and will find what a real `dwg2dxf` needs (its arguments, its exit codes) that no fake can show.
