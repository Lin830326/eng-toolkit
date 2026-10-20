"""The 18 specs a small model (Claude Haiku) wrote from the tool description alone must keep working.

tests/eval_data/specs/ holds the files exactly as the model wrote them (one pass, no running, no feedback).
tests/eval_data/tasks.py and tasks_stretch.py hold the part descriptions with closed-form volumes and sizes
(no CAD kernel involved), so every check below is against a hand calculation.  The design record is
docs/geometry-drawing/make-part-eval.md.

If the tool description changes, test_description_is_the_one_the_model_saw fails: re-run the evaluation
(docs/geometry-drawing/make-part-eval.md, "How to repeat it"), replace the specs and tool_as_seen.json, and only
then update this test's data.
"""

import importlib.util
import json
from pathlib import Path

import pytest

from eng_toolkit.domains.geometry.tools import MAKE_PART_DESCRIPTION, make_part, measure

DATA = Path(__file__).parent / "eval_data"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"eval_data_{name}", DATA / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


TASKS = _load("tasks").TASKS + _load("tasks_stretch").TASKS
TOL = _load("tasks")


@pytest.fixture(scope="module")
def ocp():
    return pytest.importorskip("OCP", reason="OCP is not installed; run: uv sync --extra cad")


def test_every_task_has_a_committed_spec_and_a_baseline_script():
    ids = [t["id"] for t in TASKS]
    assert len(ids) == len(set(ids)) == 18
    for task_id in ids:
        assert (DATA / "specs" / f"{task_id}.json").is_file(), task_id
        assert (DATA / "build123d" / f"{task_id}.py").is_file(), task_id


def test_description_is_the_one_the_model_saw():
    seen = json.loads((DATA / "tool_as_seen.json").read_text(encoding="utf8"))
    assert seen["description"] == MAKE_PART_DESCRIPTION, (
        "the tool description changed since the small-model evaluation; repeat the evaluation"
    )


def test_the_round_one_description_is_kept_as_the_record_of_what_that_model_saw():
    seen = json.loads((DATA / "tool_as_seen_round1.json").read_text(encoding="utf8"))
    assert len(seen["description"]) == 3737 and "- pattern (" not in seen["description"]


@pytest.mark.parametrize("task", TASKS, ids=[t["id"] for t in TASKS])
def test_model_written_spec_builds_the_described_part(ocp, task):
    text = (DATA / "specs" / f"{task['id']}.json").read_text(encoding="utf8")
    built = getattr(make_part, "fn", make_part)(spec=text, name=task["id"])
    assert built.ok, built.error
    step = next(a.path for a in built.artifacts if a.path.endswith(".step"))
    # measured again from the written STEP, a second code path, and compared with the closed-form value
    measured = getattr(measure, "fn", measure)(model_path=step)
    assert measured.ok, measured.error
    volume = measured.values["volume"].quantity
    assert abs(volume - task["volume"]) / task["volume"] <= TOL.REL_TOL_VOLUME, (volume, task["volume"])
    size = [measured.values[k].quantity for k in ("size_x", "size_y", "size_z")]
    assert max(abs(a - b) for a, b in zip(size, task["size"])) <= TOL.ABS_TOL_SIZE, (size, task["size"])
    assert measured.data["solids"] == 1


# --------------------------------------------------------------------------- round 2 (docs/geometry-drawing/make-part-eval.md, "Round 2")

ROUND2 = _load("tasks_round2").TASKS
KNOWN_FAILURES = {"R09_s1"}  # the model computed the rotated outlines by hand and rounded them; its volume is 0.88 % off
R2_SPECS = sorted((DATA / "specs_round2").glob("R*_s?.json"))


def _check(task, text, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    built = getattr(make_part, "fn", make_part)(spec=text, name=task["id"])
    assert built.ok, built.error
    step = next(a.path for a in built.artifacts if a.path.endswith(".step"))
    measured = getattr(measure, "fn", measure)(model_path=step)
    assert measured.ok, measured.error
    volume = measured.values["volume"].quantity
    size = [measured.values[k].quantity for k in ("size_x", "size_y", "size_z")]
    return (
        abs(volume - task["volume"]) / task["volume"],
        max(abs(a - b) for a, b in zip(size, task["size"])),
        measured.data["solids"],
    )


def test_round_two_has_ten_tasks_and_three_pinned_samples_each():
    assert [t["id"] for t in ROUND2] == [f"R{i:02d}" for i in range(1, 11)]
    assert [p.stem for p in R2_SPECS] == [f"{t['id']}_s{n}" for t in ROUND2 for n in (1, 2, 3)]
    assert (DATA / "specs_round2" / "scores.json").is_file()


@pytest.mark.parametrize("path", R2_SPECS, ids=[p.stem for p in R2_SPECS])
def test_round_two_model_written_spec_builds_the_described_part(ocp, path, tmp_path, monkeypatch):
    task = next(t for t in ROUND2 if t["id"] == path.stem.split("_")[0])
    rel, worst, solids = _check(task, path.read_text(encoding="utf8"), tmp_path, monkeypatch)
    if path.stem in KNOWN_FAILURES:
        assert rel > TOL.REL_TOL_VOLUME, "a recorded failure now passes: update KNOWN_FAILURES and the record"
    else:
        assert rel <= TOL.REL_TOL_VOLUME and worst <= TOL.ABS_TOL_SIZE and solids == 1, (rel, worst, solids)


@pytest.mark.parametrize("task", ROUND2, ids=[t["id"] for t in ROUND2])
def test_round_two_reference_spec_reaches_the_closed_form(ocp, task, tmp_path, monkeypatch):
    """The ground truth is reachable with the grammar: a spec written by hand with the new ops matches it to 1e-9."""
    text = (DATA / "specs_round2_reference" / f"{task['id']}.json").read_text(encoding="utf8")
    rel, worst, solids = _check(task, text, tmp_path, monkeypatch)
    assert rel <= 1e-9 and worst <= TOL.ABS_TOL_SIZE and solids == 1, (rel, worst, solids)
