"""A cut string says how many characters were cut from the ORIGINAL string, however many times it is cut on its way to the answer."""

import json
from pathlib import Path

import pytest
import raw_dxf as R

pytest.importorskip("ezdxf", reason="ezdxf is not installed; run: uv sync --extra drawing")
from eng_toolkit.domains.drawing import reading  # noqa: E402
from eng_toolkit.domains.drawing.tools import read as read_tool  # noqa: E402

MARK = "... [{} more characters]"


@pytest.mark.parametrize(
    ("length", "limit", "expected"),
    [(10, 500, "x" * 10), (600, 500, "x" * 500 + MARK.format(100)), (5000, 4000, "x" * 4000 + MARK.format(1000))],
)
def test_cut_text_counts_what_is_missing(length, limit, expected):
    assert reading.cut_text("x" * length, limit) == expected


def test_cutting_a_cut_string_again_counts_from_the_original():
    once = reading.cut_text("x" * 1_000_000, 4000)
    assert once.endswith(MARK.format(996_000))
    twice = reading.cut_text(once, 500)
    assert twice == "x" * 500 + MARK.format(999_500)


def test_a_string_already_short_enough_keeps_its_marker():
    once = reading.cut_text("x" * 5000, 4000)
    assert reading.cut_text(once, 4000) == once and reading.cut_text(once, 6000) == once


def call(path):
    fn = getattr(read_tool, "fn", read_tool)
    return fn(path=str(path))


@pytest.mark.parametrize(
    ("length", "in_artifact", "in_answer"),
    [
        (600, None, 100),
        (4100, 100, 3600),
        (5000, 1000, 4500),
        (1_000_000, 996_000, 999_500),
    ],
)
def test_the_markers_in_the_artifact_and_in_the_answer_are_true(tmp_path, length, in_artifact, in_answer):
    path = R.write(tmp_path / "t.dxf", R.document(R.V2000, insunits=4, entities=(R.text("x" * length, (0, 0)),)))
    result = call(path)
    assert result.ok
    answer = result.data["texts"]["items"][0]["text"]
    assert answer.startswith("x" * 500) and answer[500:] == MARK.format(in_answer)
    full = json.loads(Path(next(a.path for a in result.artifacts if a.path.endswith("drawing_read.json"))).read_text(encoding="utf-8"))
    stored = full["texts"][0]["text"]
    if in_artifact is None:
        assert stored == "x" * length
    else:
        assert stored[:4000] == "x" * 4000 and stored[4000:] == MARK.format(in_artifact)
