"""eng_toolkit.external: finding a user-installed program and running it as a limited separate process.

Everything here uses a fake program (tests/fake_exe.py behind a shim), so it needs nothing outside the repository.
tests/test_external_real.py runs the same layer against the real ngspice, Yosys, Gmsh, CalculiX and KiCad when they are installed.
"""

import hashlib
import json
import os
import sys
import time
from pathlib import Path

import exe_fixtures as F
import pytest

from eng_toolkit import external as X
from eng_toolkit.errors import ToolFailure
from eng_toolkit.licenses import ExternalTool

TOOL = F.TOOL
WINDOWS = sys.platform == "win32"


@pytest.fixture
def job(tmp_path) -> Path:
    path = tmp_path / "job"
    path.mkdir()
    return path


@pytest.fixture
def fake(tmp_path, monkeypatch) -> F.Fake:
    return F.install_fake(tmp_path, monkeypatch)


def make_program(directory: Path, name: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text("x")
    path.chmod(0o755)
    return path


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def failure(excinfo) -> ToolFailure:
    return excinfo.value


# --------------------------------------------------------------------------- locate: the variable


def test_the_variable_names_the_program_and_wins_over_path_and_locations(tmp_path, monkeypatch):
    on_path = make_program(tmp_path / "bin", "fake-tool.exe" if WINDOWS else "fake-tool")
    named = make_program(tmp_path / "other", "whatever")
    tool = ExternalTool(**{**TOOL.__dict__, "locations": (str(tmp_path / "bin"),)})
    monkeypatch.setenv("PATH", str(on_path.parent))
    monkeypatch.setenv(TOOL.env_var, str(named))
    found, why = X.locate(tool)
    assert found == X.Located(str(named), "env", TOOL.env_var) and why == ""


def test_a_wrong_variable_is_not_hidden_by_a_copy_on_path(tmp_path, monkeypatch):
    on_path = make_program(tmp_path / "bin", "fake-tool.exe" if WINDOWS else "fake-tool")
    monkeypatch.setenv("PATH", str(on_path.parent))
    for value, expected in ((str(tmp_path / "missing"), "not a file"), ("relative/tool", "not a full path"), (str(tmp_path), "not a file")):
        monkeypatch.setenv(TOOL.env_var, value)
        found, why = X.locate(TOOL)
        assert found is None and expected in why and TOOL.env_var in why


# --------------------------------------------------------------------------- locate: PATH and the known places


def test_path_is_searched_in_order_by_exact_name_and_reports_the_directory(tmp_path, monkeypatch):
    name = "fake-tool.exe" if WINDOWS else "fake-tool"
    first, second = make_program(tmp_path / "a", name), make_program(tmp_path / "b", name)
    make_program(tmp_path / "c", "fake-tool-other.exe")
    monkeypatch.delenv(TOOL.env_var, raising=False)
    monkeypatch.setenv("PATH", os.pathsep.join([str(tmp_path / "c"), str(first.parent), str(second.parent)]))
    found, _ = X.locate(TOOL)
    assert found == X.Located(str(first), "path", str(first.parent))


def test_the_first_name_of_the_descriptor_wins_within_a_directory(tmp_path, monkeypatch):
    tool = ExternalTool(**{**TOOL.__dict__, "executables": ("second.exe", "first.exe") if WINDOWS else ("second", "first")})
    make_program(tmp_path / "bin", "first.exe" if WINDOWS else "first")
    chosen = make_program(tmp_path / "bin", "second.exe" if WINDOWS else "second")
    monkeypatch.delenv(TOOL.env_var, raising=False)
    monkeypatch.setenv("PATH", str(chosen.parent))
    assert X.locate(tool)[0].path == str(chosen)


def test_relative_and_empty_path_entries_are_skipped_so_the_working_directory_is_never_searched(tmp_path, monkeypatch):
    make_program(tmp_path / "here", "fake-tool.exe")
    make_program(tmp_path / "here", "fake-tool")
    monkeypatch.chdir(tmp_path / "here")
    monkeypatch.delenv(TOOL.env_var, raising=False)
    monkeypatch.setenv("PATH", os.pathsep.join(["", ".", "here", str(tmp_path / "nowhere")]))
    found, why = X.locate(TOOL)
    assert found is None and "fake-tool" in why and TOOL.env_var in why


@pytest.mark.skipif(not WINDOWS, reason="only Windows has program suffixes")
def test_on_windows_a_cmd_or_bat_is_found_only_through_the_variable(tmp_path, monkeypatch):
    for name in ("fake-tool.cmd", "fake-tool.bat", "fake-tool"):
        make_program(tmp_path / "bin", name)
    tool = ExternalTool(**{**TOOL.__dict__, "executables": ("fake-tool.cmd", "fake-tool.bat", "fake-tool")})
    monkeypatch.delenv(TOOL.env_var, raising=False)
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    assert X.locate(tool)[0] is None
    monkeypatch.setenv(TOOL.env_var, str(tmp_path / "bin" / "fake-tool.cmd"))
    assert X.locate(tool)[0].source == "env"


@pytest.mark.skipif(WINDOWS, reason="the executable bit is a POSIX notion")
def test_a_file_on_path_without_the_executable_bit_is_not_a_program(tmp_path, monkeypatch):
    path = make_program(tmp_path / "bin", "fake-tool")
    path.chmod(0o644)
    monkeypatch.delenv(TOOL.env_var, raising=False)
    monkeypatch.setenv("PATH", str(path.parent))
    assert X.locate(TOOL)[0] is None


def test_known_locations_come_after_path_and_the_highest_version_wins(tmp_path, monkeypatch):
    name = "fake-tool.exe" if WINDOWS else "fake-tool"
    for version in ("9.0", "10.0", "2.5"):
        make_program(tmp_path / "apps" / version / "bin", name)
    tool = ExternalTool(**{**TOOL.__dict__, "locations": (str(tmp_path / "apps" / "*" / "bin"),)})
    monkeypatch.delenv(TOOL.env_var, raising=False)
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    found, _ = X.locate(tool)
    assert found.source == "location" and found.path == str(tmp_path / "apps" / "10.0" / "bin" / name)  # 10.0 before 9.0: numbers, not text
    assert found.via == str(tmp_path / "apps" / "*" / "bin")
    on_path = make_program(tmp_path / "pathbin", name)
    monkeypatch.setenv("PATH", str(on_path.parent))
    assert X.locate(tool)[0].source == "path"  # PATH beats the known places


def test_locations_expand_variables_and_skip_unset_or_relative_patterns(tmp_path, monkeypatch):
    name = "fake-tool.exe" if WINDOWS else "fake-tool"
    make_program(tmp_path / "root" / "bin", name)
    monkeypatch.setenv("ENG_TOOLKIT_TEST_ROOT", str(tmp_path / "root"))
    monkeypatch.delenv("ENG_TOOLKIT_TEST_UNSET", raising=False)
    monkeypatch.delenv(TOOL.env_var, raising=False)
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    pattern = "%ENG_TOOLKIT_TEST_ROOT%" if WINDOWS else "$ENG_TOOLKIT_TEST_ROOT"
    unset = "%ENG_TOOLKIT_TEST_UNSET%" if WINDOWS else "$ENG_TOOLKIT_TEST_UNSET"
    sep = "\\" if WINDOWS else "/"
    tool = ExternalTool(**{**TOOL.__dict__, "locations": (f"{unset}{sep}bin", "relative/bin", f"{pattern}{sep}bin")})
    found, _ = X.locate(tool)
    assert found is not None and found.path == str(tmp_path / "root" / "bin" / name)
    monkeypatch.chdir(tmp_path / "root")
    assert X.locate(ExternalTool(**{**TOOL.__dict__, "locations": ("bin",)}))[0] is None


def test_the_reason_says_what_was_looked_at(tmp_path, monkeypatch):
    F.no_tool(monkeypatch, tmp_path)
    found, why = X.locate(TOOL)
    assert found is None and "fake-tool.exe or fake-tool" in why and "PATH" in why and TOOL.env_var in why
    tool = ExternalTool(**{**TOOL.__dict__, "locations": ("/a", "/b")})
    assert "2 places" in X.locate(tool)[1]


# --------------------------------------------------------------------------- unavailable


def test_a_program_that_is_not_found_is_engine_unavailable_with_how_to_get_it(job, tmp_path, monkeypatch):
    F.no_tool(monkeypatch, tmp_path)
    with pytest.raises(ToolFailure) as caught:
        X.run(TOOL, ["--go"], workdir=job)
    e = caught.value
    assert e.family == "engine.unavailable"
    assert "fake-tool" in e.message and TOOL.project in e.message and TOOL.env_var in e.message and "PATH" in e.message and "GPL-3.0" in e.message
    assert not any(bad in e.message.lower() for bad in ("curl", "wget", "pip install", "sudo", "irm ", "iex", "apt ", "brew ", "winget", "choco"))
    assert e.details["tool"] == "fake-tool" and e.details["env_var"] == TOOL.env_var and e.details["project"] == TOOL.project and "reason" in e.details


# --------------------------------------------------------------------------- the child's world


def test_the_environment_is_scrubbed_and_everything_points_into_the_job_directory(job, fake, monkeypatch):
    for name in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "AWS_SECRET_ACCESS_KEY", "GITHUB_TOKEN", "MY_PASSWORD", "ENG_TOOLKIT_WS", "ENG_TOOLKIT_SOMETHING", "PYTHONPATH"):
        monkeypatch.setenv(name, "secret-value")
    X.run(TOOL, ["go"], workdir=job)
    seen = fake.runs()[0]["env"]
    assert "secret-value" not in json.dumps(seen)
    allowed = {
        "PATH", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "PATHEXT", "PROCESSOR_ARCHITECTURE", "NUMBER_OF_PROCESSORS",
        "LANG", "LC_ALL", "LC_CTYPE", "TEMP", "TMP", "TMPDIR", "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "XDG_CONFIG_HOME", "XDG_CACHE_HOME",
        "XDG_DATA_HOME", "PROMPT",  # PROMPT: added by cmd.exe itself, which runs the .cmd shim
    }
    assert {k.upper() for k in seen if not k.startswith("=")} <= allowed, sorted(seen)
    inside = [v for k, v in seen.items() if k.upper() in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "XDG_CONFIG_HOME")]
    assert inside and all(Path(v).resolve().is_relative_to(job.resolve()) for v in inside), inside
    assert Path(seen[next(k for k in seen if k.upper() == "HOME")]).is_dir()
    temps = {v for k, v in seen.items() if k.upper() in ("TEMP", "TMP", "TMPDIR")}
    assert len(temps) == 1 and not Path(temps.pop()).resolve().is_relative_to(job.resolve())  # a short scratch folder elsewhere, see below


