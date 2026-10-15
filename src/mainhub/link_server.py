"""Local, one-shot web page that runs Plaid Link so you can connect a real bank.

Binds to 127.0.0.1 only. Every URL contains a random secret path segment, so other pages or
programs on this machine cannot trigger the flow. The access token is handed to `on_token`
and never sent back to the browser or printed.
"""

import json
import secrets
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Protocol

from mainhub.errors import MainHubError

_PAGE = """<!doctype html><meta charset="utf-8"><title>Connect a bank</title>
<body style="font:16px system-ui;max-width:32rem;margin:4rem auto">
<h1>Connect a bank to MainHub</h1><p id="msg">Opening Plaid Link&hellip;</p>
<script src="https://cdn.plaid.com/link/v2/stable/link-initialize.js"></script>
<script>
const base = location.pathname.replace(/\\/$/, "");
const msg = document.getElementById("msg");
async function post(path, body) {
  const r = await fetch(base + path, {method: "POST", body: JSON.stringify(body || {})});
  if (!r.ok) throw new Error("request failed");
  return r.json();
}
post("/token").then(({link_token}) => Plaid.create({
  token: link_token,
  onSuccess: async (public_token) => {
    msg.textContent = "Saving\\u2026";
    try {
      await post("/exchange", {public_token});
      msg.textContent = "Connected. You can close this tab.";
    }
    catch (e) { msg.textContent = "Something went wrong; check the terminal."; }
  },
  onExit: () => { msg.textContent = "Closed without connecting. Re-run the command to retry."; },
}).open()).catch(() => { msg.textContent = "Could not start Plaid Link; check the terminal."; });
</script>"""


class PlaidLinkApi(Protocol):
    def create_link_token(self, redirect_uri: str | None = None) -> str: ...

    def exchange_public_token(self, public_token: str) -> str: ...


class LinkServer(HTTPServer):
    def __init__(
        self,
        api: PlaidLinkApi,
        on_token: Callable[[str], None],
        secret: str | None = None,
        port: int = 0,
    ) -> None:
        super().__init__(("127.0.0.1", port), _Handler)
        self.api = api
        self.on_token = on_token
        self.secret = secret or secrets.token_urlsafe(16)
        self.error: str | None = None
        self.done = False

    @property
    def url(self) -> str:
        return f"http://localhost:{self.server_address[1]}/{self.secret}/"


class _Handler(BaseHTTPRequestHandler):
    server: LinkServer

    def log_message(self, format: str, *args: object) -> None:
        """Silence request logging (URLs contain the secret path)."""

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, data: dict[str, str]) -> None:
        self._send(status, json.dumps(data).encode(), "application/json")

    def do_GET(self) -> None:
        if self.path.rstrip("/") == f"/{self.server.secret}":
            self._send(200, _PAGE.encode(), "text/html; charset=utf-8")
        else:
            self._send(404, b"not found", "text/plain")

    def do_POST(self) -> None:
        prefix = f"/{self.server.secret}"
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(min(length, 10_000))
        try:
            if self.path == f"{prefix}/token":
                self._json(200, {"link_token": self.server.api.create_link_token()})
            elif self.path == f"{prefix}/exchange":
                public_token = str(json.loads(raw)["public_token"])
                self.server.on_token(self.server.api.exchange_public_token(public_token))
                self.server.done = True
                self._json(200, {"ok": "true"})
            else:
                self._send(404, b"not found", "text/plain")
        except (MainHubError, KeyError, ValueError) as exc:
            self.server.error = (
                type(exc).__name__ if not isinstance(exc, MainHubError) else str(exc)
            )
            self._json(500, {"error": "failed"})


def serve_until_linked(server: LinkServer) -> None:
    """Handle requests until a bank is linked (or the user presses Ctrl+C)."""
    while not server.done:
        server.handle_request()
