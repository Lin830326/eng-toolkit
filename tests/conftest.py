import pytest


@pytest.fixture(autouse=True)
def isolated_workspace(tmp_path, monkeypatch):
    """Every test writes its ws/<job-id>/ files under a temporary directory."""
    root = tmp_path / "ws"
    monkeypatch.setenv("ENG_TOOLKIT_WS", str(root))
    return root