def _scratch_of(call) -> Path:
    return Path(next(v for k, v in call["env"].items() if k.upper() in ("TEMP", "TMP", "TMPDIR"))).parent


def test_temporary_files_go_to_a_short_scratch_folder_that_is_removed_afterwards(job, fake):
    X.run(TOOL, ["go"], workdir=job)
    scratch = _scratch_of(fake.runs()[0])
    assert scratch.name.startswith("etk") and len(str(scratch)) < 100
    assert not scratch.exists()  # cleaned up when the program has ended
    assert not (job / "tmp").exists()


def test_a_failing_program_still_leaves_no_scratch_folder(job, fake):
    fake.set(mode="ok", exit=3)
    with pytest.raises(ToolFailure):
        X.run(TOOL, ["go"], workdir=job)
    assert not _scratch_of(fake.runs()[0]).exists()


def test_what_a_program_leaves_in_its_scratch_folder_counts_against_the_output_cap(job, tmp_path):
    scratch = tmp_path / "scratch-count"
    scratch.mkdir()
    (scratch / "big.bin").write_bytes(b"x" * 5000)

    class Handle:
        def fileno(self):
            return os.open(os.devnull, os.O_RDONLY)

    assert X._over(Handle(), Handle(), {}, job, 4000, scratch) == ("output", 5000)
    assert X._over(Handle(), Handle(), {}, job, 4000, None) is None


