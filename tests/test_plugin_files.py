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
# One entry per (skill, phrases) pair, not a dict: a repeated skill name in a dict literal silently dropped the earlier phrases.
GUIDANCE = [
    ("eng-toolkit", [
        "my arithmetic", "from memory, not a tool result", "Ask first (hard rule)", "Never invent a pump curve",
        "hand method, not tool-checked", "data.measurements", "limits[].source", "Traditional vs Simplified",
        "routing list",
    ]),
    ("eng-motor-drive-sizing", [
        "MAXIMUM speed", "rated_speed_margin", "dwell", "J/r^2", "external force only", "rad/s x 9.549",
        "not peak power vs rated power", "load_to_rotor_inertia_ratio", "minimum_output_capacitance",
    ]),
    ("eng-structural-check", [
        "safety factor against Euler", "half a panel", "both Fx and Fy", "principal_I2", "no catalogue", "roller_x",
        "c,spring,", "not `maximum_nodal_displacement`", "data.modes", "ask before the first calculation",
        "values.max_member_deflection", "frequency_mode_<k>",
    ]),
    ("eng-fatigue-check", [
        "data.header_skipped", "data.range_histogram", "Preloaded bolt", "do not apply Kf again", "Fa*d/2",
        "outside the toolkit", "no cut-off", "wrong scale",
    ]),
    ("eng-dfm-review", ["near_tangent_deg", "RADIUS", "bends: 0", "`caller`", 'color_by="highlight"', "sheet.joint"]),
    ("eng-optimize", ["spreading", "outside the toolkit", "n - 1 gaps"]),
    ("eng-3d-view", ["ONE colour", "HALF the previous", "singularity", "gravity along -z"]),
    ("eng-plot-results", ["kN*m", "operating point", "t10", "data.png_path"]),
    ("eng-signal-analysis", ["data.header_skipped", "column_used", 'unit="g"']),
    ("eng-pcb-review", ["at_kicad_limit", "library_warnings", "design_warnings"]),
    ("eng-circuit-sim", ["bandpass_3db", "synchronous=true", "low_side_resistance", "dead_time"]),
    ("eng-flow-network", ["Q = 0 shut-off"]),
    ("eng-machine-design", ["joint_efficiency", "corrosion_allowance"]),
    ("eng-digital-synthesis", ["200-character"]),
    ("eng-drawing", ["geometry_view"]),
    ("eng-thermal-check", ["jumps by", "t = 0+", "silicone pad", "junction limit"]),
    ("eng-dfm-review", ["spindle axis", "unrecognised_bend_faces", "rule of thumb"]),
    ("eng-pcb-review", ["WRITTEN rules", "inclusive"]),
    ("eng-machine-design", ["z mn / cos(beta)", "fixed mean", "not even provisional"]),
    ("eng-motor-drive-sizing", ["strokes_per_cycle", "ONE stroke", "never call peak power a hard limit"]),
    ("eng-signal-analysis", ["matches no 1x", "front end"]),
    ("eng-optimize", ["U = k u", "k = 2"]),
    ("eng-circuit-sim", ["unwrapped", "assumed"]),
    ("eng-tolerancing", ["never assume a temperature"]),
    ("eng-toolkit", ["server instructions", 'format="both"']),
    ("eng-plot-results", ["unwrap_phase", "SI-prefix tick labels", "inline_image=false"]),
    ("eng-thermal-check", ["natural_channel", "no two-lump", "temperature_vs_time.csv"]),
    ("eng-motor-drive-sizing", ["strokes_per_cycle=2", "data.cycle"]),
    ("eng-flow-network", ["curve_points", "data.operating_point", "system_curve.csv"]),
    ("eng-pcb-review", ["gbrjob", "per_layer=true"]),
    ("eng-circuit-sim", ["phase_unwrapped_deg"]),
    ("eng-dfm-review", ["sheet.bend_tolerance", "sheet.bend_unrecognised", "tool_axis defaulted"]),
    ("eng-3d-view", ["finest size", "focus", "data.displacement_note"]),
    ("eng-optimize", ["data.parameter_scales", "order of magnitude"]),
    ("eng-fatigue-check", ["data.dominant_cycle", "data.spikes", "history_range"]),
    ("eng-structural-check", ["data.applied_total_fx/fy", "loaded_nodes"]),
    ("eng-machine-design", ["|mean| + alternating", "spur only"]),
]


@pytest.mark.parametrize("skill, phrases", GUIDANCE, ids=[f"{skill}-{n}" for n, (skill, _) in enumerate(GUIDANCE)])
def test_skill_carries_the_acceptance_round_guidance(skill, phrases):
    text = (PLUGIN / "skills" / skill / "SKILL.md").read_text(encoding="utf-8")
    missing = [phrase for phrase in phrases if phrase not in text]
    assert not missing, f"{skill} lost: {missing}"


