import json
from pathlib import Path

import httpx
import pytest

from mainhub.connectors.plaid import HttpPlaidClient
from mainhub.settings import append_to_list_var, load_env_file


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


def test_append_to_list_var_is_idempotent_and_private(tmp_path: Path) -> None:
    f = tmp_path / ".env"
    f.write_text("OTHER=1\nTOKENS=a\n")
    append_to_list_var(f, "TOKENS", "b")
    append_to_list_var(f, "TOKENS", "b")
    append_to_list_var(f, "NEW", "z")
    assert f.read_text() == "OTHER=1\nTOKENS=a,b\nNEW=z\n"
    assert f.stat().st_mode & 0o777 == 0o600


def test_create_sandbox_access_token_exchanges_public_token() -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path == "/sandbox/public_token/create":
            return httpx.Response(200, json={"public_token": "public-x"})
        assert json.loads(request.content)["public_token"] == "public-x"  # noqa: S105
        return httpx.Response(200, json={"access_token": "access-y"})

    client = HttpPlaidClient("c", "s", transport=httpx.MockTransport(handler))
    assert client.create_sandbox_access_token() == "access-y"
    assert paths == ["/sandbox/public_token/create", "/item/public_token/exchange"]
