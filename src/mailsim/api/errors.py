"""Error contract (spec 7.4) -> HTTP mapping and the single error envelope.

Envelope: {"error": {"code": ..., "message": ..., "details": {...}, "request_id": ...}}
"""

from __future__ import annotations

import logging
import re
import uuid
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from mailsim.api.agent_errors import agent_view
from mailsim.domain.errors import ErrorCode, SimError

log = logging.getLogger("mailsim.api")
_RID = re.compile(r"[A-Za-z0-9._-]{1,64}")

HTTP_STATUS: dict[ErrorCode, int] = {
    ErrorCode.INVALID_REQUEST: 400,
    ErrorCode.PERMISSION_DENIED: 403,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.CONFLICT: 409,
    ErrorCode.RUN_NOT_ACTIVE: 409,
    ErrorCode.EXTERNAL_DELIVERY_BLOCKED: 422,
    ErrorCode.SIMULATION_LIMIT_EXCEEDED: 429,
    ErrorCode.RATE_LIMITED: 429,
    ErrorCode.UNSUPPORTED_OPERATION: 501,
    ErrorCode.UPSTREAM_UNAVAILABLE: 503,
}
UNAUTHENTICATED = "UNAUTHENTICATED"  # 401: missing/invalid credential (transport-level)
INTERNAL = "INTERNAL"  # 500: unexpected server fault; details are never leaked
AGENT_PATH = "/v1/mailboxes/"  # the agent-only REST routes (api/routers/mailbox.py)


class ErrorBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
    request_id: str


class ErrorEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    error: ErrorBody


def request_id(request: Request) -> str:
    rid = getattr(request.state, "request_id", None)
    if not rid:
        supplied = request.headers.get("x-request-id", "")
        # only echo well-formed client ids (no log/header injection); otherwise mint one
        rid = supplied if _RID.fullmatch(supplied) else uuid.uuid4().hex
        request.state.request_id = rid
    return str(rid)


def envelope(request: Request, status: int, code: str, message: str,
             details: dict[str, Any] | None = None) -> JSONResponse:  # fmt: skip
    body = ErrorEnvelope(
        error=ErrorBody(code=code, message=message, details=details or {},
                        request_id=request_id(request))
    )  # fmt: skip
    return JSONResponse(body.model_dump(), status_code=status,
                        headers={"x-request-id": body.error.request_id})  # fmt: skip


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(SimError)
    async def _sim(request: Request, exc: SimError) -> JSONResponse:
        code, message, details = str(exc.code), exc.message, exc.details
        if request.url.path.startswith(AGENT_PATH):  # agent mailbox routes: the agent's view
            code, message, details = agent_view(code, message, details)
        return envelope(request, HTTP_STATUS[exc.code], code, message, details)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [
            {"loc": [str(p) for p in e.get("loc", ())], "msg": str(e.get("msg", ""))}
            for e in exc.errors()
        ]
        return envelope(request, 400, str(ErrorCode.INVALID_REQUEST), "request validation failed",
                        {"errors": errors})  # fmt: skip

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {401: UNAUTHENTICATED, 403: str(ErrorCode.PERMISSION_DENIED),
                404: str(ErrorCode.NOT_FOUND), 405: str(ErrorCode.UNSUPPORTED_OPERATION)}.get(
            exc.status_code, str(ErrorCode.INVALID_REQUEST))  # fmt: skip
        response = envelope(request, exc.status_code, code, str(exc.detail))
        for name, value in (exc.headers or {}).items():  # e.g. WWW-Authenticate on 401
            response.headers[name] = value
        return response

    @app.exception_handler(Exception)
    async def _internal(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error", extra={"request_id": request_id(request)})
        return envelope(request, 500, INTERNAL, "internal error")
