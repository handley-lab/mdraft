import pytest

import mddraft._core


@pytest.fixture(autouse=True)
def no_host_footers(monkeypatch, tmp_path):
    """Isolate tests from the host's /etc/mddraft/footers provisioning."""
    monkeypatch.setattr(mddraft._core, "FOOTERS_DIR", tmp_path / "no-footers")
