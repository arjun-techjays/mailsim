"""Graph error shape and the mapping from the simulator's error contract (spec 7.4).

Body: {"error": {"code", "message", "innerError": {"request-id", "client-request-id", "date"}}}
Every Graph route uses `GraphRoute`, so nothing under /graph/v1.0 ever answers with the
neutral /v1 envelope: authentication, validation, engine and unexpected failures all come
back in the Graph shape.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from typing import Any

from fastapi import Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from starlette.exceptions import HTTPException

from mailsim.adapters.graph.odata import QueryNotSupported
from mailsim.api.errors import request_id
from mailsim.domain.errors import ErrorCode, SimError

log = logging.getLogger("mailsim.adapters.graph")
_CLIENT_RID = re.compile(r"[A-Za-z0-9._-]{1,64}")

# simulator error code -> (HTTP status, Graph error code). Graph codes are unique per
# simulator code so a client (and the contract driver) can map them back unambiguously.
GRAPH_ERRORS: dict[ErrorCode, tuple[int, str]] = {
    ErrorCode.INVALID_REQUEST: (400, "ErrorInvalidRequest"),
    ErrorCode.NOT_FOUND: (404, "ErrorItemNotFound"),
    ErrorCode.PERMISSION_DENIED: (403, "ErrorAccessDenied"),
    ErrorCode.UNSUPPORTED_OPERATION: (501, "ErrorNotSupported"),
    ErrorCode.CONFLICT: (409, "ErrorIrresolvableConflict"),
    ErrorCode.RATE_LIMITED: (429, "ErrorTooManyRequests"),
    ErrorCode.SIMULATION_LIMIT_EXCEEDED: (429, "ErrorQuotaExceeded"),
    ErrorCode.RUN_NOT_ACTIVE: (412, "ErrorPreconditionFailed"),
    ErrorCode.EXTERNAL_DELIVERY_BLOCKED: (403, "ErrorMessageSubmissionBlocked"),
    ErrorCode.UPSTREAM_UNAVAILABLE: (503, "ErrorServiceUnavailable"),
}
UNAUTHENTICATED = (401, "InvalidAuthenticationToken")
QUERY_NOT_SUPPORTED = (400, "ErrorNotSupported")
SYNC_STATE_NOT_FOUND = (410, "SyncStateNotFound")
INTERNAL = (500, "generalException")


class GraphAuthError(Exception):
    """Missing/invalid credential (401). Never says whether a key exists."""


class SyncStateGone(Exception):
    """A delta token from before a run reset: the client must restart the sync (410)."""


def graph_error(request: Request, status: int, code: str, message: str,
                headers: dict[str, str] | None = None) -> JSONResponse:  # fmt: skip
    rid = request_id(request)
    supplied = request.headers.get("client-request-id", "")
    client_rid = supplied if _CLIENT_RID.fullmatch(supplied) else rid
    inner = {"request-id": rid, "client-request-id": client_rid,
             "date": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S")}  # fmt: skip
    body = {"error": {"code": code, "message": message, "innerError": inner}}
    out = {"request-id": rid, "client-request-id": client_rid, **(headers or {})}
    return JSONResponse(body, status_code=status, headers=out)


def error_response(request: Request, exc: Exception) -> JSONResponse:
    if isinstance(exc, QueryNotSupported):
        return graph_error(request, *QUERY_NOT_SUPPORTED, exc.message)
    if isinstance(exc, SimError):
        status, code = GRAPH_ERRORS[exc.code]
        headers = {"Retry-After": "1"} if exc.code == ErrorCode.RATE_LIMITED else None
        return graph_error(request, status, code, exc.message, headers)
    if isinstance(exc, GraphAuthError):
        return graph_error(request, *UNAUTHENTICATED, str(exc),
                           {"WWW-Authenticate": 'Bearer realm="api"'})  # fmt: skip
    if isinstance(exc, SyncStateGone):
        return graph_error(request, *SYNC_STATE_NOT_FOUND, str(exc))
    if isinstance(exc, RequestValidationError):
        errors = "; ".join(
            f"{'/'.join(str(p) for p in e.get('loc', ()))}: {e.get('msg', '')}"
            for e in exc.errors()
        )
        return graph_error(request, 400, "ErrorInvalidRequest", f"invalid request: {errors}")
    if isinstance(exc, HTTPException):
        code = {404: "ErrorItemNotFound", 405: "ErrorNotSupported"}.get(
            exc.status_code, "ErrorInvalidRequest"
        )
        return graph_error(request, exc.status_code, code, str(exc.detail))
    log.exception("unhandled Graph adapter error", extra={"request_id": request_id(request)})
    return graph_error(request, *INTERNAL, "internal error")


class GraphRoute(APIRoute):
    """Route class that renders every failure (including dependency and body validation
    failures, which happen inside the route handler) in the Graph error shape."""

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()

        async def graph_handler(request: Request) -> Response:
            try:
                return await handler(request)
            except Exception as exc:  # every failure gets the Graph shape
                return error_response(request, exc)

        return graph_handler