@pytest.mark.skipif(not WINDOWS, reason="the short alias of a long job directory is a Windows measure")
def test_a_long_job_directory_is_shown_to_the_program_through_a_short_alias(tmp_path, fake):
    job = tmp_path / ("d" * 150) / "job"
    job.mkdir(parents=True)
    (job / "in.txt").write_text("data")
    fake.set(mode="ok", files={"out.txt": "made"}, stdout=f"saw {job}")
    record = X.run(TOOL, [job / "in.txt", f"--dir={job}"], workdir=job)
    call = fake.runs()[0]
    assert len(call["cwd"]) < 80 and str(job) not in call["cwd"]  # the program works in the short alias...
    assert all(str(job) not in a for a in call["argv"])
    assert call["argv"][-2].endswith("in.txt") and call["argv"][-1] == "--dir=" + call["cwd"]
    assert (job / "out.txt").read_text() == "made"  # ...and the files it makes are in the real directory
    assert "out.txt" in [f.name for f in record.files]
    assert str(job) not in record.stdout_tail and "saw <job>" in record.stdout_tail  # the alias reads <job> as well
    assert (job / "in.txt").read_text() == "data"  # removing the alias left the directory alone


def test_a_relative_job_directory_is_made_absolute_before_the_program_starts(tmp_path, fake, monkeypatch):
    (tmp_path / "rel" / "job").mkdir(parents=True)
    monkeypatch.chdir(tmp_path / "rel")
    X.run(TOOL, ["go"], workdir=Path("job"))
    assert Path(fake.runs()[0]["cwd"]).resolve() == (tmp_path / "rel" / "job").resolve()