# Statements the tool fixes made false: a skill that repeats one sends the assistant round a problem that no longer exists.
STALE = (
    "header-less", "SVG only", "write 0.01", "never claim it was processed", "full column name", "non-synchronous only",
    "conservative screen", "material_angle_deg",
    # round 3, second pass: workarounds and hedges the tool fixes made unnecessary
    "pass `format=\"png\"`", "if its description lists", "lists a natural channel model", "No tool returns a curve table",
    "bending_stress = 0", "Use an unwrapped phase column if the table has one", "tool has no channel correlation",
    "No tool computes this", "with the pump removed", "unwrap by hand",
)


@pytest.mark.parametrize("path", SKILLS + COMMANDS, ids=lambda p: f"{p.parent.name}/{p.name}")
def test_plugin_text_does_not_describe_behaviour_the_tools_no_longer_have(path):
    text = path.read_text(encoding="utf-8")
    found = [phrase for phrase in STALE if phrase in text]
    assert not found, f"{path.name} still says {found}"


@pytest.mark.parametrize("path", SKILLS, ids=lambda p: p.parent.name)
def test_a_skill_that_hands_over_a_chart_hands_over_the_png_path(path):
    # The plot tools write the PNG by default (format="both") and show it in the reply; the path is still given in words.
    text = path.read_text(encoding="utf-8")
    if any(tool in text for tool in ("plot_xy", "plot_panels", "plot_bar")):
        assert "png_path" in text or 'format="png"' in text, f"{path.parent.name} names a plot tool but never hands over the PNG path"


def test_prompt_hook_no_longer_forbids_all_arithmetic_and_is_valid_json():
    import json

    hook = json.loads((PLUGIN / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    command = hook["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"]
    assert "hand arithmetic" not in command
    for phrase in ("my arithmetic", "never present a hand number as a tool result", "from memory, not a tool result", "ask first"):
        assert phrase in command
    assert command.count("'") == 2, "the echo text must not contain a quote that ends the shell string early"


# Distinctive numbers, parts and names of the user-acceptance scenarios (uat/scenarios) and of the tests that grade them. A skill
# that quotes one lets a pass mean "the assistant read the skill": worked examples use other parts and numbers.
SCENARIO_MARKERS = (
    "IRF540N", "RE 40", "1480", "IPE 240", "IPE240", "StickHub", "6205", "Pico", "RaspberryPi", "cnt8", "add16", "2N2222",
    "ECMA", "P265GH", "6210", "FAT 63", "L80x80", "B57861", "Grundfos", "851.8", "5794.6", "354.6", "7.63", "6.24", "3700",
    "986.67", "24.67", "49.33", "0.0560", "1.273", "0.03013", "3.155", "317 rpm", "0.299", "142 gcm", "37.1 kN", "4740",
    "414.1", "2183", "1553", "94 parts", "47 nets", "43×", "2.15 kohm", "47k", "NTC", "8016", "HTD", "63.66", "manway",
    "DN200", "1470", "294 mm", "30 kW", "S275", "70 kPa", "350.1", "150.6", "mezzanine", "crane", "45 degC", "90 degC",
)


def test_markers_are_all_distinctive_enough_to_be_a_real_list():
    assert len(SCENARIO_MARKERS) >= 50 and len(set(SCENARIO_MARKERS)) == len(SCENARIO_MARKERS)


@pytest.mark.parametrize(
    "path", SKILLS + COMMANDS + [PLUGIN / "README.md", PLUGIN / "hooks" / "hooks.json"], ids=lambda p: f"{p.parent.name}/{p.name}"
)
def test_plugin_text_quotes_no_number_or_part_of_an_acceptance_scenario(path):
    text = path.read_text(encoding="utf-8")
    found = [marker for marker in SCENARIO_MARKERS if marker in text]
    assert not found, f"{path.parent.name}/{path.name} quotes scenario-specific {found}: use a different example"


@pytest.mark.parametrize("path", SKILLS, ids=lambda p: p.parent.name)
def test_skill_description_starts_with_a_trigger_phrase(path):
    description = _fields(_frontmatter(path))["description"]
    if path.parent.name == "eng-toolkit":
        assert "Load before any eng-toolkit tool" in description
    else:
        assert description.startswith("Use FIRST for"), description[:60]


@pytest.mark.parametrize("path", [c for c in COMMANDS if c.stem != "eng-toolkit-setup"], ids=lambda p: p.stem)
def test_command_tells_the_assistant_to_load_its_skill_first(path):
    text = path.read_text(encoding="utf-8")
    assert f"First load the `{path.stem}` skill" in text
