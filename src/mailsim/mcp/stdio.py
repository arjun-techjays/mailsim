"""MCP over stdio: one process, one credential.

The key comes from the caller or ``MAILSIM_API_KEY`` and is verified at startup (an invalid
key fails fast, before anything is served) and again on every request, so revoking it, or
ending the agent's run, cuts the session off. There is no unauthenticated mode: the
dashboard's local no-auth switch never applies to MCP.

The composition root (``python -m mailsim.serve mcp-stdio``) builds the engine and calls
``run_stdio``; this package never constructs the engine itself.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from typing import Any

import anyio
from mcp.server import Server
from mcp.server.stdio import stdio_server

from mailsim.api.auth import InvalidCredential
from mailsim.mcp.server import build_server, key_resolver, verify_principal

API_KEY_ENV = "MAILSIM_API_KEY"


class StdioConfigError(Exception):
    """No usable credential: the server refuses to start."""


async def serve_stdio(server: Server[Any], stdin: Any = None, stdout: Any = None) -> None:
    """Serve ``server`` on stdin/stdout (or the given anyio text files) until EOF."""
    async with stdio_server(stdin, stdout) as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def run_stdio(
    engine: Any,
    key: str | None = None,
    *,
    session_factory: Any = None,
    evaluation_service: Any = None,
    persona_runtime_factory: Callable[[Any], Any] | None = None,
    environ: Mapping[str, str] | None = None,
    stdin: Any = None,
    stdout: Any = None,
) -> None:
    """Run the MCP server on stdio for one API key (agent, controller or evaluator)."""
    env = os.environ if environ is None else environ
    key = (key or env.get(API_KEY_ENV, "")).strip()
    if not key:
        raise StdioConfigError(f"set {API_KEY_ENV} to an API key (msk_...)")
    sf = session_factory or engine.session_factory
    try:
        principal = verify_principal(sf, key)
    except InvalidCredential:
        raise StdioConfigError(
            "the API key is missing, malformed, unknown, expired or revoked"
        ) from None
    server = build_server(engine, key_resolver(sf, key), session_factory=sf,
                          evaluation_service=evaluation_service,
                          persona_runtime_factory=persona_runtime_factory,
                          interface="mcp_stdio", role=principal.role)  # fmt: skip
    anyio.run(serve_stdio, server, stdin, stdout)


__all__ = ["API_KEY_ENV", "StdioConfigError", "run_stdio", "serve_stdio"]
