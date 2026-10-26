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
    assert 80 <= len(description) <= 300, len(description)


def test_every_skill_directory_has_a_skill_file_and_a_command():
    folders = {p.name for p in (PLUGIN / "skills").iterdir() if p.is_dir()}
    assert folders == {p.parent.name for p in SKILLS}


@pytest.mark.parametrize("path", SKILLS, ids=lambda p: p.parent.name)
def test_skill_stays_within_seven_kib(path):
    size = len(path.read_bytes().replace(bytes([13, 10]), bytes([10])))
    assert size <= 7 * 1024, f"{path.parent.name} is {size} bytes"


# Guidance the user-acceptance round showed to be missing; each phrase is the rule, not decoration.
GUIDANCE = {
    "eng-toolkit": [
        "my arithmetic", "from memory, not a tool result", "Ask first (hard rule)", "Never invent a pump curve",
        "hand method, not tool-checked", "data.measurements", "limits[].source", "Traditional vs Simplified",
        "routing list",
    ],
    "eng-motor-drive-sizing": [
        "MAXIMUM speed", "rated_speed_margin", "dwell", "J/r^2", "external force only", "rad/s x 9.549",
        "not peak power vs rated power", "load_to_rotor_inertia_ratio", "minimum_output_capacitance",
    ],
    "eng-structural-check": [
        "both Fx and Fy", "principal_I2", "no catalogue", "roller_x", "c,spring,", "not `maximum_nodal_displacement`",
        "data.modes", "ask before the first calculation", "values.max_member_deflection", "frequency_mode_<k>",
    ],
    "eng-fatigue-check": [
        "data.header_skipped", "data.range_histogram", "Preloaded bolt", "do not apply Kf again",
        "Fa*d/2", "outside the toolkit",
    ],
    "eng-dfm-review": ["near_tangent_deg", "RADIUS", "bends: 0", "`caller`", 'color_by="highlight"', "sheet.joint"],
    "eng-optimize": ["spreading", "outside the toolkit", "n - 1 gaps"],
    "eng-3d-view": ["ONE colour", "HALF the previous", "singularity", "gravity along -z"],
    "eng-plot-results": ["kN*m", "operating point", "t10", "data.png_path"],
    # behaviour added by the tool fixes of the same round
    "eng-signal-analysis": ["data.header_skipped", "column_used", 'unit="g"'],
    "eng-pcb-review": ["at_kicad_limit", "library_warnings", "design_warnings"],
    "eng-circuit-sim": ["bandpass_3db", "synchronous=true", "low_side_resistance", "dead_time"],
    "eng-flow-network": ["Q = 0 shut-off"],
    "eng-machine-design": ["joint_efficiency", "corrosion_allowance"],
    "eng-digital-synthesis": ["200-character"],
    "eng-drawing": ["geometry_view"],
}


@pytest.mark.parametrize("skill", sorted(GUIDANCE))
def test_skill_carries_the_acceptance_round_guidance(skill):
    text = (PLUGIN / "skills" / skill / "SKILL.md").read_text(encoding="utf-8")
    missing = [phrase for phrase in GUIDANCE[skill] if phrase not in text]
    assert not missing, f"{skill} lost: {missing}"


# Statements the tool fixes made false: a skill that repeats one sends the assistant round a problem that no longer exists.
STALE = (
    "header-less", "SVG only", "write 0.01", "never claim it was processed", "full column name", "non-synchronous only",
    "conservative screen", "material_angle_deg",
)


@pytest.mark.parametrize("path", SKILLS + COMMANDS, ids=lambda p: f"{p.parent.name}/{p.name}")
def test_plugin_text_does_not_describe_behaviour_the_tools_no_longer_have(path):
    text = path.read_text(encoding="utf-8")
    found = [phrase for phrase in STALE if phrase in text]
    assert not found, f"{path.name} still says {found}"


@pytest.mark.parametrize("path", SKILLS, ids=lambda p: p.parent.name)
def test_a_skill_that_hands_over_a_chart_asks_for_a_png(path):
    text = path.read_text(encoding="utf-8")
    if any(tool in text for tool in ("plot_xy", "plot_panels", "plot_bar")):
        assert 'format="png"' in text or 'format "png"' in text, f"{path.parent.name} names a plot tool but never asks for a PNG"


def test_prompt_hook_no_longer_forbids_all_arithmetic_and_is_valid_json():
    import json

    hook = json.loads((PLUGIN / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    command = hook["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"]
    assert "hand arithmetic" not in command
    for phrase in ("my arithmetic", "never present a hand number as a tool result", "from memory, not a tool result", "ask first"):
        assert phrase in command
    assert command.count("'") == 2, "the echo text must not contain a quote that ends the shell string early"
