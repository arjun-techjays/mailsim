"""How engine errors read to an AGENT, on every agent-facing interface.

Operators (controller/evaluator routes, actions, events) keep the engine's codes and
messages. An agent sees the same failure without simulation or run-lifecycle wording:

| engine code                 | agent code       | agent message              |
|-----------------------------|------------------|----------------------------|
| SIMULATION_LIMIT_EXCEEDED   | QUOTA_EXCEEDED   | action limit reached       |
| RUN_NOT_ACTIVE              | RUN_NOT_ACTIVE   | mailbox is not available   |

Every other code and message is passed through unchanged. Each interface keeps its own
status mapping (HTTP status, Gmail reason, Graph code, IMAP/SMTP reply code), keyed by the
engine code.
"""

from __future__ import annotations

from mailsim.domain.errors import ErrorCode

QUOTA_EXCEEDED = "QUOTA_EXCEEDED"
_AGENT_VIEW: dict[str, tuple[str, str]] = {
    ErrorCode.SIMULATION_LIMIT_EXCEEDED: (QUOTA_EXCEEDED, "action limit reached"),
    ErrorCode.RUN_NOT_ACTIVE: (str(ErrorCode.RUN_NOT_ACTIVE), "mailbox is not available"),
}


def agent_view(code: str, message: str, details: dict[str, object] | None = None,
               ) -> tuple[str, str, dict[str, object]]:  # fmt: skip
    """(code, message, details) as an agent may see them. Remapped errors carry no details."""
    mapped = _AGENT_VIEW.get(str(code))
    if mapped is None:
        return str(code), message, dict(details or {})
    return mapped[0], mapped[1], {}


__all__ = ["QUOTA_EXCEEDED", "agent_view"]
