"""IMAP4rev1 listener (RFC 3501) over the agent facade. INBOUND server only.

Layering: `ImapSession` is synchronous and owns the protocol state machine; it talks to the
engine only through the authenticated `AgentMailbox` (every read audited, every write counted
against the agent budget exactly as over REST). `ImapConnection` does the asyncio I/O: line
and literal framing, continuation requests, STARTTLS and timeouts. Session calls run in a
worker thread so the (blocking) database work never stalls the event loop.

Capability profile and deviations: docs/compat/imap-smtp.md.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import contextlib
import logging
import re
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from mailsim.api.agent_errors import agent_view
from mailsim.domain.errors import SimError, UnsupportedOperation
from mailsim.domain.models import (
    CanonicalMessage,
    draft_flag_error,
    is_system_label,
    system_label_error,
)
from mailsim.engine.context import use_interface
from mailsim.mime.build import build_mime
from mailsim.mime.parse import parse_mime
from mailsim.protocols import imapmime
from mailsim.protocols.auth import AgentSession, Authenticator, AuthFailed
from mailsim.protocols.imapwire import (
    ParseError,
    QStr,
    astring,
    compress_ranges,
    in_ranges,
    is_sequence_set,
    literal,
    literal_marker,
    parse_sequence_set,
    string,
    tokenize,
    valid_tag,
)
from mailsim.protocols.tls import TLSContext

log = logging.getLogger(__name__)

LINE_LIMIT = 64 * 1024  # one command line (literals are counted separately)
MAX_LITERAL = 25 * 1024 * 1024 + 64 * 1024  # an APPENDed message (MIME limit) plus slack
MAX_COMMAND = MAX_LITERAL + 2 * LINE_LIMIT
IDLE_TIMEOUT = 30 * 60.0  # RFC 3501 autologout timer is at least 30 minutes
PREAUTH_TIMEOUT = 60.0  # idle limit before a successful login
PREAUTH_LITERAL = 8 * 1024  # literals accepted before login (credentials only)
PREAUTH_COMMAND = 16 * 1024  # a whole command (lines + literals) before login
WRITE_TIMEOUT = 60.0  # a client that stops reading is disconnected after this long
MAX_AUTH_FAILURES = 3
PAGE_SIZE = 500  # engine maximum page size for folder listings
CHANGE_LIMIT = 500  # change-feed page size used to detect mailbox changes
MAX_PATTERN = 255  # LIST/LSUB reference + pattern length
MAX_SEARCH_DEPTH = 32
RAW_CACHE_BYTES = 64 * 1024 * 1024

SYSTEM_FLAGS = {
    "\\seen": ("\\Seen", "SEEN"),
    "\\answered": ("\\Answered", "ANSWERED"),
    "\\flagged": ("\\Flagged", "FLAGGED"),
    "\\deleted": ("\\Deleted", "DELETED"),
    "\\draft": ("\\Draft", "DRAFT"),
}
ENGINE_TO_IMAP = {engine: imap for imap, engine in SYSTEM_FLAGS.values()}
FLAG_ORDER = ["\\Answered", "\\Flagged", "\\Deleted", "\\Seen", "\\Draft"]
SPECIAL_USE = {"sent": "\\Sent", "drafts": "\\Drafts", "trash": "\\Trash", "archive": "\\Archive"}
_KEYWORD_RE = re.compile(r"^[!#$&'+\-.0-9:;<=>?@A-Z^_`a-z|~/]+$")
_SYSTEM_LABELS = {"INBOX", "SENT", "DRAFTS", "TRASH", "ARCHIVE"}
_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
_DATE_RE = re.compile(r"^(\d{1,2})-([A-Za-z]{3})-(\d{4})$")
_FETCH_SECTION_RE = re.compile(
    r"^(BODY|BODY\.PEEK)\[([^\]]*)\](?:<(\d{1,10})(?:\.(\d{1,10}))?>)?$", re.IGNORECASE
)
_ERROR_CODES = {
    "NOT_FOUND": "NONEXISTENT",
    "PERMISSION_DENIED": "NOPERM",
    "RUN_NOT_ACTIVE": "UNAVAILABLE",
    "SIMULATION_LIMIT_EXCEEDED": "LIMIT",
    "RATE_LIMITED": "LIMIT",
    "INVALID_REQUEST": "CANNOT",
    "UNSUPPORTED_OPERATION": "CANNOT",
    "CONFLICT": "INUSE",
}


class Disconnect(Exception):
    """End the connection after sending `lines` (BYE)."""


class WriteTimeout(Exception):
    """The client stopped reading; the connection is aborted without a goodbye."""


# --------------------------------------------------------------------------- helpers
def is_keyword(name: str) -> bool:
    return bool(_KEYWORD_RE.match(name))


def wildcard_match(pattern: str, name: str, *, fold: bool = False) -> bool:
    """IMAP LIST matching (`*` any, `%` any except the "/" delimiter) without regexes:
    O(len(pattern) * len(name)) dynamic programming, so hostile patterns cannot backtrack."""
    if fold:
        pattern, name = pattern.upper(), name.upper()
    row = [True] + [False] * len(name)  # row[j]: pattern[:i] matches name[:j]
    for ch in pattern:
        nxt = [False] * (len(name) + 1)
        if ch in "*%":
            nxt[0] = row[0]
            for j in range(1, len(name) + 1):
                nxt[j] = row[j] or (nxt[j - 1] and (ch == "*" or name[j - 1] != "/"))
        else:
            for j in range(1, len(name) + 1):
                nxt[j] = row[j - 1] and name[j - 1] == ch
        row = nxt
    return row[-1]


def _has_keyword(labels: list[str], word: str) -> bool:
    return word.lower() in {k.lower() for k in labels}


def user_labels(msg: CanonicalMessage) -> list[str]:
    """CanonicalMessage.labels = [folder system label] + user labels + [UNREAD]: keep users'."""
    labels = list(msg.labels)
    if labels and labels[0] in _SYSTEM_LABELS:
        labels = labels[1:]
    if labels and labels[-1] == "UNREAD" and "SEEN" not in msg.flags and not msg.is_draft:
        labels = labels[:-1]
    return labels


def imap_flags(msg: CanonicalMessage) -> list[str]:
    system = {ENGINE_TO_IMAP[f] for f in msg.flags if f in ENGINE_TO_IMAP}
    return [f for f in FLAG_ORDER if f in system] + [k for k in user_labels(msg) if is_keyword(k)]


def internal_date(msg: CanonicalMessage) -> datetime:
    return (msg.received_at or msg.created_at).astimezone(UTC)


def format_internal_date(when: datetime) -> str:
    return f"{when.day:2d}-{_MONTHS[when.month - 1]}-{when.year:04d} {when:%H:%M:%S} +0000"


def parse_search_date(tok: object) -> date:
    m = _DATE_RE.match(astring(tok))
    months = [mo.lower() for mo in _MONTHS]
    if m is None or m.group(2).lower() not in months:
        raise ParseError(f"invalid date {astring(tok)!r}")
    try:
        return date(int(m.group(3)), months.index(m.group(2).lower()) + 1, int(m.group(1)))
    except ValueError:
        raise ParseError(f"invalid date {astring(tok)!r}") from None


def _flag_list(flags: list[str]) -> bytes:
    return b"(" + " ".join(flags).encode() + b")"


def _is_atom(tok: object) -> bool:
    return isinstance(tok, str) and not isinstance(tok, QStr)


def _one_line(text: str) -> str:
    return " ".join(text.split())[:300]


