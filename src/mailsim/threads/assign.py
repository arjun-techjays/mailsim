"""Pure threading logic (no database access) so it is property-testable.

Rules (spec 4.1: do not rely exclusively on subject):
1. A message joins the thread of any message in the mailbox whose Message-ID appears in its
   In-Reply-To/References, and of any message that references *its* Message-ID (so
   out-of-order arrival converges).
2. If several threads are linked, they are merged into the lexicographically smallest id.
3. With no header links at all, a message whose subject carries a reply/forward prefix
   (Re:/Fwd:) joins an existing thread with the same normalised subject. A fresh message
   with a coincidentally equal subject starts its own thread.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_PREFIX = re.compile(r"^\s*((re|fwd?|aw|sv)\s*(\[\d+\])?\s*:\s*)+", re.IGNORECASE)
_WS = re.compile(r"\s+")


def subject_key(subject: str) -> str:
    return _WS.sub(" ", _PREFIX.sub("", subject)).strip().casefold()


def has_reply_prefix(subject: str) -> bool:
    return bool(_PREFIX.match(subject))


@dataclass(frozen=True)
class ThreadCandidate:
    """The minimum an existing message must expose to take part in threading."""

    message_id_header: str
    thread_id: str
    in_reply_to: str | None
    references: tuple[str, ...]
    subject_key: str


@dataclass
class ThreadDecision:
    thread_id: str | None  # None => create a new thread
    merged_threads: list[str] = field(default_factory=list)  # threads folded into thread_id


def decide_thread(
    *,
    message_id_header: str,
    in_reply_to: str | None,
    references: list[str],
    subject: str,
    existing: list[ThreadCandidate],
) -> ThreadDecision:
    linked_ids = {i for i in [in_reply_to, *references] if i}
    threads: set[str] = set()
    for cand in existing:
        parent_of_new = cand.message_id_header in linked_ids
        child_of_new = message_id_header in {cand.in_reply_to, *cand.references}
        if parent_of_new or child_of_new:
            threads.add(cand.thread_id)
    if threads:
        target = min(threads)
        return ThreadDecision(target, sorted(threads - {target}))
    if not linked_ids and has_reply_prefix(subject):
        key = subject_key(subject)
        matches = sorted({c.thread_id for c in existing if c.subject_key == key})
        if matches:
            return ThreadDecision(matches[0])
    return ThreadDecision(None)
