"""The MCP server instructions carry the hard rules to assistants that never open a skill."""

import asyncio
from pathlib import Path

from fastmcp import Client

from eng_toolkit.gateway.server import INSTRUCTIONS, MAX_INSTRUCTIONS_BYTES, gateway

PLUGIN_SKILLS = Path(__file__).resolve().parents[1] / "plugin" / "skills"

# One entry per rule, in the order the instructions state them: every needle must appear in the text.
RULES = (
    ("unit string on every input", ("unit string", "bare numbers")),
    ("ask before computing", ("ASK before computing", "Never invent", "from memory, not a tool result")),
    ("deciding numbers come from a tool", ("comes from a tool call", "my arithmetic")),
    ("load the skills first", ("Skill tool", "`eng-toolkit` skill", "domain skill")),
    ("pictures are PNG, never hand-written", ('format="png"', "exact PNG path", "describe the picture", "Never hand-write SVG, HTML or ASCII")),
    ("read the result fields before 'passed'", ("warnings", "data.assumptions", "not_performed", "at_kicad_limit", "0 or null", "not checked")),
    ("a refusal is a result", ("refusal", "is a result", "never bypass")),
)


def test_instructions_state_each_of_the_seven_rules_in_order():
    positions = []
    for name, needles in RULES:
        for needle in needles:
            assert needle in INSTRUCTIONS, f"{name}: missing {needle!r}"
        positions.append(INSTRUCTIONS.index(needles[0]))
    assert positions == sorted(positions)
    for number in range(1, 8):
        assert f"\n{number}. " in INSTRUCTIONS


def test_instructions_stay_under_the_size_cap_and_are_plain_text():
    size = len(INSTRUCTIONS.encode("utf-8"))
    assert size <= MAX_INSTRUCTIONS_BYTES <= 2500, size
    INSTRUCTIONS.encode("ascii")


def test_instructions_name_every_plugin_domain_skill():
    names = sorted(path.name for path in PLUGIN_SKILLS.iterdir() if path.is_dir() and path.name != "eng-toolkit")
    assert names, "plugin skills not found"
    for name in names:
        assert name in INSTRUCTIONS, f"skill {name} is not named in the server instructions"


def test_the_client_receives_the_instructions_at_initialize():
    async def initialize():
        async with Client(gateway) as client:
            return client.instructions

    assert asyncio.run(initialize()) == INSTRUCTIONS
