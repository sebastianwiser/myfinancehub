from pathlib import Path

import pytest

from mainhub.settings import load_env_file, set_var


def test_load_env_file_skips_comments_and_empty_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for var in ("A_KEY", "B_KEY", "C_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("C_KEY", "real")
    f = tmp_path / ".env"
    f.write_text("# c\nA_KEY='x'\nB_KEY=\nC_KEY=fromfile\n")
    load_env_file(f)
    import os

    assert os.environ["A_KEY"] == "x"
    assert "B_KEY" not in os.environ
    assert os.environ["C_KEY"] == "real"
    for var in ("A_KEY", "B_KEY"):
        monkeypatch.delenv(var, raising=False)


def test_set_var_replaces_and_is_private(tmp_path: Path) -> None:
    f = tmp_path / ".env"
    f.write_text("A=1\nB=old\n")
    set_var(f, "B", "new")
    set_var(f, "C", "3")
    assert f.read_text() == "A=1\nB=new\nC=3\n"
    assert f.stat().st_mode & 0o777 == 0o600