def test_path_holds_the_additions_the_programs_own_folder_and_the_system_directories_only(job, fake, tmp_path, monkeypatch):
    extra = tmp_path / "fake" / "lib"
    extra.mkdir()
    monkeypatch.setenv("PATH", os.pathsep.join([str(tmp_path / "elsewhere"), os.environ["PATH"]]))
    tool = ExternalTool(**{**TOOL.__dict__, "path_additions": ("{bin}", "{bin}/../fake/lib", "{bin}/../does-not-exist")})
    X.run(tool, ["go"], workdir=job)
    entries = fake.runs()[0]["env"]["PATH"].split(os.pathsep)
    assert entries[0] == str(fake.shim.parent) and entries[1] == os.path.normpath(str(extra))  # {bin} is the program's folder; only existing additions
    assert not any("elsewhere" in e for e in entries)
    assert len(entries) == len(set(entries)) and entries[-2:] == X._system_directories()


def test_keep_env_and_fixed_env_reach_the_child_and_nothing_else_does(job, fake, monkeypatch):
    monkeypatch.setenv("ENG_TOOLKIT_KEPT", "kept-value")
    monkeypatch.setenv("ENG_TOOLKIT_DROPPED", "dropped-value")
    tool = ExternalTool(**{**TOOL.__dict__, "keep_env": ("ENG_TOOLKIT_KEPT", "ENG_TOOLKIT_ABSENT"), "env": (("FAKE_FIXED", "1"),)})
    X.run(tool, ["go"], workdir=job)
    seen = fake.runs()[0]["env"]
    assert seen["ENG_TOOLKIT_KEPT"] == "kept-value" and seen["FAKE_FIXED"] == "1"
    assert "ENG_TOOLKIT_DROPPED" not in seen and "ENG_TOOLKIT_ABSENT" not in seen


# --------------------------------------------------------------------------- running


def test_it_runs_in_the_job_directory_with_no_stdin_and_the_exact_argument_list(job, fake):
    args = ["--flag", "two words", "&& echo injected", "$HOME", "'quoted'", '"dq"', ""]
    record = X.run(TOOL, args, workdir=job)
    (call,) = fake.runs()
    assert call["argv"] == args  # no shell, no splitting, no expansion
    assert Path(call["cwd"]).resolve() == job.resolve() and call["stdin"] == ""
    assert record.returncode == 0 and record.program == str(fake.shim) and record.source == f"env: {TOOL.env_var}"


