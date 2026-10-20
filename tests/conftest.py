"""A scripted local HTTP server that stands in for api.appstoreconnect.apple.com.

Each test queues responses with `asc.reply(method, path, status, body, headers)`.
The server records every request it got in `asc.requests`, so a test can prove
that a call never reached HTTP, or reached it exactly once.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest


@dataclass
class Request:
    method: str
    path: str
    headers: dict[str, str]
    body: bytes


@dataclass
class Reply:
    status: int
    body: bytes
    headers: dict[str, str]


@dataclass
class FakeAsc:
    root: str = ""
    requests: list[Request] = field(default_factory=list)
    replies: dict[tuple[str, str], list[Reply]] = field(default_factory=dict)

    def reply(
        self,
        method: str,
        path: str,
        status: int = 200,
        body: Any = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        """Queue one response for `method path` (path includes the query string, if any)."""
        raw = body if isinstance(body, bytes) else json.dumps(body or {}).encode()
        self.replies.setdefault((method, path), []).append(
            Reply(status, raw, headers or {})
        )

    def calls(self, method: str, path_prefix: str = "") -> list[Request]:
        return [
            r
            for r in self.requests
            if r.method == method and r.path.startswith(path_prefix)
        ]


@pytest.fixture
def asc() -> Iterator[FakeAsc]:
    fake = FakeAsc()

    class Handler(BaseHTTPRequestHandler):
        def _serve(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""
            fake.requests.append(
                Request(self.command, self.path, dict(self.headers), body)
            )
            queue = fake.replies.get((self.command, self.path)) or []
            reply = (
                queue.pop(0)
                if queue
                else Reply(599, b'{"errors":[{"detail":"no scripted reply"}]}', {})
            )
            self.send_response(reply.status)
            for name, value in reply.headers.items():
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(reply.body)))
            self.end_headers()
            self.wfile.write(reply.body)

        do_GET = do_POST = do_PATCH = do_PUT = do_DELETE = _serve

        def log_message(self, format: str, *args: Any) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    fake.root = f"http://127.0.0.1:{server.server_address[1]}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield fake
    finally:
        server.shutdown()
        server.server_close()
