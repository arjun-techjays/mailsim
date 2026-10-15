"""Request body size cap (spec 10 "resource limits"), as a pure ASGI middleware.

The limit is ``app.state.max_body_bytes`` when set, else ``MAILSIM_MAX_BODY_BYTES``, else
40 MB. A request is refused before the handler runs when its declared Content-Length is over
the limit, or when the streamed body (chunked or mis-declared) grows past it. The refusal is
the frozen error envelope with 400 INVALID_REQUEST (413 is not in the frozen error map).
"""

from __future__ import annotations

import os
from typing import Any

from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from mailsim.api.errors import envelope
from mailsim.domain.errors import ErrorCode

DEFAULT_MAX_BODY_BYTES = 40 * 1024 * 1024
TOO_LARGE = "request body too large"


def max_body_bytes(app: Any) -> int:
    configured = getattr(getattr(app, "state", None), "max_body_bytes", None)
    if configured is not None:
        return int(configured)
    return int(os.environ.get("MAILSIM_MAX_BODY_BYTES", DEFAULT_MAX_BODY_BYTES))


class BodySizeLimit:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def _reject(self, scope: Scope, receive: Receive, send: Send, limit: int) -> None:
        response = envelope(Request(scope), 400, str(ErrorCode.INVALID_REQUEST), TOO_LARGE,
                            {"limit_bytes": limit})  # fmt: skip
        await response(scope, receive, send)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        limit = max_body_bytes(scope.get("app"))
        declared = dict(scope.get("headers") or []).get(b"content-length")
        if declared is not None and (not declared.isdigit() or int(declared) > limit):
            await self._reject(scope, receive, send, limit)
            return
        # Buffer the body (the JSON handlers read it whole anyway) while counting bytes.
        chunks: list[bytes] = []
        size = 0
        while True:
            message = await receive()
            if message["type"] != "http.request":
                break  # client went away; let the app see the disconnect below
            chunk = message.get("body", b"")
            size += len(chunk)
            if size > limit:
                await self._reject(scope, receive, send, limit)
                return
            chunks.append(chunk)
            if not message.get("more_body", False):
                break
        body = b"".join(chunks)
        replayed = False

        async def replay() -> Message:
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await self.app(scope, replay, send)