def test_the_record_has_the_exit_code_the_streams_and_the_files_it_created(job, fake):
    fake.set(stdout="hello out", stderr="hello err", files={"result.txt": "42\n", "sub/dir/more.bin": "abc"})
    (job / "input.dat").write_text("pre-existing")
    record = X.run(TOOL, ["go"], workdir=job)
    assert record.returncode == 0 and record.stdout_tail == "hello out" and record.stderr_tail == "hello err"
    by_name = {f.name: f for f in record.files}
    assert by_name["result.txt"] == X.FileRecord("result.txt", (job / "result.txt").stat().st_size, sha(job / "result.txt"), "created")
    assert by_name["sub/dir/more.bin"].bytes == 3 and by_name["sub/dir/more.bin"].sha256 == hashlib.sha256(b"abc").hexdigest()
    assert "input.dat" not in by_name  # untouched files are not reported
    assert record.seconds > 0 and record.memory_cap_applied in (True, False)
    assert record.limits["timeout_s"] == X.DEFAULT_TIMEOUT_S and record.limits["max_output_bytes"] == X.DEFAULT_OUTPUT_BYTES
    as_dict = record.to_dict()
    assert json.loads(json.dumps(as_dict))["files"][0]["status"] == "created" and as_dict["limits"]["memory_cap_applied"] == record.memory_cap_applied


def test_a_file_the_program_changed_is_reported_as_modified(job, fake):
    target = job / "existing.txt"
    target.write_text("line\n")
    fake.set(mode="modify", file=str(target))
    record = X.run(TOOL, ["go"], workdir=job)
    (changed,) = [f for f in record.files if f.name == "existing.txt"]
    assert changed.status == "modified" and changed.sha256 == sha(target) and changed.bytes == target.stat().st_size > len("line\n")


def test_a_non_zero_exit_is_engine_failure_with_the_end_of_stderr_and_the_record(job, fake):
    fake.set(exit=3, stderr="ERROR: bad input on line 7")
    with pytest.raises(ToolFailure) as caught:
        X.run(TOOL, ["go"], workdir=job)
    e = caught.value
    assert e.family == "engine.failure" and "exited with code 3" in e.message and "bad input on line 7" in e.message
    assert e.details["returncode"] == 3 and e.details["stderr_tail"] == "ERROR: bad input on line 7"
    assert len(fake.runs()) == 1  # no retry


def test_check_false_reports_the_exit_code_instead_of_raising(job, fake):
    fake.set(exit=201, stdout="This is Version 2.23")
    record = X.run(TOOL, ["go"], workdir=job, check=False)
    assert record.returncode == 201 and record.stdout_tail == "This is Version 2.23"


def test_a_failure_with_no_stderr_quotes_stdout_and_says_so_when_there_is_nothing(job, fake):
    fake.set(exit=1, stdout="only stdout")
    with pytest.raises(ToolFailure) as caught:
        X.run(TOOL, ["go"], workdir=job)
    assert "only stdout" in caught.value.message
    fake.set(stdout="")
    with pytest.raises(ToolFailure) as caught:
        X.run(TOOL, ["go"], workdir=job)
    assert "printed nothing" in caught.value.message


def test_streams_are_cut_and_scrubbed_of_the_job_directory_and_the_home_directory(job, fake, tmp_path, monkeypatch):
    home = tmp_path / "someone"
    (home / "docs").mkdir(parents=True)
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HOME", str(home))
    leaked = f"opened {job}{os.sep}input{os.sep}a.dat and {str(job).replace(os.sep, '/')}/b.dat and {home}{os.sep}docs{os.sep}secret.txt and {str(job).upper()}"
    fake.set(exit=2, stderr="x" * 6000 + " " + leaked, stdout="y" * 5000 + " " + leaked)
    with pytest.raises(ToolFailure) as caught:
        X.run(TOOL, ["go"], workdir=job)
    e = caught.value
    text = json.dumps({"m": e.message, "d": e.details})
    for forbidden in (str(job), str(job).replace("\\", "\\\\"), str(job).replace(os.sep, "/"), str(job).upper(), str(home), str(home).replace("\\", "\\\\")):
        assert forbidden not in text, forbidden
    assert "<job>" in e.details["stderr_tail"] and "<home>" in e.details["stderr_tail"]
    assert len(e.details["stderr_tail"]) <= X.TAIL_CHARS + 3 and len(e.details["stdout_tail"]) <= X.TAIL_CHARS + 3
    assert len(e.message) < X.MESSAGE_CHARS + 120
    assert e.details["stderr_tail"].startswith("...")


