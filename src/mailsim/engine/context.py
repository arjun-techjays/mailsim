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

``agent_request`` scopes ONE agent request (an HTTP request, an MCP tool call, an IMAP command;
the interface opens a fresh one with ``use_agent_request``). The engine marks it once an agent
WRITE in it has committed; agent reads made after that point in the same request are the
interface building its response to that write (e.g. Gmail returning the sent message, an IMAP
re-sync after STORE). Those read-backs are still recorded, but neither counted against nor
refused by the read/event caps: a write that already happened must not answer "quota
exceeded". Reads before the first write, and every read of a request that writes nothing, count
as usual, so an agent cannot obtain uncapped reads of its own choosing; the most a write buys is
the read-back of the request that made it, and writes are themselves budgeted.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps

INTERFACE_NAMES = frozenset(
    {"rest", "gmail", "graph", "mcp_http", "mcp_stdio", "imap", "smtp", "api", "cli"}
)

current_interface: ContextVar[str | None] = ContextVar("mailsim_interface", default=None)
connection_test: ContextVar[bool] = ContextVar("mailsim_connection_test", default=False)


@dataclass
class AgentRequest:
    """One agent request; `wrote` once an agent write in it has committed."""

    wrote: bool = False


agent_request: ContextVar[AgentRequest | None] = ContextVar("mailsim_agent_request", default=None)


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


@contextmanager
def use_agent_request() -> Iterator[AgentRequest]:
    """Run the block as one fresh agent request (never inherits an outer one's state)."""
    request = AgentRequest()
    token = agent_request.set(request)
    try:
        yield request
    finally:
        agent_request.reset(token)


@contextmanager
def use_request_item() -> Iterator[AgentRequest]:
    """One item of a request that loops over agent-supplied ids (Gmail batchModify/batchDelete,
    thread operations): its reads count until ITS OWN write, so one item's write never
    exempts another item's reads (an id repeated a thousand times buys nothing). A write in
    it still marks the enclosing request, whose response building after the loop remains a
    read-back."""
    parent = agent_request.get()
    with use_agent_request() as item:
        yield item
    if parent is not None and item.wrote:
        parent.wrote = True


def in_agent_request[**P, R](fn: Callable[P, R]) -> Callable[P, R]:
    """`fn` wrapped so each call is one fresh agent request (for worker-thread hand-offs)."""

    @wraps(fn)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        with use_agent_request():
            return fn(*args, **kwargs)

    return wrapper


def note_agent_write() -> None:
    """Called by the engine after an agent write committed: later reads are read-backs."""
    request = agent_request.get()
    if request is not None:
        request.wrote = True


def is_read_back() -> bool:
    """An agent read made after a committed write of the same agent request."""
    request = agent_request.get()
    return request is not None and request.wrote


def with_interface[**P, R](name: str, fn: Callable[P, R]) -> Callable[P, R]:
    """`fn` wrapped so it runs attributed to `name` (for worker-thread hand-offs)."""

    @wraps(fn)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        with use_interface(name):
            return fn(*args, **kwargs)

    return wrapper


__all__ = [
    "INTERFACE_NAMES",
    "AgentRequest",
    "agent_request",
    "connection_test",
    "current_interface",
    "in_agent_request",
    "is_read_back",
    "note_agent_write",
    "use_agent_request",
    "use_connection_test",
    "use_request_item",
    "use_interface",
    "with_interface",
]
