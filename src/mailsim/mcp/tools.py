"""MCP tool catalogue (spec 8.1-8.3): explicit argument schemas and role-bound handlers.

Each tool names the role facade(s) it runs on. That one field drives both the per-role tool
listing and the authorization check made on EVERY call (``facade_for(principal)`` must yield
one of those facades), so hiding a tool is never the security boundary (spec 8). Handlers
only ever see a facade: the agent's is bound to its key's run + mailbox, and none of the
agent tools can reach scenario, answer-key or evaluation data.
"""

from __future__ import annotations

import base64
import binascii
import copy
import functools
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Any, Literal, cast

import mcp_types as types
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints

from mailsim.api.auth import revoke_agent_keys
from mailsim.api.pagination import decode_sequence_cursor, encode_sequence_cursor, page_messages
from mailsim.api.schemas import MAX_RAW_B64_CHARS, normalise_addresses
from mailsim.auth.principal import Role
from mailsim.domain.errors import InvalidRequest, NotFound, UnsupportedOperation
from mailsim.domain.models import MAX_LABEL_CHARS, MAX_LABELS_PER_CHANGE, CanonicalMessage
from mailsim.domain.states import TERMINAL
from mailsim.engine.facades import AgentMailbox, Controller, EvaluatorView
from mailsim.engine.pagination import MAX_PAGE_SIZE, check_page_size
from mailsim.personas.runtime import PersonaRuntime, advance_clock, tick_clock

JSON = dict[str, Any]
ROLE_FACADE: dict[Role, type[Any]] = {
    Role.AGENT: AgentMailbox,
    Role.CONTROLLER: Controller,
    Role.EVALUATOR: EvaluatorView,
}
TERMINAL_STATUSES = frozenset(str(s) for s in TERMINAL)


@dataclass(frozen=True)
class Deps:
    """Optional collaborators wired by the composition root (never reachable by agents)."""

    session_factory: Any = None  # sessionmaker: agent-key revocation on cancel / completion
    evaluation_service: Any = None  # mailsim.evaluation.service.EvaluationService
    persona_runtime_factory: Callable[[Controller], Any] | None = None