def test_control_characters_in_a_stream_are_removed(job, fake):
    fake.set(stdout="a\x00b\x07c\x1bd  e\t\tf")
    assert X.run(TOOL, ["go"], workdir=job).stdout_tail == "a b c d e f"


def test_a_program_that_cannot_be_started_is_engine_failure(job, tmp_path, monkeypatch):
    broken = tmp_path / ("broken.exe" if WINDOWS else "broken")
    broken.write_bytes(b"this is not a program")
    broken.chmod(0o755)
    monkeypatch.setenv(TOOL.env_var, str(broken))
    with pytest.raises(ToolFailure) as caught:
        X.run(TOOL, ["go"], workdir=job)
    assert caught.value.family == "engine.failure" and "could not be started" in caught.value.message


# --------------------------------------------------------------------------- confinement and argument checks


def test_a_path_argument_must_lie_inside_the_job_directory(job, fake, tmp_path):
    inside = job / "input" / "a.dat"
    X.run(TOOL, [inside, "plain string outside C:/elsewhere/x"], workdir=job)
    assert fake.runs()[0]["argv"] == [str(inside), "plain string outside C:/elsewhere/x"]  # a string is the caller's business
    for outside in (tmp_path / "other.dat", job / ".." / "other.dat", job.parent, Path(job).parent / (job.name + "-sibling") / "x"):
        with pytest.raises(ToolFailure) as caught:
            X.run(TOOL, [outside], workdir=job)
        assert caught.value.family == "input.path_not_allowed" and "job directory" in caught.value.message
    assert len(fake.runs()) == 1  # nothing was started for the refused ones


def test_a_nul_in_an_argument_is_refused(job, fake):
    with pytest.raises(ToolFailure) as caught:
        X.run(TOOL, ["a\x00b"], workdir=job)
    assert caught.value.family == "input.invalid" and fake.runs() == []


# --------------------------------------------------------------------------- limits


def test_a_program_that_does_not_end_is_stopped_with_everything_it_started(job, fake, tmp_path):
    marker = tmp_path / "child.pid"
    fake.set(mode="spawn", child_pid=str(marker))
    started = time.monotonic()
    with pytest.raises(ToolFailure) as caught:
        X.run(TOOL, ["go"], workdir=job, timeout_s=2)
    e = caught.value
    assert e.family == "engine.timeout" and "2 s" in e.message and e.details["timeout_s"] == 2 and time.monotonic() - started < 20
    deadline = time.monotonic() + 5
    while not marker.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    child = int(marker.read_text())
    assert F.wait_dead(child) and F.wait_dead(fake.runs()[0]["pid"]), "the program or its child survived the timeout"


def test_output_below_the_job_directory_is_capped_while_it_is_written(job, fake):
    fake.set(mode="write", file=str(job / "big" / "out.bin"), total=60_000_000, chunk=100_000)
    (job / "big").mkdir()
    with pytest.raises(ToolFailure) as caught:
        X.run(TOOL, ["go"], workdir=job, max_output_bytes=1_000_000)
    e = caught.value
    assert e.family == "engine.resource_limit" and e.details["stream"] == "output" and e.details["limit_bytes"] == 1_000_000
    assert (job / "big" / "out.bin").stat().st_size < 20_000_000  # stopped, not left to write 60 MB
    assert F.wait_dead(fake.runs()[0]["pid"])


def test_many_small_files_count_together(job, fake):
    fake.set(mode="write", file=str(job / "a.bin"), total=700_000, chunk=100_000)
    X.run(TOOL, ["go"], workdir=job, max_output_bytes=1_000_000)  # 700 kB alone is fine
    (job / "b.bin").write_bytes(b"x" * 600_000)  # already there before the run: not counted as written
    X.run(TOOL, ["go"], workdir=job, max_output_bytes=1_000_000)
    fake.set(file=str(job / "c.bin"))
    X.run(TOOL, ["go"], workdir=job, max_output_bytes=1_000_000)