@dataclass
class Selected:
    name: str  # IMAP mailbox name (as LISTed)
    engine_folder: str
    read_only: bool
    uid_validity: int
    uids: list[int] = field(default_factory=list)  # seq n -> uids[n-1]
    flags: dict[int, list[str]] = field(default_factory=dict)
    listing: dict[int, CanonicalMessage] | None = None  # cache until the change feed moves


@dataclass
class Reply:
    lines: list[bytes]
    close: bool = False
    starttls: bool = False
    continuation: bytes | None = None
    pending: Callable[[bytes], Reply] | None = None


@dataclass(frozen=True)
class FetchItem:
    name: str  # UID, FLAGS, ..., BODY (section fetch), BODYSTRUCTURE, ...
    section: imapmime.Section | None = None
    partial: tuple[int, int | None] | None = None
    peek: bool = False
    label: str = ""  # response name, e.g. "BODY[HEADER.FIELDS (FROM)]"


# --------------------------------------------------------------------------- session
class ImapSession:
    """One client's protocol state machine (not-authenticated / authenticated / selected)."""

    def __init__(self, authenticator: Authenticator, *, tls_available: bool = False,
                 secured: bool = False) -> None:  # fmt: skip
        self.auth = authenticator
        self.tls_available = tls_available
        self.secured = secured
        self.agent: AgentSession | None = None
        self.selected: Selected | None = None
        self.failures = 0
        self._raw_cache: OrderedDict[str, tuple[bytes, imapmime.Part]] = OrderedDict()
        self._raw_bytes = 0
        self._folder_cache: list[dict[str, Any]] | None = None
        self._seq = 0  # change-feed position the cached views reflect
        self._flush: Callable[[list[bytes]], None] | None = None

    # ----------------------------------------------------------------- framing
    @property
    def state(self) -> str:
        if self.agent is None:
            return "not_authenticated"
        return "selected" if self.selected is not None else "authenticated"

    def capabilities(self) -> list[str]:
        caps = ["IMAP4rev1", "LITERAL+", "UIDPLUS", "MOVE", "UNSELECT", "SPECIAL-USE",
                "NAMESPACE", "ID", "OBJECTID", "CHILDREN"]  # fmt: skip
        if self.agent is None:
            caps += ["SASL-IR", "AUTH=PLAIN"]
            if self.tls_available and not self.secured:
                caps.append("STARTTLS")
        return caps

    def greeting(self) -> bytes:
        caps = " ".join(self.capabilities())
        return f"* OK [CAPABILITY {caps}] IMAP4rev1 ready\r\n".encode()

    def execute(self, data: bytes, flush: Callable[[list[bytes]], None] | None = None) -> Reply:
        """Run one complete command (literals inline as received). `flush` streams untagged
        responses early (large FETCH results are sent per message)."""
        self._flush = flush
        if self.agent is not None:
            try:
                valid = self.auth.still_valid(self.agent)
            except Exception:
                log.exception("IMAP credential re-check failed")
                valid = False
            if not valid:
                self.agent, self.selected = None, None
                return Reply([b"* BYE [UNAVAILABLE] credentials revoked or expired\r\n"],
                             close=True)  # fmt: skip
        try:
            tokens = tokenize(data)
        except ParseError as exc:
            head = data.split(b" ", 1)[0].decode("ascii", "replace")
            tag = head if valid_tag(head) else "*"
            return Reply([f"{tag} BAD {_one_line(str(exc))}\r\n".encode()])
        if len(tokens) < 2 or not valid_tag(tokens[0]) or not _is_atom(tokens[1]):
            tag = str(tokens[0]) if tokens and valid_tag(tokens[0]) else "*"
            return Reply([f"{tag} BAD missing command\r\n".encode()])
        tag, name, args = str(tokens[0]), str(tokens[1]).upper(), tokens[2:]
        out: list[bytes] = []
        try:
            reply = self._dispatch(tag, name, args, out)
        except ParseError as exc:
            return Reply([*out, f"{tag} BAD {_one_line(str(exc))}\r\n".encode()])
        except imapmime.SectionError as exc:
            return Reply([*out, f"{tag} BAD {_one_line(str(exc))}\r\n".encode()])
        except Disconnect as exc:
            return Reply([*out, f"* BYE {_one_line(str(exc))}\r\n".encode()], close=True)
        except WriteTimeout:
            return Reply([], close=True)
        except SimError as exc:
            code = _ERROR_CODES.get(str(exc.code), "SERVERBUG")
            agent_code, message, _ = agent_view(str(exc.code), exc.message)
            line = f"{tag} NO [{code}] {agent_code}: {_one_line(message)}\r\n"
            return Reply([*out, line.encode()])
        except Exception:
            log.exception("IMAP command %s failed", name)
            return Reply([*out, f"{tag} NO [SERVERBUG] internal error\r\n".encode()])
        if isinstance(reply, Reply):
            return Reply([*out, *reply.lines], reply.close, reply.starttls, reply.continuation,
                         reply.pending)  # fmt: skip
        return Reply([*out, f"{tag} {reply}\r\n".encode()])

    def _dispatch(self, tag: str, name: str, args: list[Any], out: list[bytes]) -> str | Reply:
        any_state = {"CAPABILITY", "NOOP", "LOGOUT", "ID"}
        not_auth = {"LOGIN", "AUTHENTICATE", "STARTTLS"}
        selected = {"CHECK", "CLOSE", "UNSELECT", "EXPUNGE", "SEARCH", "FETCH", "STORE", "COPY",
                    "MOVE", "UID"}  # fmt: skip
        auth = {"SELECT", "EXAMINE", "LIST", "LSUB", "STATUS", "APPEND", "NAMESPACE", "CREATE",
                "DELETE", "RENAME", "SUBSCRIBE", "UNSUBSCRIBE"}  # fmt: skip
        state = self.state
        if name in any_state:
            pass
        elif name in not_auth:
            if state != "not_authenticated":
                return "BAD already authenticated"
        elif name in auth:
            if state == "not_authenticated":
                return "BAD authenticate first"
        elif name in selected:
            if state != "selected":
                return "BAD no mailbox selected"
        else:
            return f"BAD unknown command {name[:40]}"
        handler: Callable[[str, list[Any], list[bytes]], str | Reply] = getattr(
            self, "cmd_" + name.lower()
        )
        return handler(tag, args, out)

    @staticmethod
    def _arity(args: list[Any], n: int, usage: str) -> None:
        if len(args) != n:
            raise ParseError(f"usage: {usage}")

    @property
    def _mb(self) -> Any:
        assert self.agent is not None
        return self.agent.mailbox

    # ----------------------------------------------------------------- any state
    def cmd_capability(self, tag: str, args: list[Any], out: list[bytes]) -> str:
        self._arity(args, 0, "CAPABILITY")
        out.append(f"* CAPABILITY {' '.join(self.capabilities())}\r\n".encode())
        return "OK CAPABILITY completed"

    def cmd_noop(self, tag: str, args: list[Any], out: list[bytes]) -> str:
        self._arity(args, 0, "NOOP")
        if self.selected is not None:
            self._sync(out, expunges=True)
        return "OK NOOP completed"

    def cmd_check(self, tag: str, args: list[Any], out: list[bytes]) -> str:
        self._arity(args, 0, "CHECK")
        self._sync(out, expunges=True)
        return "OK CHECK completed"

    def cmd_logout(self, tag: str, args: list[Any], out: list[bytes]) -> Reply:
        self._arity(args, 0, "LOGOUT")
        return Reply([b"* BYE logging out\r\n", f"{tag} OK LOGOUT completed\r\n".encode()],
                     close=True)  # fmt: skip

    def cmd_id(self, tag: str, args: list[Any], out: list[bytes]) -> str:
        self._arity(args, 1, "ID (params) | ID NIL")
        out.append(b'* ID ("name" "IMAP" "vendor" "IMAP")\r\n')
        return "OK ID completed"

    # ----------------------------------------------------------------- authentication
    def cmd_starttls(self, tag: str, args: list[Any], out: list[bytes]) -> str | Reply:
        self._arity(args, 0, "STARTTLS")
        if not self.tls_available or self.secured:
            return "BAD STARTTLS not available"
        return Reply([f"{tag} OK Begin TLS negotiation now\r\n".encode()], starttls=True)

    def cmd_login(self, tag: str, args: list[Any], out: list[bytes]) -> str | Reply:
        self._arity(args, 2, "LOGIN user password")
        return self._login(tag, astring(args[0]), astring(args[1]))

    def cmd_authenticate(self, tag: str, args: list[Any], out: list[bytes]) -> str | Reply:
        if not args or len(args) > 2:
            raise ParseError("usage: AUTHENTICATE mechanism [initial-response]")
        if astring(args[0]).upper() != "PLAIN":
            return "NO [CANNOT] unsupported authentication mechanism"
        if len(args) == 2:  # SASL-IR
            initial = astring(args[1])
            return self._auth_plain(tag, b"" if initial == "=" else initial.encode())
        return Reply(
            [], continuation=b"+ \r\n", pending=lambda line: self._continue_plain(tag, line)
        )

    def _continue_plain(self, tag: str, line: bytes) -> Reply:
        reply = self._auth_plain(tag, line.strip())
        return reply if isinstance(reply, Reply) else Reply([f"{tag} {reply}\r\n".encode()])

    def _auth_plain(self, tag: str, b64: bytes) -> str | Reply:
        if b64 == b"*":
            return "BAD AUTHENTICATE cancelled"
        try:
            authzid, authcid, password = base64.b64decode(b64, validate=True).split(b"\0")
        except (binascii.Error, ValueError):
            return "BAD malformed SASL PLAIN response"
        if authzid and authzid != authcid:
            return self._failed(tag, "NO [AUTHORIZATIONFAILED] authorization identity mismatch")
        return self._login(tag, authcid.decode("utf-8", "replace"),
                           password.decode("utf-8", "replace"))  # fmt: skip

    def _login(self, tag: str, username: str, password: str) -> str | Reply:
        try:
            self.agent = self.auth.login(username, password)
        except AuthFailed as exc:
            log.info("IMAP login failed: %s", exc.reason)
            if exc.code == "RUN_NOT_ACTIVE":
                return self._failed(
                    tag, "NO [UNAVAILABLE] RUN_NOT_ACTIVE: mailbox is not available"
                )
            if exc.code is not None:
                return self._failed(tag, f"NO [AUTHORIZATIONFAILED] {_one_line(exc.reason)}")
            return self._failed(tag, "NO [AUTHENTICATIONFAILED] Authentication failed")
        self._seq = int(self._mb.list_changes(0, limit=1)["latest_sequence"])
        caps = " ".join(self.capabilities())
        return f"OK [CAPABILITY {caps}] authenticated as {self.agent.address}"

    def _failed(self, tag: str, status: str) -> str | Reply:
        self.failures += 1
        if self.failures >= MAX_AUTH_FAILURES:
            return Reply([f"{tag} {status}\r\n".encode(), b"* BYE too many failed attempts\r\n"],
                         close=True)  # fmt: skip
        return status

    # ----------------------------------------------------------------- folders
    def _folders(self) -> list[dict[str, Any]]:
        if self._folder_cache is None:
            self._folder_cache = list(self._mb.list_folders())
        return self._folder_cache

    def _poll(self) -> bool:
        """One change-feed read: True when this mailbox changed since the cached views.
        A run reset ends a selected session (its UIDs are no longer valid)."""
        feed = self._mb.list_changes(self._seq, limit=CHANGE_LIMIT)
        self._seq = int(feed["latest_sequence"])
        if feed["reset_since"]:
            self._folder_cache = None
            if self.selected is not None:
                self.selected = None
                raise Disconnect("[UNAVAILABLE] UIDVALIDITY changed; reconnect")
            return True
        if feed["changes"]:
            self._folder_cache = None
            return True
        return False

    def _fresh_folders(self) -> list[dict[str, Any]]:
        self._poll()
        return self._folders()

    def _folder(self, name: str) -> dict[str, Any] | None:
        for f in self._folders():
            if name == f["name"] or name.upper() == str(f["name"]).upper():
                return f
        return None

    def _messages(self, folder: str) -> list[CanonicalMessage]:
        items: list[CanonicalMessage] = []
        cursor: str | None = None
        while True:
            page = self._mb.list_messages(folder=folder, page_size=PAGE_SIZE, cursor=cursor)
            items += page.items
            cursor = page.next_cursor
            if cursor is None:
                break
        return sorted((m for m in items if m.imap_uid is not None), key=lambda m: m.imap_uid or 0)

    def _attrs(self, f: dict[str, Any]) -> str:
        attrs = ["\\HasNoChildren"]
        if f.get("system_type") in SPECIAL_USE:
            attrs.append(SPECIAL_USE[f["system_type"]])
        return " ".join(attrs)

    def cmd_list(self, tag: str, args: list[Any], out: list[bytes]) -> str:
        return self._list("LIST", args, out)

    def cmd_lsub(self, tag: str, args: list[Any], out: list[bytes]) -> str:
        return self._list("LSUB", args, out)

    def _list(self, kind: str, args: list[Any], out: list[bytes]) -> str:
        self._arity(args, 2, f"{kind} reference pattern")
        reference, pattern = astring(args[0]), astring(args[1])
        if pattern == "":
            if kind == "LIST":
                out.append(b'* LIST (\\Noselect) "/" ""\r\n')
            return f"OK {kind} completed"
        full = reference + pattern
        if len(full) > MAX_PATTERN:
            raise ParseError(f"{kind} pattern too long (max {MAX_PATTERN})")
        for f in self._fresh_folders():
            name = str(f["name"])
            if wildcard_match(full, name, fold=name.upper() == "INBOX"):
                out.append(f'* {kind} ({self._attrs(f)}) "/" '.encode() + string(name) + b"\r\n")
        return f"OK {kind} completed"

    def cmd_namespace(self, tag: str, args: list[Any], out: list[bytes]) -> str:
        self._arity(args, 0, "NAMESPACE")
        out.append(b'* NAMESPACE (("" "/")) NIL NIL\r\n')
        return "OK NAMESPACE completed"

    def _unsupported(self, what: str) -> str:
        return f"NO [CANNOT] UNSUPPORTED_OPERATION: {what}"

    def cmd_create(self, tag: str, args: list[Any], out: list[bytes]) -> str:
        return self._unsupported("only the system folders exist; CREATE is not supported")

    def cmd_delete(self, tag: str, args: list[Any], out: list[bytes]) -> str:
        return self._unsupported("system folders cannot be deleted")

    def cmd_rename(self, tag: str, args: list[Any], out: list[bytes]) -> str:
        return self._unsupported("system folders cannot be renamed")

    def cmd_subscribe(self, tag: str, args: list[Any], out: list[bytes]) -> str:
        self._arity(args, 1, "SUBSCRIBE mailbox")
        if self._folder(astring(args[0])) is None:
            return "NO [NONEXISTENT] no such mailbox"
        return "OK SUBSCRIBE completed (all folders are always subscribed)"

    def cmd_unsubscribe(self, tag: str, args: list[Any], out: list[bytes]) -> str:
        self._arity(args, 1, "UNSUBSCRIBE mailbox")
        return "OK UNSUBSCRIBE completed (folders stay listed in LSUB)"

    def cmd_status(self, tag: str, args: list[Any], out: list[bytes]) -> str:
        if len(args) != 2 or not isinstance(args[1], list) or not args[1]:
            raise ParseError("usage: STATUS mailbox (items)")
        name = astring(args[0])
        self._poll()
        f = self._folder(name)
        if f is None:
            return "NO [NONEXISTENT] no such mailbox"
        msgs = self._messages(str(f["name"]))
        values = {
            "MESSAGES": len(msgs),
            "RECENT": 0,
            "UIDNEXT": f["uid_next"],
            "UIDVALIDITY": f["uid_validity"],
            "UNSEEN": sum(1 for m in msgs if "SEEN" not in m.flags),
        }
        parts = []
        for item in args[1]:
            key = astring(item).upper()
            if key not in values:
                raise ParseError(f"unknown STATUS item {key[:40]}")
            parts.append(f"{key} {values[key]}")
        out.append(b"* STATUS " + string(str(f["name"])) + f" ({' '.join(parts)})\r\n".encode())
        return "OK STATUS completed"

    # ----------------------------------------------------------------- select
    def cmd_select(self, tag: str, args: list[Any], out: list[bytes]) -> str:
        return self._select(args, out, read_only=False)

    def cmd_examine(self, tag: str, args: list[Any], out: list[bytes]) -> str:
        return self._select(args, out, read_only=True)

    def _select(self, args: list[Any], out: list[bytes], *, read_only: bool) -> str:
        self._arity(args, 1, "SELECT mailbox")
        self.selected = None  # a failed SELECT leaves no mailbox selected (RFC 3501 6.3.1)
        self._poll()
        f = self._folder(astring(args[0]))
        if f is None:
            return "NO [NONEXISTENT] no such mailbox"
        msgs = self._messages(str(f["name"]))
        sel = Selected(str(f["name"]), str(f["name"]), read_only, int(f["uid_validity"]))
        sel.uids = [int(m.imap_uid or 0) for m in msgs]
        sel.flags = {int(m.imap_uid or 0): imap_flags(m) for m in msgs}
        sel.listing = {int(m.imap_uid or 0): m for m in msgs}
        keywords = sorted({k for flags in sel.flags.values() for k in flags if k[0] != "\\"})
        out.append(b"* FLAGS " + _flag_list(FLAG_ORDER + keywords) + b"\r\n")
        out.append(f"* {len(msgs)} EXISTS\r\n* 0 RECENT\r\n".encode())
        unseen = next((i + 1 for i, m in enumerate(msgs) if "SEEN" not in m.flags), None)
        if unseen is not None:
            out.append(f"* OK [UNSEEN {unseen}] first unseen message\r\n".encode())
        permanent = "()" if read_only else "(" + " ".join([*FLAG_ORDER, "\\*"]) + ")"
        out.append(f"* OK [PERMANENTFLAGS {permanent}] flags permitted\r\n".encode())
        out.append(f"* OK [UIDVALIDITY {sel.uid_validity}] UIDs valid\r\n".encode())
        out.append(f"* OK [UIDNEXT {f['uid_next']}] predicted next UID\r\n".encode())
        out.append(f"* OK [MAILBOXID ({f['id']})] mailbox id\r\n".encode())
        self.selected = sel
        mode = "READ-ONLY" if read_only else "READ-WRITE"
        return f"OK [{mode}] {'EXAMINE' if read_only else 'SELECT'} completed"

    def cmd_close(self, tag: str, args: list[Any], out: list[bytes]) -> str:
        self._arity(args, 0, "CLOSE")
        sel = self.selected
        assert sel is not None
        if not sel.read_only and any(
            "DELETED" in m.flags for m in self._sync([], expunges=False).values()
        ):
            self._mb.expunge(sel.engine_folder)
        self.selected = None
        return "OK CLOSE completed"

    def cmd_unselect(self, tag: str, args: list[Any], out: list[bytes]) -> str:
        self._arity(args, 0, "UNSELECT")
        self.selected = None
        return "OK UNSELECT completed"

    # ----------------------------------------------------------------- mailbox view
    def _sync(self, out: list[bytes], *, expunges: bool,
              quiet: frozenset[int] = frozenset()) -> dict[int, CanonicalMessage]:  # fmt: skip
        """Refresh the selected folder; report changes the client may now learn about.

        EXPUNGE responses are only sent when `expunges` (never during non-UID FETCH / STORE /
        SEARCH, RFC 3501 7.4.1); vanished messages stay as ghosts in the sequence map until
        then. New messages always produce EXISTS; flag changes made elsewhere produce FETCH.
        """
        sel = self.selected
        assert sel is not None
        if self._poll() or sel.listing is None:
            sel.listing = {int(m.imap_uid or 0): m for m in self._messages(sel.engine_folder)}
        by_uid = sel.listing
        if expunges:
            for idx in range(len(sel.uids) - 1, -1, -1):
                if sel.uids[idx] not in by_uid:
                    out.append(f"* {idx + 1} EXPUNGE\r\n".encode())
                    sel.flags.pop(sel.uids.pop(idx), None)
        known = set(sel.uids)
        new = sorted(u for u in by_uid if u not in known)
        for idx, uid in enumerate(sel.uids):
            msg = by_uid.get(uid)
            if msg is None:
                continue
            flags = imap_flags(msg)
            if sel.flags.get(uid) != flags and uid not in quiet:
                out.append(f"* {idx + 1} FETCH (UID {uid} FLAGS ".encode() + _flag_list(flags)
                           + b")\r\n")  # fmt: skip
            sel.flags[uid] = flags
        if new:
            sel.uids += new
            sel.flags.update({u: imap_flags(by_uid[u]) for u in new})
            out.append(f"* {len(sel.uids)} EXISTS\r\n".encode())
        return by_uid

    def _targets(self, token: object, uid_mode: bool,
                 by_uid: dict[int, CanonicalMessage]) -> list[tuple[int, CanonicalMessage]]:  # fmt: skip
        sel = self.selected
        assert sel is not None
        if uid_mode:
            ranges = parse_sequence_set(token, max(sel.uids, default=0))
            return [(i + 1, by_uid[u]) for i, u in enumerate(sel.uids)
                    if u in by_uid and in_ranges(u, ranges)]  # fmt: skip
        count = len(sel.uids)
        ranges = parse_sequence_set(token, count)
        if count == 0:
            return []
        if any(hi > count for _, hi in ranges):
            raise ParseError("invalid message sequence number")
        return [(i + 1, by_uid[u]) for i, u in enumerate(sel.uids)
                if u in by_uid and in_ranges(i + 1, ranges)]  # fmt: skip

    def _require_writable(self) -> None:
        assert self.selected is not None
        if self.selected.read_only:
            raise _ReadOnly()

    # ----------------------------------------------------------------- UID
    def cmd_uid(self, tag: str, args: list[Any], out: list[bytes]) -> str:
        if not args or not isinstance(args[0], str):
            raise ParseError("usage: UID FETCH|SEARCH|STORE|COPY|MOVE|EXPUNGE ...")
        sub = args[0].upper()
        handlers: dict[str, Callable[[list[Any], list[bytes], bool], str]] = {
            "FETCH": self._fetch, "SEARCH": self._search, "STORE": self._store,
            "COPY": self._copy, "MOVE": self._move, "EXPUNGE": self._uid_expunge,
        }  # fmt: skip
        if sub not in handlers:
            raise ParseError(f"unknown UID command {sub[:40]}")
        return self._guarded(lambda: handlers[sub](args[1:], out, True))

    def _guarded(self, fn: Callable[[], str]) -> str:
        try:
            return fn()
        except _ReadOnly:
            return "NO [READ-ONLY] mailbox is selected read-only"

    def cmd_fetch(self, tag: str, args: list[Any], out: list[bytes]) -> str:
        return self._fetch(args, out, False)

    def cmd_search(self, tag: str, args: list[Any], out: list[bytes]) -> str:
        return self._search(args, out, False)

    def cmd_store(self, tag: str, args: list[Any], out: list[bytes]) -> str:
        return self._guarded(lambda: self._store(args, out, False))

    def cmd_copy(self, tag: str, args: list[Any], out: list[bytes]) -> str:
        return self._copy(args, out, False)

    def cmd_move(self, tag: str, args: list[Any], out: list[bytes]) -> str:
        return self._guarded(lambda: self._move(args, out, False))

    def cmd_expunge(self, tag: str, args: list[Any], out: list[bytes]) -> str:
        self._arity(args, 0, "EXPUNGE")
        return self._guarded(lambda: self._expunge(out))

    # ----------------------------------------------------------------- FETCH
    def _raw(self, msg: CanonicalMessage) -> tuple[bytes, imapmime.Part]:
        key = f"{msg.id}:{msg.raw_blob_ref}"
        hit = self._raw_cache.get(key)
        if hit is not None:
            self._raw_cache.move_to_end(key)
            return hit
        raw = self._mb.get_raw(msg.id) if msg.raw_blob_ref else None
        if raw is None:  # no stored MIME: rebuild from the canonical fields (no attachments)
            raw = build_mime(message_id=msg.message_id_header, from_=msg.from_, to=msg.to,
                             cc=msg.cc, subject=msg.subject, date=msg.created_at,
                             text=msg.body_text, html=msg.body_html, in_reply_to=msg.in_reply_to,
                             references=msg.references or None)  # fmt: skip
        entry = (raw, imapmime.parse(raw))
        self._raw_cache[key] = entry
        self._raw_bytes += len(raw)
        while self._raw_bytes > RAW_CACHE_BYTES and len(self._raw_cache) > 1:
            _, (old, _part) = self._raw_cache.popitem(last=False)
            self._raw_bytes -= len(old)
        return entry

    def _fetch_items(self, token: object) -> list[FetchItem]:
        macros = {
            "ALL": ["FLAGS", "INTERNALDATE", "RFC822.SIZE", "ENVELOPE"],
            "FAST": ["FLAGS", "INTERNALDATE", "RFC822.SIZE"],
            "FULL": ["FLAGS", "INTERNALDATE", "RFC822.SIZE", "ENVELOPE", "BODY"],
        }
        if isinstance(token, list):
            names = [astring(t) for t in token]
        elif isinstance(token, str) and not isinstance(token, QStr):
            names = macros.get(token.upper(), [token])
        else:
            raise ParseError("invalid FETCH attribute list")
        if not names:
            raise ParseError("empty FETCH attribute list")
        return [self._fetch_item(n) for n in names]

    def _fetch_item(self, name: str) -> FetchItem:
        simple = {"UID", "FLAGS", "INTERNALDATE", "RFC822.SIZE", "ENVELOPE", "BODYSTRUCTURE",
                  "BODY", "RFC822", "RFC822.HEADER", "RFC822.TEXT", "EMAILID", "THREADID"}  # fmt: skip
        upper = name.upper()
        if upper in simple:
            return FetchItem(upper)
        m = _FETCH_SECTION_RE.match(name)
        if m is None:
            raise ParseError(f"unknown FETCH attribute {name[:60]}")
        spec, _, rest = m.group(2).partition(" ")
        fields: list[str] | None = None
        if rest:
            parsed = tokenize(rest.encode())
            if len(parsed) != 1 or not isinstance(parsed[0], list) or not parsed[0]:
                raise ParseError("invalid header field list")
            fields = [astring(t).upper() for t in parsed[0]]
        section = imapmime.parse_section(spec.upper(), fields)
        partial = None
        if m.group(3) is not None:
            partial = (int(m.group(3)), int(m.group(4)) if m.group(4) is not None else None)
        text = ".".join(str(n) for n in section.parts)
        if section.text:
            text = f"{text}.{section.text}" if text else section.text
        if fields is not None:
            text += " (" + " ".join(fields) + ")"
        return FetchItem("BODY", section, partial, m.group(1).upper() == "BODY.PEEK",
                         f"BODY[{text}]")  # fmt: skip

    def _render(self, item: FetchItem, msg: CanonicalMessage) -> bytes:
        name = item.name
        if name == "UID":
            return f"UID {msg.imap_uid}".encode()
        if name == "FLAGS":
            return b"FLAGS " + _flag_list(imap_flags(msg))
        if name == "INTERNALDATE":
            return f'INTERNALDATE "{format_internal_date(internal_date(msg))}"'.encode()
        if name == "EMAILID":
            return f"EMAILID ({msg.id})".encode()
        if name == "THREADID":
            return f"THREADID ({msg.thread_id})".encode()
        raw, root = self._raw(msg)
        if name == "RFC822.SIZE":
            return f"RFC822.SIZE {len(raw)}".encode()
        if name == "ENVELOPE":
            return b"ENVELOPE " + imapmime.envelope(root)
        if name in ("BODYSTRUCTURE", "BODY") and item.section is None:
            return name.encode() + b" " + imapmime.bodystructure(root, extended=name != "BODY")
        if name == "RFC822":
            return b"RFC822 " + literal(raw)
        if name == "RFC822.HEADER":
            return b"RFC822.HEADER " + literal(root.header)
        if name == "RFC822.TEXT":
            return b"RFC822.TEXT " + literal(root.body)
        assert item.section is not None
        data = imapmime.section_bytes(raw, root, item.section)
        label = item.label
        if item.partial is not None:
            start, length = item.partial
            data = data[start:] if length is None else data[start : start + length]
            label += f"<{start}>"
        return label.encode() + b" " + literal(data)

    def _fetch(self, args: list[Any], out: list[bytes], uid_mode: bool) -> str:
        if len(args) == 3:
            raise ParseError("UNSUPPORTED_OPERATION: FETCH modifiers (CONDSTORE) are not supported")
        if len(args) != 2:
            raise ParseError("usage: FETCH sequence-set items")
        items = self._fetch_items(args[1])
        if uid_mode and not any(i.name == "UID" for i in items):
            items.insert(0, FetchItem("UID"))
        sets_seen = any(i.name in ("RFC822", "RFC822.TEXT") or (i.section is not None and not i.peek)
                        for i in items)  # fmt: skip
        sel = self.selected
        assert sel is not None
        by_uid = self._sync(out, expunges=uid_mode)
        for seq, msg in self._targets(args[0], uid_mode, by_uid):
            shown = items
            if sets_seen and not sel.read_only and "SEEN" not in msg.flags:
                # RFC 3501 6.4.5: fetching the body sets \Seen (a read, never budgeted)
                self._mb.mark_seen(msg.id)
                labels = msg.labels[:-1] if msg.labels[-1:] == ["UNREAD"] else msg.labels
                msg = msg.model_copy(update={"flags": sorted({*msg.flags, "SEEN"}),
                                             "labels": labels})  # fmt: skip
                uid = int(msg.imap_uid or 0)
                sel.flags[uid] = imap_flags(msg)
                if not any(i.name == "FLAGS" for i in items):
                    shown = [*items, FetchItem("FLAGS")]
            body = b" ".join(self._render(i, msg) for i in shown)
            out.append(f"* {seq} FETCH (".encode() + body + b")\r\n")
            self._stream(out)
        return f"OK {'UID ' if uid_mode else ''}FETCH completed"

    def _stream(self, out: list[bytes]) -> None:
        if self._flush is not None and out:
            self._flush(list(out))
            out.clear()

    # ----------------------------------------------------------------- STORE
    def _store(self, args: list[Any], out: list[bytes], uid_mode: bool) -> str:
        if len(args) < 3 or not isinstance(args[1], str):
            raise ParseError("usage: STORE sequence-set [+|-]FLAGS[.SILENT] (flags)")
        self._require_writable()
        op = args[1].upper()
        silent = op.endswith(".SILENT")
        op = op.removesuffix(".SILENT")
        if op not in ("FLAGS", "+FLAGS", "-FLAGS"):
            raise ParseError(f"unknown STORE item {op[:40]}")
        raw_flags = args[2] if len(args) == 3 and isinstance(args[2], list) else args[2:]
        system: set[str] = set()
        keywords: set[str] = set()
        for tok in raw_flags:
            flag = astring(tok)
            if flag.lower() in SYSTEM_FLAGS:
                system.add(SYSTEM_FLAGS[flag.lower()][1])
            elif flag.lower() == "\\recent":
                continue
            elif flag.startswith("\\") or not is_keyword(flag):
                raise ParseError(f"invalid flag {flag[:40]}")
            else:
                keywords.add(flag)
        by_uid = self._sync(out, expunges=uid_mode)
        targets = self._targets(args[0], uid_mode, by_uid)
        # refused (and recorded) before any write: a keyword "Sent" is no system flag, and
        # \Draft marks real drafts only (FLAGS without it keeps a draft's)
        first = targets[0][1].id if targets else None
        self._mb.check_labels(first, add=sorted(keywords) if op != "-FLAGS" else (),
                              remove=sorted(keywords) if op == "-FLAGS" else ())  # fmt: skip
        if "DRAFT" in system:
            for _, msg in targets:
                self._mb.check_draft_flag(msg, add=() if op == "-FLAGS" else ("DRAFT",),
                                          remove=("DRAFT",) if op == "-FLAGS" else ())  # fmt: skip
        wanted = {k.lower() for k in keywords}
        for _seq, msg in targets:
            cur_sys, cur_kw = set(msg.flags), set(user_labels(msg))
            have = {k.lower() for k in cur_kw}
            add_k = {k for k in keywords if k.lower() not in have}  # keywords match ignoring case
            if op == "+FLAGS":
                add_s, rm_s, rm_k = system - cur_sys, set(), set()
            elif op == "-FLAGS":
                add_s, rm_s, add_k = set(), system & cur_sys, set()
                rm_k = {k for k in cur_kw if k.lower() in wanted}
            else:
                add_s, rm_s = system - cur_sys, (cur_sys & set(ENGINE_TO_IMAP)) - system
                rm_s.discard("DRAFT")  # a draft stays a draft (checked above)
                # stored system-named labels (seeded mail) are no keywords: left alone
                rm_k = {k for k in cur_kw if is_keyword(k) and k.lower() not in wanted
                        and not is_system_label(k)}  # fmt: skip
            if add_s or rm_s:
                self._mb.set_flags(msg.id, add=sorted(add_s), remove=sorted(rm_s))
            if add_k or rm_k:
                self._mb.modify_labels(msg.id, add=sorted(add_k), remove=sorted(rm_k))
        touched = frozenset(int(m.imap_uid or 0) for _, m in targets)
        fresh = self._sync(out, expunges=uid_mode, quiet=touched)
        sel = self.selected
        assert sel is not None
        if not silent:
            position = {u: i + 1 for i, u in enumerate(sel.uids)}  # after any EXPUNGE just sent
            for _seq, msg in targets:
                now = fresh.get(int(msg.imap_uid or 0))
                if now is None or int(msg.imap_uid or 0) not in position:
                    continue  # removed by another session between the write and this echo
                uid = f"UID {now.imap_uid} " if uid_mode else ""
                seq = position[int(msg.imap_uid or 0)]
                out.append(f"* {seq} FETCH ({uid}FLAGS ".encode() + _flag_list(imap_flags(now))
                           + b")\r\n")  # fmt: skip
        return f"OK {'UID ' if uid_mode else ''}STORE completed"

    # ----------------------------------------------------------------- COPY / MOVE
    def _destination(self, args: list[Any], verb: str) -> tuple[object, dict[str, Any] | None]:
        if len(args) != 2:
            raise ParseError(f"usage: {verb} sequence-set mailbox")
        return args[0], self._folder(astring(args[1]))

    def _new_uids(self, folder: dict[str, Any], ids: list[str]) -> list[int]:
        by_id = {m.id: int(m.imap_uid or 0) for m in self._messages(str(folder["name"]))}
        return [by_id[i] for i in ids if i in by_id]

    def _copy(self, args: list[Any], out: list[bytes], uid_mode: bool) -> str:
        token, dest = self._destination(args, "COPY")
        by_uid = self._sync(out, expunges=uid_mode)
        targets = self._targets(token, uid_mode, by_uid)
        if dest is None:
            return "NO [TRYCREATE] NOT_FOUND: no such destination mailbox"
        new_ids = [str(self._mb.copy(m.id, str(dest["name"]))["message_id"]) for _, m in targets]
        verb = f"{'UID ' if uid_mode else ''}COPY completed"
        if not targets:
            return f"OK {verb}"
        if self.selected is not None and str(dest["name"]) == self.selected.engine_folder:
            self._sync(out, expunges=uid_mode)
        src = compress_ranges(int(m.imap_uid or 0) for _, m in targets)
        dst = compress_ranges(self._new_uids(dest, new_ids))
        return f"OK [COPYUID {dest['uid_validity']} {src} {dst}] {verb}"

    def _move(self, args: list[Any], out: list[bytes], uid_mode: bool) -> str:
        token, dest = self._destination(args, "MOVE")
        self._require_writable()
        by_uid = self._sync(out, expunges=uid_mode)
        targets = self._targets(token, uid_mode, by_uid)
        if dest is None:
            return "NO [TRYCREATE] NOT_FOUND: no such destination mailbox"
        for _, m in targets:
            self._mb.move(m.id, str(dest["name"]))
        if targets:
            src = compress_ranges(int(m.imap_uid or 0) for _, m in targets)
            dst = compress_ranges(self._new_uids(dest, [m.id for _, m in targets]))
            out.append(f"* OK [COPYUID {dest['uid_validity']} {src} {dst}] moved\r\n".encode())
            self._sync(out, expunges=True)
        return f"OK {'UID ' if uid_mode else ''}MOVE completed"

    # ----------------------------------------------------------------- EXPUNGE
    def _expunge(self, out: list[bytes]) -> str:
        sel = self.selected
        assert sel is not None
        self._require_writable()
        by_uid = self._sync(out, expunges=True)
        if any("DELETED" in m.flags for m in by_uid.values()):
            self._mb.expunge(sel.engine_folder)
            self._sync(out, expunges=True)
        return "OK EXPUNGE completed"

    def _uid_expunge(self, args: list[Any], out: list[bytes], uid_mode: bool) -> str:
        self._arity(args, 1, "UID EXPUNGE uid-set")
        self._require_writable()
        by_uid = self._sync(out, expunges=True)
        for _, msg in self._targets(args[0], True, by_uid):
            if "DELETED" in msg.flags:
                self._mb.delete(msg.id)
        self._sync(out, expunges=True)
        return "OK UID EXPUNGE completed"

    # ----------------------------------------------------------------- APPEND
    def cmd_append(self, tag: str, args: list[Any], out: list[bytes]) -> str:
        if len(args) < 2 or len(args) > 4 or not isinstance(args[-1], bytes):
            raise ParseError("usage: APPEND mailbox [(flags)] [date-time] {literal}")
        flags: list[Any] = []
        for extra in args[1:-1]:
            if isinstance(extra, list):
                flags = extra
            elif not isinstance(extra, QStr):  # the date-time is accepted and ignored
                raise ParseError("invalid APPEND argument")
        system: list[str] = []
        keywords: list[str] = []
        for tok in flags:
            flag = astring(tok)
            if flag.lower() in SYSTEM_FLAGS:
                system.append(SYSTEM_FLAGS[flag.lower()][1])
            elif is_keyword(flag) and not flag.startswith("\\"):
                keywords.append(flag)
            else:
                raise ParseError(f"invalid flag {flag[:40]}")
        self._poll()  # UIDVALIDITY in APPENDUID must be current
        dest = self._folder(astring(args[0]))
        if dest is None:
            return "NO [TRYCREATE] NOT_FOUND: no such mailbox"
        raw: bytes = args[-1]
        if dest.get("system_type") == "sent":
            return self._append_sent(dest, raw)
        if dest.get("system_type") == "drafts" and self._own_draft(raw):
            return self._append_draft(dest, raw, system, keywords, out)
        # checked (and recorded) before anything is stored: \Draft marks real drafts only, and
        # system names are no keywords; Sent/Drafts placement is refused by the engine
        error = system_label_error(keywords) or (
            draft_flag_error(False, system, ()) if dest.get("system_type") != "drafts" else None
        )
        if error is not None:
            self._mb.record_refused("insert", error, folder=str(dest["name"]), size=len(raw))
        new_id = str(self._mb.insert_message(raw, folder=str(dest["name"]))["message_id"])
        if system:
            self._mb.set_flags(new_id, add=sorted(set(system)))
        if keywords:
            self._mb.modify_labels(new_id, add=sorted(set(keywords)))
        if self.selected is not None and self.selected.engine_folder == str(dest["name"]):
            self._sync(out, expunges=False)
        uids = self._new_uids(dest, [new_id])
        return f"OK [APPENDUID {dest['uid_validity']} {uids[0]}] APPEND completed"

    def _append_sent(self, dest: dict[str, Any], raw: bytes) -> str:
        """APPEND to Sent (a client filing its copy after an SMTP send): OK, but nothing new
        is stored. The submission already filed the real sent copy; an appended one would be
        evidence of nothing. The matching sent message's UID is reported when the Message-ID
        names one."""
        try:
            header = parse_mime(raw).message_id
        except (SimError, ValueError):
            header = None  # nothing is stored either way
        if header:
            for m in self._mb.find_by_message_id(header):
                if m.folder == dest["name"] and m.imap_uid is not None:
                    return f"OK [APPENDUID {dest['uid_validity']} {m.imap_uid}] APPEND completed"
        return "OK APPEND completed"

    def _own_draft(self, raw: bytes) -> bool:
        parsed = parse_mime(raw)
        sender = parsed.from_.email.casefold() if parsed.from_ is not None else ""
        assert self.agent is not None
        return sender == self.agent.address.casefold()

    def _append_draft(self, dest: dict[str, Any], raw: bytes, system: list[str],
                      keywords: list[str], out: list[bytes]) -> str:  # fmt: skip
        """APPEND to Drafts of a message from this mailbox (a client saving a draft): a REAL
        draft, made through the engine's draft path (the `draft` operation), so it is graded
        as one and can be sent later. Its MIME is rebuilt from the parsed parts."""
        parsed = parse_mime(raw)
        if parsed.attachments:
            raise UnsupportedOperation("drafts with attachments cannot be saved over IMAP")
        error = system_label_error(keywords)
        if error is not None:
            self._mb.record_refused("insert", error, folder=str(dest["name"]), size=len(raw))
        draft = self._mb.create_draft(
            to=[a.email for a in parsed.to], cc=[a.email for a in parsed.cc],
            bcc=[a.email for a in parsed.bcc], subject=parsed.subject,
            text=parsed.body_text or "", html=parsed.body_html,
            in_reply_to=parsed.in_reply_to, references=parsed.references or None,
        )  # fmt: skip
        new_id = str(draft["message_id"])
        extra = sorted(set(system) - {"DRAFT", "SEEN"})
        if extra:
            self._mb.set_flags(new_id, add=extra)
        if keywords:
            self._mb.modify_labels(new_id, add=sorted(set(keywords)))
        if self.selected is not None and self.selected.engine_folder == str(dest["name"]):
            self._sync(out, expunges=False)
        uids = self._new_uids(dest, [new_id])
        return f"OK [APPENDUID {dest['uid_validity']} {uids[0]}] APPEND completed"

    # ----------------------------------------------------------------- SEARCH
    def _search(self, args: list[Any], out: list[bytes], uid_mode: bool) -> str:
        args = list(args)
        if len(args) >= 2 and isinstance(args[0], str) and args[0].upper() == "CHARSET":
            charset = astring(args[1]).upper()
            if charset not in ("US-ASCII", "UTF-8"):
                return "NO [BADCHARSET (US-ASCII UTF-8)] unsupported charset"
            args = args[2:]
        if not args:
            raise ParseError("usage: SEARCH criteria")
        by_uid = self._sync(out, expunges=uid_mode)
        sel = self.selected
        assert sel is not None
        preds: list[Callable[[int, CanonicalMessage], bool]] = []
        pos = 0
        while pos < len(args):
            pred, pos = self._search_key(args, pos)
            preds.append(pred)
        hits = []
        for idx, uid in enumerate(sel.uids):
            msg = by_uid.get(uid)
            if msg is not None and all(p(idx + 1, msg) for p in preds):
                hits.append(uid if uid_mode else idx + 1)
        out.append(("* SEARCH" + "".join(f" {n}" for n in hits) + "\r\n").encode())
        return f"OK {'UID ' if uid_mode else ''}SEARCH completed"

    def _search_key(self, args: list[Any], pos: int, depth: int = 0
                    ) -> tuple[Callable[[int, CanonicalMessage], bool], int]:  # fmt: skip
        if depth > MAX_SEARCH_DEPTH:
            raise ParseError("search criteria nested too deeply")
        if pos >= len(args):
            raise ParseError("missing search key")
        tok = args[pos]
        pos += 1
        if isinstance(tok, list):
            if not tok:
                raise ParseError("empty search list")
            inner: list[Callable[[int, CanonicalMessage], bool]] = []
            i = 0
            while i < len(tok):
                p, i = self._search_key(tok, i, depth + 1)
                inner.append(p)
            return (lambda n, m: all(p(n, m) for p in inner)), pos
        if not isinstance(tok, str) or isinstance(tok, QStr):
            raise ParseError("invalid search key")
        key = tok.upper()

        def arg() -> object:
            nonlocal pos
            if pos >= len(args):
                raise ParseError(f"{key} needs an argument")
            pos += 1
            return args[pos - 1]

        flag_keys = {"ANSWERED": "ANSWERED", "DELETED": "DELETED", "DRAFT": "DRAFT",
                     "FLAGGED": "FLAGGED", "SEEN": "SEEN"}  # fmt: skip
        if key in flag_keys:
            return (lambda n, m: flag_keys[key] in m.flags), pos
        if key.startswith("UN") and key[2:] in flag_keys:
            return (lambda n, m: flag_keys[key[2:]] not in m.flags), pos
        if key in ("ALL", "OLD"):
            return (lambda n, m: True), pos
        if key in ("RECENT", "NEW"):
            return (lambda n, m: False), pos
        if key in ("KEYWORD", "UNKEYWORD"):
            word = astring(arg())
            want = key == "KEYWORD"
            return (lambda n, m: _has_keyword(user_labels(m), word) == want), pos
        if key in ("FROM", "TO", "CC", "BCC"):
            needle = astring(arg()).lower()

            def addr_match(n: int, m: CanonicalMessage) -> bool:
                addrs = {"FROM": [m.from_], "TO": m.to, "CC": m.cc, "BCC": m.bcc}[key]
                return any(needle in f"{a.name or ''} <{a.email}>".lower() for a in addrs)

            return addr_match, pos
        if key in ("SUBJECT", "BODY", "TEXT"):
            needle = astring(arg()).lower()
            return (lambda n, m: needle in self._search_text(key, m)), pos
        if key == "HEADER":
            field_name, needle = astring(arg()).lower(), astring(arg()).lower()

            def header_match(n: int, m: CanonicalMessage) -> bool:
                values = [v for k, vs in m.headers.items() if k.lower() == field_name
                          for v in (vs if isinstance(vs, list) else [vs])]  # fmt: skip
                return any(needle in str(v).lower() for v in values)

            return header_match, pos
        if key in ("BEFORE", "ON", "SINCE", "SENTBEFORE", "SENTON", "SENTSINCE"):
            day = parse_search_date(arg())
            sent = key.startswith("SENT")
            op = key.removeprefix("SENT")

            def date_match(n: int, m: CanonicalMessage) -> bool:
                d = (m.created_at.astimezone(UTC) if sent else internal_date(m)).date()
                return d < day if op == "BEFORE" else d == day if op == "ON" else d >= day

            return date_match, pos
        if key in ("LARGER", "SMALLER"):
            size_tok = arg()
            if not isinstance(size_tok, str) or not size_tok.isdigit():
                raise ParseError(f"{key} needs a number")
            size = int(size_tok)
            if key == "LARGER":
                return (lambda n, m: len(self._raw(m)[0]) > size), pos
            return (lambda n, m: len(self._raw(m)[0]) < size), pos
        if key == "UID":
            sel = self.selected
            assert sel is not None
            ranges = parse_sequence_set(arg(), max(sel.uids, default=0))
            return (lambda n, m: in_ranges(int(m.imap_uid or 0), ranges)), pos
        if key == "NOT":
            inner_p, pos = self._search_key(args, pos, depth + 1)
            return (lambda n, m: not inner_p(n, m)), pos
        if key == "OR":
            left, pos = self._search_key(args, pos, depth + 1)
            right, pos = self._search_key(args, pos, depth + 1)
            return (lambda n, m: left(n, m) or right(n, m)), pos
        if key in ("EMAILID", "THREADID"):
            wanted = astring(arg())
            if key == "EMAILID":
                return (lambda n, m: m.id == wanted), pos
            return (lambda n, m: m.thread_id == wanted), pos
        if is_sequence_set(tok):
            sel = self.selected
            assert sel is not None
            ranges = parse_sequence_set(tok, len(sel.uids))
            return (lambda n, m: in_ranges(n, ranges)), pos
        raise ParseError(f"unknown search key {key[:40]}")

    def _search_text(self, key: str, m: CanonicalMessage) -> str:
        if key == "SUBJECT":
            return m.subject.lower()
        body = f"{m.body_text or ''}\n{m.body_html or ''}"
        if key == "BODY":
            return body.lower()
        header = self._raw(m)[1].header.decode("utf-8", "replace")
        return f"{header}\n{m.subject}\n{body}".lower()