class Args(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


# Argument bounds: generous for real use, small enough that no tool call can make the server
# allocate or log unbounded input (bodies stay within the REST/MIME body limits as well).
MAX_ID_CHARS = 256
MAX_LIST_ITEMS = 500
MAX_QUERY_CHARS = 1024
MAX_SUBJECT_CHARS = 16 * 1024
MAX_BODY_CHARS = 5 * 1024 * 1024
MAX_CURSOR_CHARS = 1024
# get_attachment returns the bytes base64-encoded inside the JSON result: keep that bounded.
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024

Id = Annotated[str, StringConstraints(min_length=1, max_length=MAX_ID_CHARS)]
Name = Annotated[str, StringConstraints(max_length=MAX_ID_CHARS)]  # folder, label, flag, ...
Address = Annotated[str, StringConstraints(max_length=MAX_ID_CHARS)]
Body = Annotated[str, StringConstraints(max_length=MAX_BODY_CHARS)]
Subject = Annotated[str, StringConstraints(max_length=MAX_SUBJECT_CHARS)]
CursorStr = Annotated[str, StringConstraints(max_length=MAX_CURSOR_CHARS)]
LabelName = Annotated[str, StringConstraints(max_length=MAX_LABEL_CHARS)]


def _labels(description: str | None = None) -> Any:
    return Field(default_factory=list, max_length=MAX_LABELS_PER_CHANGE, description=description)


def _list(description: str | None = None) -> Any:
    return Field(default_factory=list, max_length=MAX_LIST_ITEMS, description=description)


# recipient lists: "Name <addr@example.com>" entries are reduced to the address
Recipients = Annotated[list[Address], AfterValidator(normalise_addresses)]
RECIPIENTS_HELP = (
    'addresses; "Name <addr@example.com>" is accepted (quote a name containing '
    "a comma); at most 100 distinct recipients per message"
)


PageSize = Field(default=50, ge=1, le=MAX_PAGE_SIZE, description="items per page")
Cursor = Field(default=None, description="`next_cursor` of the previous page")


# ---- argument models (their JSON Schemas are the tools' inputSchema) ----------------------
class ListMessagesArgs(Args):
    folder: Name | None = Field(default=None, description="folder id, name or system type "
                                "(inbox, sent, drafts, trash, archive); default: all folders")  # fmt: skip
    page_size: int = PageSize
    cursor: CursorStr | None = Cursor
    order: Literal["asc", "desc"] = Field(default="asc", description="by (created_at, id)")


class MessageIdArgs(Args):
    message_id: Id = Field(description="message id (msg_...)")


class ThreadIdArgs(Args):
    thread_id: Id = Field(description="thread id")


class SearchArgs(Args):
    query: str = Field(min_length=1, max_length=MAX_QUERY_CHARS,
                       description="matched against subject, body and sender")  # fmt: skip
    folder: Name | None = Field(default=None, description="restrict hits to this folder")
    page_size: int = PageSize
    cursor: CursorStr | None = Cursor


class SendArgs(Args):
    to: Recipients = _list(RECIPIENTS_HELP)
    cc: Recipients = _list()
    bcc: Recipients = _list()
    subject: Subject = ""
    text: Body = ""
    html: Body | None = None


class ReplyArgs(Args):
    message_id: Id
    text: Body
    reply_all: bool = False


class ForwardArgs(Args):
    message_id: Id
    to: Recipients = Field(min_length=1, max_length=MAX_LIST_ITEMS, description=RECIPIENTS_HELP)
    cc: Recipients = _list()
    text: Body = Field(default="", description="written above the forwarded message")


class AttachmentArgs(Args):
    message_id: Id
    attachment_id: Id = Field(description="`id` of an entry of the message's `attachments`")


class ManageLabelsArgs(Args):
    message_id: Id
    folder: Name | None = Field(default=None, description="move the message to this folder")
    add_labels: list[LabelName] = _labels()
    remove_labels: list[LabelName] = _labels()
    add_flags: list[Name] = _list("IMAP-style flags, e.g. SEEN, FLAGGED, ANSWERED")
    remove_flags: list[Name] = _list()


class ChangesArgs(Args):
    since: int = Field(default=0, ge=0, description="`next_since` of the previous call")
    limit: int = Field(default=500, ge=1, le=500)


class NoArgs(Args):
    pass


class RunArgs(Args):
    run_id: Id


# every message field of inject (raw_base64 replaces all of them)
INJECT_FIELDS = ("from_", "to", "cc", "subject", "text", "html", "in_reply_to", "references")


class InjectArgs(Args):
    run_id: Id
    mailbox_id: Id = Field(description="target simulated mailbox")
    from_: Address | None = Field(default=None, alias="from", description="sender address")
    to: list[Address] = _list()
    cc: list[Address] = _list()
    subject: Subject | None = None
    text: Body | None = None
    html: Body | None = None
    in_reply_to: Name | None = None
    references: list[Name] | None = Field(default=None, max_length=MAX_LIST_ITEMS)
    raw_base64: str | None = Field(default=None, max_length=MAX_RAW_B64_CHARS,
                                   description="a complete RFC 5322 message instead of fields")  # fmt: skip
    event_id: Id | None = None


class AdvanceArgs(Args):
    run_id: Id
    seconds: float = Field(ge=0, description="virtual seconds to advance the run clock")


class TraceArgs(Args):
    run_id: Id
    cursor: CursorStr | None = Cursor
    page_size: int = Field(default=100, ge=1, le=MAX_PAGE_SIZE)
    type: Name | None = Field(default=None, description="only events of this type")
    actor: Name | None = Field(default=None, description="only events by this actor")
    epoch: int | None = Field(default=None, ge=0)


class ResultsArgs(Args):
    run_id: Id
    evaluate: bool = Field(default=False, description="grade the run now instead of returning "
                           "the latest stored evaluation")  # fmt: skip
    answer_key_version: int | None = Field(default=None, description="evaluate only")
    checks: Literal["on", "off"] | None = Field(default=None, description="evaluate only")
    complete_run: bool = Field(default=True, description="evaluate only: set PASSED/FAILED")


# ---- helpers ------------------------------------------------------------------------------
def message(m: CanonicalMessage) -> JSON:
    return m.model_dump(mode="json", by_alias=True)


def _page(items: list[CanonicalMessage], next_cursor: str | None) -> JSON:
    return {"items": [message(m) for m in items], "next_cursor": next_cursor}


def _folder_name(mb: AgentMailbox, folder: str) -> str:
    for f in mb.list_folders():  # the engine's matching rules: id, system type or name
        if folder in (f["id"], f["system_type"]) or folder.lower() == f["name"].lower():
            return str(f["name"])
    raise NotFound(f"folder {folder!r} not found")


def _runtime(deps: Deps, ctl: Controller) -> PersonaRuntime | None:
    factory = deps.persona_runtime_factory
    return None if factory is None else cast("PersonaRuntime", factory(ctl))


def _revoke_agent_keys(deps: Deps, view: Controller | EvaluatorView, run_id: str) -> None:
    if deps.session_factory is None:
        return
    with deps.session_factory.begin() as s:
        revoke_agent_keys(s, view.scope.tenant_id, view.scope.project_id, run_id)


# ---- agent tools (spec 8.1) ---------------------------------------------------------------
def list_messages(mb: AgentMailbox, a: ListMessagesArgs, _: Deps) -> JSON:
    p = mb.list_messages(folder=a.folder, page_size=a.page_size, cursor=a.cursor, order=a.order)
    return _page(p.items, p.next_cursor)


def get_message(mb: AgentMailbox, a: MessageIdArgs, _: Deps) -> JSON:
    return message(mb.get_message(a.message_id))


def get_thread(mb: AgentMailbox, a: ThreadIdArgs, _: Deps) -> JSON:
    return {"items": [message(m) for m in mb.get_thread(a.thread_id)]}


def search_messages(mb: AgentMailbox, a: SearchArgs, _: Deps) -> JSON:
    check_page_size(a.page_size)
    hits = mb.search_messages(a.query)
    if a.folder is not None:
        name = _folder_name(mb, a.folder)
        hits = [h for h in hits if h.folder == name]
    p = page_messages(hits, a.page_size, a.cursor)
    return _page(p.items, p.next_cursor)


def send_message(mb: AgentMailbox, a: SendArgs, _: Deps) -> JSON:
    return mb.send(to=a.to, cc=a.cc, bcc=a.bcc, subject=a.subject, text=a.text, html=a.html)


def reply_to_message(mb: AgentMailbox, a: ReplyArgs, _: Deps) -> JSON:
    return mb.reply(a.message_id, text=a.text, reply_all=a.reply_all)


def forward_message(mb: AgentMailbox, a: ForwardArgs, _: Deps) -> JSON:
    return mb.forward(a.message_id, to=a.to, cc=a.cc, text=a.text)


def get_attachment(mb: AgentMailbox, a: AttachmentArgs, _: Deps) -> JSON:
    ref, data = mb.get_attachment(a.message_id, a.attachment_id, max_bytes=MAX_ATTACHMENT_BYTES)
    return {"id": ref.id, "filename": ref.filename, "mime_type": ref.mime_type,
            "size": len(data), "checksum": ref.checksum,
            "content_base64": base64.b64encode(data).decode("ascii")}  # fmt: skip


def manage_labels(mb: AgentMailbox, a: ManageLabelsArgs, _: Deps) -> JSON:
    if not (a.folder or a.add_labels or a.remove_labels or a.add_flags or a.remove_flags):
        raise InvalidRequest("nothing to change: set folder, labels or flags")
    # system labels and the DRAFT flag: refused (and recorded) before the first write
    mb.check_labels(a.message_id, add=a.add_labels, remove=a.remove_labels)
    mb.check_draft_flag_of(a.message_id, add=a.add_flags, remove=a.remove_flags)
    if a.folder is not None:
        _folder_name(mb, a.folder)  # validate before the first write
        mb.move(a.message_id, a.folder)
    if a.add_labels or a.remove_labels:
        mb.modify_labels(a.message_id, add=a.add_labels, remove=a.remove_labels)
    if a.add_flags or a.remove_flags:
        mb.set_flags(a.message_id, add=a.add_flags, remove=a.remove_flags)
    return message(mb.get_message(a.message_id))


def delete_message(mb: AgentMailbox, a: MessageIdArgs, _: Deps) -> JSON:
    mb.delete(a.message_id)
    return {"deleted": a.message_id}


def list_folders(mb: AgentMailbox, _a: NoArgs, _: Deps) -> JSON:
    return {"items": [{"id": f["id"], "name": f["name"], "system_type": f["system_type"]}
                      for f in mb.list_folders()]}  # fmt: skip


def get_changes(mb: AgentMailbox, a: ChangesArgs, _: Deps) -> JSON:
    return mb.list_changes(a.since, limit=a.limit)


# ---- simulation control (spec 8.2) --------------------------------------------------------
def _inject_message(a: InjectArgs) -> JSON | bytes:
    if a.raw_base64 is not None:
        mixed = [f for f in INJECT_FIELDS if f in a.model_fields_set]
        if mixed:
            raise InvalidRequest("provide either `raw_base64` or message fields, not both",
                                 details={"fields": [f.rstrip("_") for f in mixed]})  # fmt: skip
        try:
            return base64.b64decode(a.raw_base64, validate=True)
        except (binascii.Error, ValueError):
            raise InvalidRequest("raw_base64 is not valid base64") from None
    if a.from_ is None or not a.to:
        raise InvalidRequest("`from` and at least one `to` are required (or `raw_base64`)")
    fields = a.model_dump(by_alias=True, exclude_none=True,
                          include=set(INJECT_FIELDS))  # fmt: skip
    return fields


def inject_incoming_email(ctl: Controller, a: InjectArgs, _: Deps) -> JSON:
    return ctl.inject(a.run_id, a.mailbox_id, _inject_message(a), event_id=a.event_id)


def advance_simulation(ctl: Controller, a: AdvanceArgs, deps: Deps) -> JSON:
    # persona replies are answered at their due times on the way (`advance_clock`)
    return advance_clock(ctl, _runtime(deps, ctl), a.run_id, a.seconds)


def trigger_scheduled_event(ctl: Controller, a: RunArgs, deps: Deps) -> JSON:
    return tick_clock(ctl, _runtime(deps, ctl), a.run_id)


def reset_run(ctl: Controller, a: RunArgs, _: Deps) -> JSON:
    ctl.reset_run(a.run_id)
    return ctl.run_info(a.run_id)


def start_run(ctl: Controller, a: RunArgs, _: Deps) -> JSON:
    ctl.start_run(a.run_id)
    return ctl.run_info(a.run_id)


def cancel_run(ctl: Controller, a: RunArgs, deps: Deps) -> JSON:
    ctl.cancel_run(a.run_id)
    _revoke_agent_keys(deps, ctl, a.run_id)  # a cancelled run has no agent
    return ctl.run_info(a.run_id)


def replay_run(ctl: Controller, a: RunArgs, _: Deps) -> JSON:
    r = ctl.replay_run(a.run_id)
    return {"replay_run_id": r.replay_run_id, "matches": r.matches,
            "original_hash": r.original_hash, "replay_hash": r.replay_hash,
            "event_count": r.event_count, "missing_blobs": list(r.missing_blobs)}  # fmt: skip


# ---- inspection / evaluation (spec 8.3) ---------------------------------------------------
def list_run_mailboxes(view: Controller | EvaluatorView, a: RunArgs, _: Deps) -> JSON:
    return {"items": view.list_mailboxes(a.run_id)}


def get_run_status(view: Controller | EvaluatorView, a: RunArgs, _: Deps) -> JSON:
    return view.run_info(a.run_id)


def get_run_trace(view: Controller | EvaluatorView, a: TraceArgs, _: Deps) -> JSON:
    rows, nxt = view.events_page(a.run_id, after_sequence=decode_sequence_cursor(a.cursor),
                                 limit=a.page_size, event_type=a.type, actor=a.actor,
                                 epoch=a.epoch)  # fmt: skip
    return {"items": rows, "next_cursor": encode_sequence_cursor(nxt) if nxt is not None else None}


def get_test_results(view: EvaluatorView, a: ResultsArgs, deps: Deps) -> JSON:
    service = deps.evaluation_service
    if service is None:
        raise UnsupportedOperation("server is not configured with an evaluation service",
                                   details={"missing": "evaluation_service"})  # fmt: skip
    if not a.evaluate:
        report = service.latest(view.scope, a.run_id)
    else:
        report = service.evaluate(view.scope, a.run_id, answer_key_version=a.answer_key_version,
                                  checks=a.checks, complete_run=a.complete_run)  # fmt: skip
        if view.run_info(a.run_id)["status"] in TERMINAL_STATUSES:
            _revoke_agent_keys(deps, view, a.run_id)  # completed through this call
    out: JSON = report.model_dump(mode="json")
    return out


# ---- catalogue ----------------------------------------------------------------------------
Handler = Callable[[Any, Any, Deps], JSON]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    facades: tuple[type[Any], ...]  # the facade(s) a caller's role must map to
    args: type[Args]
    handler: Handler
    read_only: bool = False
    destructive: bool = False

    def roles(self) -> frozenset[Role]:
        return frozenset(r for r, f in ROLE_FACADE.items() if f in self.facades)

    def tool(self) -> types.Tool:
        return types.Tool(
            name=self.name,
            description=self.description,
            input_schema=input_schema(self.args),
            annotations=types.ToolAnnotations(
                read_only_hint=self.read_only,
                destructive_hint=self.destructive if not self.read_only else None,
                open_world_hint=False,  # a closed sandbox: nothing ever leaves it
            ),
        )


@functools.cache
def _schema(args: type[Args]) -> JSON:
    schema = args.model_json_schema(by_alias=True)
    schema.setdefault("properties", {})
    return schema


def input_schema(args: type[Args]) -> JSON:
    """The tool's inputSchema (generated once per model; callers get their own copy)."""
    return copy.deepcopy(_schema(args))


AGENT = (AgentMailbox,)
CONTROL = (Controller,)
INSPECT = (Controller, EvaluatorView)
EVALUATE = (EvaluatorView,)

TOOLS: tuple[ToolSpec, ...] = (
    ToolSpec(
        "list_messages",
        "List messages in your mailbox (optionally one folder), paged. "
        "Follow next_cursor until it is null.",
        AGENT,
        ListMessagesArgs,
        list_messages,
        read_only=True,
    ),
    ToolSpec(
        "get_message",
        "Get one message of your mailbox by id.",
        AGENT,
        MessageIdArgs,
        get_message,
        read_only=True,
    ),
    ToolSpec(
        "get_thread",
        "Get every message of a conversation thread in your mailbox.",
        AGENT,
        ThreadIdArgs,
        get_thread,
        read_only=True,
    ),
    ToolSpec(
        "search_messages",
        "Search your mailbox (subject, body, sender), paged.",
        AGENT,
        SearchArgs,
        search_messages,
        read_only=True,
    ),
    ToolSpec(
        "get_attachment",
        "Download one attachment of a message (ids from the message's `attachments`); the "
        "bytes come back base64-encoded in content_base64. Attachments larger than 10 MB are "
        "refused with UNSUPPORTED_OPERATION.",
        AGENT,
        AttachmentArgs,
        get_attachment,
        read_only=True,
    ),
    ToolSpec(
        "send_message",
        "Send a new message. A recipient that is not allowed is rejected with "
        "EXTERNAL_DELIVERY_BLOCKED.",
        AGENT,
        SendArgs,
        send_message,
    ),
    ToolSpec(
        "reply_to_message",
        "Reply to a message (reply_all to include every recipient).",
        AGENT,
        ReplyArgs,
        reply_to_message,
    ),
    ToolSpec(
        "forward_message",
        "Forward a message, attachments included, to new recipients (text goes above it).",
        AGENT,
        ForwardArgs,
        forward_message,
    ),
    ToolSpec(
        "manage_labels",
        "Move a message to a folder and/or add or remove labels and "
        "flags. Applied in that order as separate writes; returns the message.",
        AGENT,
        ManageLabelsArgs,
        manage_labels,
    ),
    ToolSpec(
        "delete_message",
        "Permanently delete a message (to trash it, use manage_labels with folder=trash).",
        AGENT,
        MessageIdArgs,
        delete_message,
        destructive=True,
    ),
    ToolSpec(
        "list_folders",
        "List the folders of your mailbox.",
        AGENT,
        NoArgs,
        list_folders,
        read_only=True,
    ),
    ToolSpec(
        "get_changes",
        "Mailbox change feed after event `since`; resume from next_since "
        "while has_more. reset_since=true means the mailbox was reset: resynchronise.",
        AGENT,
        ChangesArgs,
        get_changes,
        read_only=True,
    ),
    ToolSpec(
        "inject_incoming_email",
        "Deliver an inbound message into a simulated mailbox (fields, or raw_base64 MIME).",
        CONTROL,
        InjectArgs,
        inject_incoming_email,
    ),
    ToolSpec(
        "advance_simulation",
        "Advance the run's virtual clock, firing due scheduled "
        "events in order, then let personas reply.",
        CONTROL,
        AdvanceArgs,
        advance_simulation,
    ),
    ToolSpec(
        "trigger_scheduled_event",
        "Fire every scheduled event already due (clock does not move), then let personas reply.",
        CONTROL,
        RunArgs,
        trigger_scheduled_event,
    ),
    ToolSpec(
        "reset_run",
        "Restore the scenario baseline (new epoch); the run is READY again.",
        CONTROL,
        RunArgs,
        reset_run,
        destructive=True,
    ),
    ToolSpec("start_run", "Start a READY run.", CONTROL, RunArgs, start_run),
    ToolSpec(
        "cancel_run",
        "Cancel a run and revoke its agent credentials.",
        CONTROL,
        RunArgs,
        cancel_run,
        destructive=True,
    ),
    ToolSpec(
        "replay_run",
        "Replay the run's recorded events into a new run and compare state "
        "hashes (deterministic replay check).",
        CONTROL,
        RunArgs,
        replay_run,
    ),
    ToolSpec(
        "list_run_mailboxes",
        "Mailboxes of a run (operator view).",
        INSPECT,
        RunArgs,
        list_run_mailboxes,
        read_only=True,
    ),
    ToolSpec(
        "get_run_status",
        "Run status, epoch, virtual time and agent action count.",
        INSPECT,
        RunArgs,
        get_run_status,
        read_only=True,
    ),
    ToolSpec(
        "get_run_trace",
        "The run's event trace in sequence order, paged and filterable by type, actor and epoch.",
        INSPECT,
        TraceArgs,
        get_run_trace,
        read_only=True,
    ),
    ToolSpec(
        "get_test_results",
        "Latest evaluation report of a run, or (evaluate=true) grade "
        "it now against its answer key.",
        EVALUATE,
        ResultsArgs,
        get_test_results,
    ),
)
TOOLS_BY_NAME: dict[str, ToolSpec] = {t.name: t for t in TOOLS}


def tools_for(role: Role) -> list[ToolSpec]:
    facade = ROLE_FACADE.get(role)
    return [t for t in TOOLS if facade in t.facades]


__all__ = ["Deps", "ROLE_FACADE", "TOOLS", "TOOLS_BY_NAME", "ToolSpec", "tools_for"]