def test_a_file_written_faster_than_the_polling_is_still_caught_when_the_program_ends(job, fake):
    fake.set(mode="write", file=str(job / "fast.bin"), total=3_000_000, chunk=3_000_000, delay=0)
    with pytest.raises(ToolFailure) as caught:
        X.run(TOOL, ["go"], workdir=job, max_output_bytes=1_000_000)
    assert caught.value.family == "engine.resource_limit" and caught.value.details["bytes"] >= 3_000_000


def test_a_program_that_prints_without_end_is_stopped(job, fake):
    fake.set(mode="flood")
    with pytest.raises(ToolFailure) as caught:
        X.run(TOOL, ["go"], workdir=job)
    assert caught.value.family == "engine.resource_limit" and caught.value.details["stream"] == "stdout"
    assert F.wait_dead(fake.runs()[0]["pid"])


@pytest.mark.skipif(not WINDOWS and not sys.platform.startswith("linux"), reason="no memory cap on this platform")
def test_the_memory_cap_is_applied_and_a_program_that_exceeds_it_dies(job, fake):
    fake.set(mode="alloc", alloc_mb=700)
    with pytest.raises(ToolFailure) as caught:
        X.run(TOOL, ["go"], workdir=job, memory_mb=250)
    assert caught.value.family == "engine.failure" and "allocated" not in caught.value.details["stdout_tail"]
    fake.set(alloc_mb=20)
    record = X.run(TOOL, ["go"], workdir=job, memory_mb=500)
    assert record.memory_cap_applied is True and "allocated" in record.stdout_tail
    assert X.run(TOOL, ["go"], workdir=job, memory_mb=None).memory_cap_applied is False  # None: no cap, and the record says so


# --------------------------------------------------------------------------- version and the doctor row


def test_the_version_is_the_regex_match_whatever_the_exit_code(job, fake):
    located, _ = X.locate(TOOL)
    assert X.version(TOOL, located) == "1.2.3"
    fake.set(version_text="tool is fake-tool 4.5.6beta", version_exit=201)  # CalculiX exits 201 for -v
    assert X.version(TOOL, located) == "4.5.6"
    fake.set(version_text="nothing like a version")
    assert X.version(TOOL, located) is None
    fake.set(version_text="")
    assert X.version(TOOL, located) is None
    assert X.version(TOOL, X.Located(str(job / "no-such-program"), "env", "x")) is None  # cannot start: no exception


def test_the_version_regex_without_a_group_gives_the_whole_match(fake):
    tool = ExternalTool(**{**TOOL.__dict__, "version_regex": r"fake-tool \d+\.\d+"})
    assert X.version(tool, X.locate(tool)[0]) == "fake-tool 1.2"


def test_probe_reports_found_and_not_found_and_never_raises(tmp_path, monkeypatch):
    F.no_tool(monkeypatch, tmp_path)
    row = X.probe(TOOL)
    assert row["found"] is False and row["path"] is None and row["version"] is None and row["status"] == "not-installed (optional)"
    assert TOOL.env_var in row["reason"] and TOOL.project in row["install"] and row["declared_tier"] == "gpl-isolated" and row["optional"] is True
    fake = F.install_fake(tmp_path, monkeypatch)
    row = X.probe(TOOL)
    assert row["found"] is True and row["path"] == str(fake.shim) and row["version"] == "1.2.3" and row["found_via"] == f"env: {TOOL.env_var}"
    assert row["status"] == "ok" and row["declared_license"] == "GPL-3.0-or-later" and "reason" not in row
    required = ExternalTool(**{**TOOL.__dict__, "optional": False})
    F.no_tool(monkeypatch, tmp_path)
    assert X.probe(required)["status"] == "missing"
    monkeypatch.setenv(TOOL.env_var, str(tmp_path / "gone"))
    assert X.probe(TOOL)["found"] is False  # a wrong variable is a clean "not found", not an exception
    json.dumps(X.probe(TOOL))
