"""Gmail-shaped errors for the Gmail API v1 compatibility profile.

Every failure under ``/gmail/v1`` and ``/upload/gmail/v1`` answers Google's JSON error shape::

    {"error": {"code": 404, "message": "...", "status": "NOT_FOUND",
               "errors": [{"message": "...", "domain": "global", "reason": "notFound"}],
               "details": [{"@type": "type.googleapis.com/google.rpc.ErrorInfo",
                            "reason": "NOT_FOUND", "domain": "global", "metadata": {}}]}}

``details[0]`` is a ``google.rpc.ErrorInfo`` whose ``reason`` is the simulator's neutral error
code (spec 7.4), so callers can tell apart codes that share an HTTP status (e.g. CONFLICT and
RUN_NOT_ACTIVE are both 409). The REST error envelope is never used on these paths.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from starlette.exceptions import HTTPException

from mailsim.domain.errors import ErrorCode, SimError

log = logging.getLogger("mailsim.adapters.gmail")

UNAUTHENTICATED = "UNAUTHENTICATED"
INTERNAL = "INTERNAL"
ERROR_DOMAIN = "global"

# neutral code -> (HTTP status, errors[].reason, error.status)
GMAIL_ERRORS: dict[str, tuple[int, str, str]] = {
    ErrorCode.INVALID_REQUEST: (400, "invalidArgument", "INVALID_ARGUMENT"),
    ErrorCode.NOT_FOUND: (404, "notFound", "NOT_FOUND"),
    ErrorCode.PERMISSION_DENIED: (403, "forbidden", "PERMISSION_DENIED"),
    ErrorCode.UNSUPPORTED_OPERATION: (501, "notImplemented", "UNIMPLEMENTED"),
    ErrorCode.CONFLICT: (409, "conflict", "ABORTED"),
    ErrorCode.RUN_NOT_ACTIVE: (409, "failedPrecondition", "FAILED_PRECONDITION"),
    ErrorCode.EXTERNAL_DELIVERY_BLOCKED: (400, "failedPrecondition", "FAILED_PRECONDITION"),
    ErrorCode.SIMULATION_LIMIT_EXCEEDED: (429, "rateLimitExceeded", "RESOURCE_EXHAUSTED"),
    ErrorCode.RATE_LIMITED: (429, "rateLimitExceeded", "RESOURCE_EXHAUSTED"),
    ErrorCode.UPSTREAM_UNAVAILABLE: (503, "backendError", "UNAVAILABLE"),
    UNAUTHENTICATED: (401, "authError", "UNAUTHENTICATED"),
    INTERNAL: (500, "backendError", "INTERNAL"),
}
_HTTP_CODES = {401: UNAUTHENTICATED, 403: ErrorCode.PERMISSION_DENIED, 404: ErrorCode.NOT_FOUND,
               405: ErrorCode.UNSUPPORTED_OPERATION}  # fmt: skip


class GmailAuthError(Exception):
    """Missing, malformed, unknown, expired or revoked credential (401, no further detail)."""


def _metadata(details: dict[str, object]) -> dict[str, str]:
    return {k: v if isinstance(v, str) else json.dumps(v, default=str, sort_keys=True)
            for k, v in sorted(details.items())}  # fmt: skip


def gmail_error(code: str, message: str, details: dict[str, object] | None = None) -> JSONResponse:
    status, reason, grpc = GMAIL_ERRORS[code]
    body: dict[str, Any] = {
        "error": {
            "code": status,
            "message": message,
            "errors": [{"message": message, "domain": "global", "reason": reason}],
            "status": grpc,
            "details": [{"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": str(code),
                         "domain": ERROR_DOMAIN, "metadata": _metadata(details or {})}],
        }
    }  # fmt: skip
    headers = (
        {"WWW-Authenticate": 'Bearer realm="api", error="invalid_token"'} if status == 401 else None
    )
    return JSONResponse(body, status_code=status, headers=headers)


def to_response(exc: Exception) -> JSONResponse:
    """Translate any exception raised under the Gmail paths to the Gmail error shape."""
    if isinstance(exc, SimError):
        return gmail_error(str(exc.code), exc.message, exc.details)
    if isinstance(exc, GmailAuthError):
        return gmail_error(UNAUTHENTICATED, "Request had invalid authentication credentials. "
                                            "Expected an API key as a Bearer token.")  # fmt: skip
    if isinstance(exc, RequestValidationError):
        problems = "; ".join(f"{'.'.join(str(p) for p in e.get('loc', ()))}: {e.get('msg', '')}"
                             for e in exc.errors())  # fmt: skip
        return gmail_error(ErrorCode.INVALID_REQUEST, f"Invalid request: {problems}"[:500])
    if isinstance(exc, HTTPException):
        code = _HTTP_CODES.get(exc.status_code, ErrorCode.INVALID_REQUEST)
        return gmail_error(code, str(exc.detail))
    log.exception("unhandled error in the Gmail adapter", exc_info=exc)
    return gmail_error(INTERNAL, "Internal error encountered.")


class GmailRoute(APIRoute):
    """Route class that renders every failure (validation, auth, engine) as a Gmail error, so the
    app-wide REST exception handlers never see these paths."""

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def handler(request: Request) -> Response:
            try:
                return await original(request)
            except Exception as exc:  # noqa: BLE001 - every error becomes a Gmail error body
                return to_response(exc)

        return handler
