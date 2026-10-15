"""Frozen cross-adapter contract-test drivers (Wave 0).

Every agent-facing interface (neutral facade now; REST, Gmail, Graph, IMAP+SMTP and MCP in
Waves 1-2) implements `MailboxDriver` and is appended to `DRIVERS`. The same tests in
`test_contract.py` then run against each, and read-after-write is checked across every
(writer, reader) pair on the SAME run (spec 5.5: one canonical state behind all adapters).

Drivers translate their native ids/errors into the neutral vocabulary:
- message references are the driver's own ids; `canonical_id()` maps back for comparisons
- errors are normalised with `error_code(exc)` to `mailsim.domain.errors.ErrorCode` values
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar, Protocol

from mailsim.domain.errors import ErrorCode, SimError
from mailsim.engine.facades import AgentMailbox

ALL_CAPABILITIES = frozenset(
    {"list", "page", "get", "thread", "send", "reply", "move", "flag", "label", "delete",
     "changes", "search"}
)  # fmt: skip


@dataclass(frozen=True)
class Msg:
    """Neutral view of a message as seen through a driver."""

    ref: str  # driver-native id
    canonical_id: str
    message_id_header: str
    thread_ref: str
    subject: str
    folder: str  # system type: inbox|sent|drafts|trash|archive
    flags: frozenset[str]
    labels: frozenset[str]
    in_reply_to: str | None = None
    body: str = ""  # everything textual the agent can read through this driver (body, headers)


class MailboxDriver(Protocol):
    name: ClassVar[str]
    capabilities: ClassVar[frozenset[str]]

    def list(self, folder: str | None = None) -> list[Msg]: ...
    def page(self, size: int, cursor: str | None) -> tuple[list[Msg], str | None]: ...
    def get(self, ref: str) -> Msg: ...
    def thread(self, thread_ref: str) -> list[Msg]: ...
    def search(self, text: str) -> list[Msg]: ...
    def send(self, to: list[str], subject: str, text: str) -> str: ...  # returns Message-ID header
    def reply(self, ref: str, text: str) -> None: ...
    def move(self, ref: str, folder: str) -> None: ...
    def flag(self, ref: str, add: list[str]) -> None: ...
    def label(self, ref: str, add: list[str]) -> None: ...
    def delete(self, ref: str) -> None: ...
    def changes(self, since: int) -> tuple[list[str], int]: ...  # (canonical ids, cursor)
    def error_code(self, exc: BaseException) -> str: ...


_FOLDER = {
    "INBOX": "inbox",
    "Sent": "sent",
    "Drafts": "drafts",
    "Trash": "trash",
    "Archive": "archive",
}


class NeutralDriver:
    """The neutral engine facade (what REST and every adapter must agree with)."""

    name: ClassVar[str] = "neutral"
    capabilities: ClassVar[frozenset[str]] = ALL_CAPABILITIES

    def __init__(self, mailbox: AgentMailbox) -> None:
        self.mb = mailbox

    @staticmethod
    def _msg(m: Any) -> Msg:
        return Msg(m.id, m.id, m.message_id_header, m.thread_id, m.subject, _FOLDER[m.folder],
                   frozenset(m.flags), frozenset(m.labels), m.in_reply_to,
                   m.model_dump_json())  # fmt: skip

    def list(self, folder: str | None = None) -> list[Msg]:
        return [self._msg(m) for m in self.mb.list_messages(folder=folder, page_size=500).items]

    def page(self, size: int, cursor: str | None) -> tuple[list[Msg], str | None]:
        p = self.mb.list_messages(page_size=size, cursor=cursor)
        return [self._msg(m) for m in p.items], p.next_cursor

    def get(self, ref: str) -> Msg:
        return self._msg(self.mb.get_message(ref))

    def thread(self, thread_ref: str) -> list[Msg]:
        return [self._msg(m) for m in self.mb.get_thread(thread_ref)]

    def search(self, text: str) -> list[Msg]:
        return [self._msg(m) for m in self.mb.search_messages(text)]

    def send(self, to: list[str], subject: str, text: str) -> str:
        return str(self.mb.send(to=to, subject=subject, text=text)["message_id_header"])

    def reply(self, ref: str, text: str) -> None:
        self.mb.reply(ref, text=text)

    def move(self, ref: str, folder: str) -> None:
        self.mb.move(ref, folder)

    def flag(self, ref: str, add: list[str]) -> None:
        self.mb.set_flags(ref, add=add)

    def label(self, ref: str, add: list[str]) -> None:
        self.mb.modify_labels(ref, add=add)

    def delete(self, ref: str) -> None:
        self.mb.delete(ref)

    def changes(self, since: int) -> tuple[list[str], int]:
        ids: list[str] = []
        while True:
            feed = self.mb.list_changes(since)
            ids += [c["message_id"] for c in feed["changes"]]
            since = int(feed["next_since"])
            if not feed["has_more"]:
                return ids, since

    def error_code(self, exc: BaseException) -> str:
        return str(exc.code) if isinstance(exc, SimError) else type(exc).__name__


# Waves 1-2 append: RestDriver, GmailDriver, GraphDriver, ImapSmtpDriver, McpDriver.
DRIVERS: list[type[NeutralDriver]] = [NeutralDriver]

__all__ = ["ALL_CAPABILITIES", "DRIVERS", "ErrorCode", "MailboxDriver", "Msg", "NeutralDriver"]
