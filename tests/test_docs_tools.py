"""The READMEs and the tool reference name every domain and every tool the gateway serves.

The README once said "four domains" while the gateway served twenty. These tests read the discovered registry, so a
domain or a tool that is added without its documentation fails here.
"""

from pathlib import Path

import pytest

from eng_toolkit.domains import all_domains

ROOT = Path(__file__).resolve().parents[1]
READMES = ("README.md", "README.zh-TW.md")
DOMAINS = all_domains()


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


@pytest.mark.parametrize("domain", DOMAINS, ids=lambda d: d.name)
def test_the_tool_reference_of_a_domain_has_a_section_for_every_tool(domain):
    text = read(f"docs/tools/{domain.name}.md")
    for tool in domain.tools:
        assert f"{domain.name}_{tool}" in text, f"docs/tools/{domain.name}.md does not name {domain.name}_{tool}"


@pytest.mark.parametrize("readme", READMES)
def test_each_readme_has_one_row_per_domain_with_all_of_its_tools(readme):
    rows = {line.split("|")[1].strip(): line for line in read(readme).splitlines() if line.startswith("| [`")}
    assert list(rows) == [f"[`{d.name}`](docs/tools/{d.name}.md)" for d in DOMAINS], "rows follow toolkit_list_tools order"
    for domain in DOMAINS:
        row = rows[f"[`{domain.name}`](docs/tools/{domain.name}.md)"]
        named = {cell.strip("`") for cell in row.split("|")[3].split()}
        assert named == {f"{domain.name}_{tool}" for tool in domain.tools}, (readme, domain.name)


def test_the_tool_reference_has_no_file_without_a_domain():
    files = {p.stem for p in (ROOT / "docs" / "tools").glob("*.md")}
    assert files == {d.name for d in DOMAINS} | {"toolkit"}


@pytest.mark.parametrize("readme", READMES)
def test_the_links_of_a_readme_point_at_files_that_exist(readme):
    import re

    for target in re.findall(r"\]\(([^)#]+)(?:#[^)]*)?\)", read(readme)):
        if "://" not in target:
            assert (ROOT / target).exists(), f"{readme} links to {target}, which does not exist"
