"""MCP server factory: one low-level ``mcp.server.Server`` over the role facades.

``build_server(engine, resolve_principal, ...)`` is transport-neutral. The resolver turns the
per-request transport context (the HTTP request for streamable HTTP; nothing for stdio) into
the caller's ``Principal`` and is consulted on EVERY ``tools/list`` and ``tools/call``, so a
revoked or expired key stops working mid-session. Authorization is the facade the
principal's role yields (``facade_for``), checked for every call; the role-filtered listing
is a convenience only (spec 8).

Tool failures are MCP tool errors (``isError: true``) whose ``structuredContent`` is
``{"error": {"code", "message", "details"}}`` with the simulator's error codes (spec 7.4),
plus ``UNAUTHENTICATED`` for a missing/invalid key and ``INTERNAL`` for unexpected faults.
"""

from __future__ import annotations

import functools
import json
import logging
from collections.abc import Callable, Mapping
from typing import Any

import anyio.to_thread
import mcp_types as types
from mcp.server import Server, ServerRequestContext
from mcp.shared.exceptions import MCPError
from pydantic import ValidationError

from mailsim import __version__
from mailsim.api.auth import InvalidCredential, verify_key
from mailsim.api.errors import INTERNAL, UNAUTHENTICATED
from mailsim.auth.principal import Principal
from mailsim.domain.errors import ErrorCode, PermissionDenied, SimError
from mailsim.engine.facades import facade_for
from mailsim.mcp.tools import TOOLS_BY_NAME, Deps, tools_for

log = logging.getLogger("mailsim.mcp")

PrincipalResolver = Callable[[Any], Principal]
"""(transport request or None) -> Principal; raises ``InvalidCredential``."""

INSTRUCTIONS = (
    "mailsim: a sandboxed email simulation for testing AI agents. Tools are scoped by the "
    "API key's role: agent keys act on one simulated mailbox; controller keys drive runs and "
    "the virtual clock; evaluator keys read traces and results. Mail never leaves the sandbox."
)
UNAUTHENTICATED_MESSAGE = "missing or invalid API key"


def verify_principal(session_factory: Any, key: str) -> Principal:
    with session_factory() as session:
        return verify_key(session, key)


def key_resolver(session_factory: Any, key: str) -> PrincipalResolver:
    """A fixed key (stdio): re-verified on every request, so revocation applies at once."""
    return lambda _request: verify_principal(session_factory, key)


def bearer_resolver(session_factory: Any) -> PrincipalResolver:
    """Streamable HTTP: the request's ``Authorization: Bearer <key>``."""

    def resolve(request: Any) -> Principal:
        header = request.headers.get("authorization", "") if request is not None else ""
        scheme, _, key = header.partition(" ")
        if scheme.lower() != "bearer" or not key.strip():
            raise InvalidCredential(UNAUTHENTICATED_MESSAGE)
        return verify_principal(session_factory, key.strip())

    return resolve


def _jsonable(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str))


def tool_error(
    code: str, message: str, details: Mapping[str, Any] | None = None
) -> types.CallToolResult:
    error = {"code": code, "message": message, "details": _jsonable(dict(details or {}))}
    return types.CallToolResult(
        content=[types.TextContent(text=f"{code}: {message}")],
        structured_content={"error": error},
        is_error=True,
    )


def tool_result(value: dict[str, Any]) -> types.CallToolResult:
    data = _jsonable(value)
    return types.CallToolResult(
        content=[types.TextContent(text=json.dumps(data, sort_keys=True))],
        structured_content=data,
    )


class McpService:
    """The synchronous core behind the MCP handlers (also unit-testable without a transport)."""

    def __init__(self, engine: Any, resolve_principal: PrincipalResolver, deps: Deps) -> None:
        self._engine, self._resolve, self._deps = engine, resolve_principal, deps

    def list_tools(self, request: Any) -> list[types.Tool]:
        try:
            principal = self._resolve(request)
        except InvalidCredential:
            raise MCPError(types.INVALID_REQUEST, UNAUTHENTICATED_MESSAGE) from None
        return [spec.tool() for spec in tools_for(principal.role)]

    def call_tool(
        self, request: Any, name: str, arguments: dict[str, Any] | None
    ) -> types.CallToolResult:
        try:
            principal = self._resolve(request)
        except InvalidCredential:
            return tool_error(UNAUTHENTICATED, UNAUTHENTICATED_MESSAGE)
        spec = TOOLS_BY_NAME.get(name)
        if spec is None:
            return tool_error(str(ErrorCode.UNSUPPORTED_OPERATION), f"unknown tool {name!r}",
                              {"tool": name})  # fmt: skip
        try:
            # authorization: the facade this credential's role allows, on every call
            facade = facade_for(self._engine, principal)
            if not isinstance(facade, spec.facades):
                raise PermissionDenied("this credential's role may not call this tool",
                                       details={"tool": name, "role": str(principal.role)})  # fmt: skip
            args = spec.args.model_validate(arguments or {})
            return tool_result(spec.handler(facade, args, self._deps))
        except ValidationError as exc:
            errors = [{"loc": [str(p) for p in e["loc"]], "msg": e["msg"]} for e in exc.errors()]
            return tool_error(str(ErrorCode.INVALID_REQUEST), "invalid tool arguments",
                              {"errors": errors})  # fmt: skip
        except SimError as exc:
            return tool_error(str(exc.code), exc.message, exc.details)
        except Exception:
            log.exception("MCP tool %s failed", name)
            return tool_error(INTERNAL, "internal error")


def build_server(
    engine: Any,
    resolve_principal: PrincipalResolver,
    *,
    session_factory: Any = None,
    evaluation_service: Any = None,
    persona_runtime_factory: Callable[[Any], Any] | None = None,
) -> Server[Any]:
    """An MCP server over ``engine`` (a SimEngine, typed loosely: interfaces never import
    ``engine.core``). ``session_factory`` defaults to ``engine.session_factory``."""
    deps = Deps(
        session_factory=session_factory or getattr(engine, "session_factory", None),
        evaluation_service=evaluation_service,
        persona_runtime_factory=persona_runtime_factory,
    )
    service = McpService(engine, resolve_principal, deps)

    async def on_list_tools(
        ctx: ServerRequestContext[Any], _params: types.PaginatedRequestParams | None
    ) -> types.ListToolsResult:
        tools = await anyio.to_thread.run_sync(service.list_tools, ctx.request)
        return types.ListToolsResult(tools=tools)

    async def on_call_tool(
        ctx: ServerRequestContext[Any], params: types.CallToolRequestParams
    ) -> types.CallToolResult:
        call = functools.partial(service.call_tool, ctx.request, params.name, params.arguments)
        return await anyio.to_thread.run_sync(call)

    def input_schema(name: str) -> dict[str, Any] | None:
        # unscoped by design (called before auth): schemas are public, authorization is not
        spec = TOOLS_BY_NAME.get(name)
        return spec.tool().input_schema if spec is not None else None

    return Server(
        "mailsim",
        version=__version__,
        title="mailsim email simulator",
        instructions=INSTRUCTIONS,
        get_tool_input_schema=input_schema,
        on_list_tools=on_list_tools,
        on_call_tool=on_call_tool,
    )


__all__ = [
    "INSTRUCTIONS",
    "McpService",
    "PrincipalResolver",
    "bearer_resolver",
    "build_server",
    "key_resolver",
    "tool_error",
    "tool_result",
    "verify_principal",
]
