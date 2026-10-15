"""MCP over streamable HTTP at ``/mcp``, mounted into the REST app by ``serve.mount_adapters``.

``router`` (an APIRouter) carries one raw ASGI route. Each request:

1. needs ``Authorization: Bearer <sandbox key>`` (any role); a missing or invalid key is
   answered ``401`` with the REST error envelope (code ``UNAUTHENTICATED``) before any MCP
   processing, and the key is verified again by every tool call;
2. is served statelessly (no ``Mcp-Session-Id``) with JSON responses, by a session manager
   that lives only for that request. Only POST is routed: with no sessions there is no GET
   notification stream and nothing to DELETE, so both answer 405 (the spec allows this).
   Credentials are per request, so there is no session state to bind to a key, and nothing
   needs the host app's lifespan: the router works in any FastAPI app (and under
   TestClient) without startup hooks.

The engine and collaborators come from ``app.state`` (``engine``, ``session_factory``,
``evaluation_service``, ``persona_runtime_factory``), exactly as the REST routers use them.
"""

from __future__ import annotations

import logging
from typing import Any

import anyio.to_thread
from fastapi import APIRouter
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.requests import Request
from starlette.routing import Route
from starlette.types import Receive, Scope, Send

from mailsim.api.auth import InvalidCredential
from mailsim.api.errors import INTERNAL, UNAUTHENTICATED, envelope
from mailsim.domain.errors import ErrorCode
from mailsim.mcp.server import UNAUTHENTICATED_MESSAGE, bearer_resolver, build_server

PATH = "/mcp"
log = logging.getLogger("mailsim.mcp")


class McpEndpoint:
    """Raw ASGI endpoint (streaming-capable) for the MCP streamable HTTP transport."""

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        request = Request(scope, receive)
        state = request.app.state
        engine = getattr(state, "engine", None)
        session_factory = getattr(state, "session_factory", None)
        if engine is None or session_factory is None:
            response = envelope(request, 501, str(ErrorCode.UNSUPPORTED_OPERATION),
                                "server is not configured with an engine")  # fmt: skip
            await response(scope, receive, send)
            return
        resolve = bearer_resolver(session_factory)
        try:
            await anyio.to_thread.run_sync(resolve, request)
        except InvalidCredential:
            response = envelope(request, 401, UNAUTHENTICATED, UNAUTHENTICATED_MESSAGE)
            response.headers["WWW-Authenticate"] = "Bearer"
            await response(scope, receive, send)
            return
        except Exception:  # never leak the cause (SQL, hosts, hashes) to the caller
            log.exception("MCP credential check failed")
            await envelope(request, 500, INTERNAL, "internal error")(scope, receive, send)
            return
        server = build_server(
            engine,
            resolve,
            session_factory=session_factory,
            evaluation_service=getattr(state, "evaluation_service", None),
            persona_runtime_factory=getattr(state, "persona_runtime_factory", None),
            interface="mcp_http",
        )
        manager = StreamableHTTPSessionManager(app=server, stateless=True, json_response=True)
        async with manager.run():
            await manager.handle_request(scope, receive, send)


def _routes() -> list[Any]:
    return [Route(PATH, endpoint=McpEndpoint(), methods=["POST"],
                  include_in_schema=False, name="mcp")]  # fmt: skip


router = APIRouter()
router.routes.extend(_routes())

__all__ = ["PATH", "McpEndpoint", "router"]
