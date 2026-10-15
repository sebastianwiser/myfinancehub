import threading

import httpx

from mainhub.errors import ConnectorError
from mainhub.link_server import LinkServer, serve_until_linked


class FakeApi:
    def create_link_token(self, redirect_uri: str | None = None) -> str:
        return "link-fake"

    def exchange_public_token(self, public_token: str) -> str:
        if public_token == "bad":  # noqa: S105
            raise ConnectorError("exchange rejected")
        return "access-fake"


def _start(tokens: list[str]) -> tuple[LinkServer, threading.Thread]:
    server = LinkServer(FakeApi(), tokens.append, secret="s3cret")  # noqa: S106
    thread = threading.Thread(target=serve_until_linked, args=(server,), daemon=True)
    thread.start()
    return server, thread


def test_flow_saves_token_and_stops() -> None:
    tokens: list[str] = []
    server, thread = _start(tokens)
    base = server.url
    assert "Plaid.create" in httpx.get(base).text
    assert httpx.post(base + "token").json() == {"link_token": "link-fake"}
    reply = httpx.post(base + "exchange", content='{"public_token": "public-1"}')
    assert reply.status_code == 200
    assert "access-fake" not in reply.text  # the token never goes back to the browser
    thread.join(timeout=5)
    server.server_close()
    assert tokens == ["access-fake"]
    assert not thread.is_alive()


def test_wrong_secret_path_is_rejected() -> None:
    tokens: list[str] = []
    server, thread = _start(tokens)
    port = server.server_address[1]
    assert httpx.get(f"http://localhost:{port}/wrong/").status_code == 404
    assert httpx.post(f"http://localhost:{port}/wrong/token").status_code == 404
    httpx.post(server.url + "exchange", content='{"public_token": "p"}')  # finish and stop
    thread.join(timeout=5)
    server.server_close()
    assert tokens == ["access-fake"]


def test_exchange_failure_is_reported_not_saved() -> None:
    tokens: list[str] = []
    server, thread = _start(tokens)
    reply = httpx.post(server.url + "exchange", content='{"public_token": "bad"}')
    assert reply.status_code == 500
    assert server.error == "exchange rejected"
    assert tokens == []
    httpx.post(server.url + "exchange", content='{"public_token": "ok"}')
    thread.join(timeout=5)
    server.server_close()
