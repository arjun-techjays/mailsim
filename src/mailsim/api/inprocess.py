"""Send one HTTP request through an ASGI app in-process (no socket, no HTTP client).

Used by the dashboard's connection tests: the request walks the app's full middleware and
routing stack exactly as an external client's would, but never leaves the process.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from starlette.types import ASGIApp, Message

MAX_RESPONSE_BYTES = 1024 * 1024


@dataclass
class InProcessResponse:
    status: int
    headers: dict[str, str]
    body: bytes
    truncated: bool = False


async def asgi_request(
    app: ASGIApp,
    method: str,
    path: str,
    *,
    query: str = "",
    headers: dict[str, str] | None = None,
    body: bytes = b"",
    host: str = "127.0.0.1",
    scheme: str = "http",
    max_body: int = MAX_RESPONSE_BYTES,
) -> InProcessResponse:
    """`app(scope, receive, send)` for one request from a loopback client; the response
    body is capped at `max_body` bytes (`truncated` then says so)."""
    raw_headers = [(b"host", host.encode("latin-1"))]
    raw_headers += [(k.lower().encode("latin-1"), v.encode("latin-1"))
                    for k, v in (headers or {}).items()]  # fmt: skip
    if body:
        raw_headers.append((b"content-length", str(len(body)).encode()))
    scope: dict[str, Any] = {
        "type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1", "method": method.upper(), "scheme": scheme,
        "path": path, "raw_path": path.encode("latin-1"), "root_path": "",
        "query_string": query.encode("latin-1"), "headers": raw_headers,
        "client": ("127.0.0.1", 0), "server": None,
    }  # fmt: skip
    sent = False

    async def receive() -> Message:
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        return {"type": "http.disconnect"}

    out = InProcessResponse(status=500, headers={}, body=b"")
    chunks: list[bytes] = []
    size = 0

    async def send(message: Message) -> None:
        nonlocal size
        if message["type"] == "http.response.start":
            out.status = int(message["status"])
            out.headers = {k.decode("latin-1").lower(): v.decode("latin-1")
                           for k, v in message.get("headers", [])}  # fmt: skip
        elif message["type"] == "http.response.body":
            chunk = bytes(message.get("body", b""))
            room = max_body - size
            if len(chunk) > room:
                chunk, out.truncated = chunk[:room], True
            chunks.append(chunk)
            size += len(chunk)

    await app(scope, receive, send)
    out.body = b"".join(chunks)
    return out


__all__ = ["InProcessResponse", "asgi_request"]
