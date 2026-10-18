"""Which interface a command came through (REST, Gmail, IMAP, ...), for the audit trail.

Interfaces set ``current_interface`` around the work they do for a caller (``use_interface``)
and ``SimEngine`` stamps it on every recorded action. It is attribution only: it never
changes what a command may do. Context variables follow ``asyncio.to_thread`` / anyio worker
threads (they copy the caller's context), so an interface sets it once per request or session.

``connection_test`` marks an OPERATOR's key check (the dashboard's "Test connection", made
server-side by ``POST /v1/runs/{id}/connection-tests``). Only that operator route sets it, in
process, around its own probe; nothing a caller sends (header, body, key) can. While it is set,
an agent-key command is read-only (anything else is refused), arms no triggers and is recorded
with the actor ``connection_test`` instead of ``agent``, so it is never graded or counted as an
agent action.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps

INTERFACE_NAMES = frozenset(
    {"rest", "gmail", "graph", "mcp_http", "mcp_stdio", "imap", "smtp", "api", "cli"}
)

current_interface: ContextVar[str | None] = ContextVar("mailsim_interface", default=None)
connection_test: ContextVar[bool] = ContextVar("mailsim_connection_test", default=False)


@contextmanager
def use_interface(name: str | None) -> Iterator[None]:
    """Attribute every command inside the block to `name` (restored on exit)."""
    if name is not None and name not in INTERFACE_NAMES:
        raise ValueError(f"unknown interface {name!r}")
    token = current_interface.set(name)
    try:
        yield
    finally:
        current_interface.reset(token)


@contextmanager
def use_connection_test() -> Iterator[None]:
    """Mark every command inside the block as an operator connection test (restored on exit)."""
    token = connection_test.set(True)
    try:
        yield
    finally:
        connection_test.reset(token)


def with_interface[**P, R](name: str, fn: Callable[P, R]) -> Callable[P, R]:
    """`fn` wrapped so it runs attributed to `name` (for worker-thread hand-offs)."""

    @wraps(fn)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        with use_interface(name):
            return fn(*args, **kwargs)

    return wrapper


__all__ = [
    "INTERFACE_NAMES",
    "connection_test",
    "current_interface",
    "use_connection_test",
    "use_interface",
    "with_interface",
]
