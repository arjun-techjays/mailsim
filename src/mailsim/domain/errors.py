"""Error contract (spec 7.4) as an exception hierarchy.

Phase 2 maps these codes to HTTP; provider adapters map them to provider shapes.
"""

from __future__ import annotations

from enum import StrEnum


class ErrorCode(StrEnum):
    INVALID_REQUEST = "INVALID_REQUEST"
    NOT_FOUND = "NOT_FOUND"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    UNSUPPORTED_OPERATION = "UNSUPPORTED_OPERATION"
    CONFLICT = "CONFLICT"
    RATE_LIMITED = "RATE_LIMITED"
    SIMULATION_LIMIT_EXCEEDED = "SIMULATION_LIMIT_EXCEEDED"
    RUN_NOT_ACTIVE = "RUN_NOT_ACTIVE"
    EXTERNAL_DELIVERY_BLOCKED = "EXTERNAL_DELIVERY_BLOCKED"


class SimError(Exception):
    """Base class; carries a stable machine-readable code."""

    code: ErrorCode = ErrorCode.INVALID_REQUEST

    def __init__(self, message: str, *, details: dict[str, object] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details: dict[str, object] = details or {}

    def to_dict(self) -> dict[str, object]:
        return {"code": str(self.code), "message": self.message, "details": self.details}


class InvalidRequest(SimError):
    code = ErrorCode.INVALID_REQUEST


class NotFound(SimError):
    code = ErrorCode.NOT_FOUND


class PermissionDenied(SimError):
    code = ErrorCode.PERMISSION_DENIED


class UnsupportedOperation(SimError):
    code = ErrorCode.UNSUPPORTED_OPERATION


class Conflict(SimError):
    code = ErrorCode.CONFLICT


class RateLimited(SimError):
    code = ErrorCode.RATE_LIMITED


class SimulationLimitExceeded(SimError):
    code = ErrorCode.SIMULATION_LIMIT_EXCEEDED


class RunNotActive(SimError):
    code = ErrorCode.RUN_NOT_ACTIVE


class ExternalDeliveryBlocked(SimError):
    code = ErrorCode.EXTERNAL_DELIVERY_BLOCKED