class _ReadOnly(Exception):
    pass


# --------------------------------------------------------------------------- connection
class ImapConnection:
    """asyncio framing for one client: lines, literals, continuations, STARTTLS."""

    def __init__(self, session: ImapSession, reader: asyncio.StreamReader,
                 writer: asyncio.StreamWriter, *, tls_context: TLSContext | None = None,
                 idle_timeout: float = IDLE_TIMEOUT) -> None:  # fmt: skip
        self.session, self.reader, self.writer = session, reader, writer
        self.tls_context = tls_context
        self.idle_timeout = idle_timeout

    async def _send(self, chunks: list[bytes]) -> None:
        if chunks:
            self.writer.write(b"".join(chunks))
            try:
                await asyncio.wait_for(self.writer.drain(), WRITE_TIMEOUT)
            except TimeoutError:
                raise WriteTimeout() from None

    @property
    def _timeout(self) -> float:
        if self.session.agent is None:
            return min(self.idle_timeout, PREAUTH_TIMEOUT)
        return self.idle_timeout

    async def _readline(self) -> bytes:
        return await asyncio.wait_for(self.reader.readuntil(b"\n"), self._timeout)

    async def _read_command(self) -> bytes | None:
        line = await self._readline()
        buf = bytearray(line)
        while (marker := literal_marker(line)) is not None:
            size, non_sync = marker
            authed = self.session.agent is not None
            limit = MAX_LITERAL if authed else PREAUTH_LITERAL
            total = MAX_COMMAND if authed else PREAUTH_COMMAND
            if size > limit or len(buf) + size > total:
                if non_sync:
                    raise Disconnect("literal too large")
                head = bytes(buf).split(b" ", 1)[0].decode("ascii", "replace")
                tag = head if valid_tag(head) else "*"
                await self._send([f"{tag} NO [TOOBIG] literal too large\r\n".encode()])
                return None
            if not non_sync:
                await self._send([b"+ Ready for literal data\r\n"])
            buf += await asyncio.wait_for(self.reader.readexactly(size), self._timeout)
            line = await self._readline()
            buf += line
            if self.session.agent is None and len(buf) > PREAUTH_COMMAND:
                raise Disconnect("command too large")
        return bytes(buf)

    async def run(self) -> None:
        with use_interface("imap"):  # worker threads (asyncio.to_thread) inherit it
            await self._run()

    async def _run(self) -> None:
        try:
            await self._send([self.session.greeting()])
            await self._loop()
        except WriteTimeout:
            self.writer.transport.abort()  # the client stopped reading: drop it
        except Disconnect as exc:
            await self._bye(str(exc))
        except TimeoutError:
            await self._bye("autologout; idle for too long")
        except (asyncio.LimitOverrunError, ValueError):
            await self._bye("command line too long")
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            self.writer.close()
            with contextlib.suppress(ConnectionError, OSError, TimeoutError):
                await asyncio.wait_for(self.writer.wait_closed(), 5)
            self.writer.transport.abort()  # no-op once closed; drops unread buffers otherwise

    async def _bye(self, text: str) -> None:
        with contextlib.suppress(ConnectionError, OSError, WriteTimeout):
            await self._send([f"* BYE {text}\r\n".encode()])

    async def _loop(self) -> None:
        while True:
            data = await self._read_command()
            if data is None:
                continue
            if not data.strip():
                continue
            reply = await asyncio.to_thread(self.session.execute, data, self._flusher())
            await self._send(reply.lines)
            while reply.continuation is not None and reply.pending is not None:
                await self._send([reply.continuation])
                line = await self._readline()
                reply = await asyncio.to_thread(reply.pending, line)
                await self._send(reply.lines)
            if reply.starttls:
                await self._start_tls()
            if reply.close:
                return

    def _flusher(self) -> Callable[[list[bytes]], None]:
        """Send untagged responses from the worker thread while a command is running."""
        loop = asyncio.get_running_loop()

        def flush(chunks: list[bytes]) -> None:
            future = asyncio.run_coroutine_threadsafe(self._send(chunks), loop)
            try:
                future.result(timeout=WRITE_TIMEOUT + 5)
            except TimeoutError:  # pragma: no cover - _send times out first
                future.cancel()
                raise WriteTimeout() from None

        return flush

    async def _start_tls(self) -> None:
        if getattr(self.reader, "_buffer", b""):
            # pipelined plaintext after STARTTLS would be injected into the TLS session
            raise Disconnect("data received after STARTTLS before the TLS handshake")
        assert self.tls_context is not None
        await self.writer.start_tls(self.tls_context)
        self.session.secured = True


__all__ = ["ImapConnection", "ImapSession", "Reply", "imap_flags", "user_labels"]
