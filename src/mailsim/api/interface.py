"""Interface attribution for HTTP requests (`actions.interface`, see engine/context.py).

The HTTP app serves several interfaces; the path decides which one a request belongs to:
agent REST mailbox routes (`/v1/mailboxes/...`) are ``rest``, the Gmail / Graph adapters and
the MCP endpoint are ``gmail`` / ``graph`` / ``mcp_http``, and every other route (control
plane, simulator, operator reads) is ``api``. Sync handlers run in worker threads that copy
this request's context, so the engine sees the value.
"""

from __future__ import annotations

from starlette.types import ASGIApp, Receive, Scope, Send

from mailsim.engine.context import use_interface

PREFIXES: tuple[tuple[str, str], ...] = (
    ("/v1/mailboxes/", "rest"),
    ("/gmail/", "gmail"),
    ("/graph/", "graph"),
    ("/mcp/", "mcp_http"),
)


def interface_for_path(path: str) -> str:
    if path == "/mcp":
        return "mcp_http"
    return next((name for prefix, name in PREFIXES if path.startswith(prefix)), "api")


class InterfaceAttribution:
    """ASGI middleware: run each HTTP request attributed to its interface."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        with use_interface(interface_for_path(scope.get("path", ""))):
            await self.app(scope, receive, send)
