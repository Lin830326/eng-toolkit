"""The plugin's skills and commands must load: a frontmatter that is not valid YAML makes a skill silently disappear.

``eng-plot-results`` shipped with ``artifacts: filter response`` in its description; the colon made the YAML invalid and no
assistant ever saw the skill. The check runs without PyYAML too: an unquoted description may not contain ': '.
"""

import re
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1] / "plugin"
SKILLS = sorted(PLUGIN.glob("skills/*/SKILL.md"))
COMMANDS = sorted(PLUGIN.glob("commands/*.md"))


def _frontmatter(path):
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    match = re.match(r"---\n(.*?)\n---\n", text, re.S)
    assert match, f"{path.name}: no frontmatter"
    return match.group(1)


def _fields(front):
    fields = {}
    for line in front.split("\n"):
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip()
    return fields


@pytest.mark.parametrize("path", SKILLS + COMMANDS, ids=lambda p: f"{p.parent.name}/{p.name}")
def test_frontmatter_is_valid_yaml(path):
    front = _frontmatter(path)
    yaml = pytest.importorskip("yaml")
    loaded = yaml.safe_load(front)
    assert isinstance(loaded, dict), front[:100]
    assert str(loaded.get("description", "")).strip(), f"{path} has no description"


@pytest.mark.parametrize("path", SKILLS, ids=lambda p: p.parent.name)
def test_skill_name_matches_its_folder_and_description_has_no_bare_colon(path):
    fields = _fields(_frontmatter(path))
    assert fields["name"] == path.parent.name
    description = fields["description"]
    quoted = description[:1] in "\"'"
    assert quoted or ": " not in description, "an unquoted description may not contain ': ' (invalid YAML)"
    assert 80 <= len(description) <= 700, len(description)


def test_every_skill_directory_has_a_skill_file_and_a_command():
    folders = {p.name for p in (PLUGIN / "skills").iterdir() if p.is_dir()}
    assert folders == {p.parent.name for p in SKILLS}
