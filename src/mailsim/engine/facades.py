"""Role-bound facades: the ONLY engine surface that interfaces (REST, provider adapters,
protocol listeners, MCP) may use. import-linter forbids those packages from importing
`mailsim.engine.core` directly, so a caller-supplied actor can never bypass the agent's
permission, mailbox and budget guards (spec 8.4 roles).

- AgentMailbox: actor is fixed to `agent`, bound to one run + one mailbox.
- Controller: test-harness operations (runs, injection, clock, reset, replay).
- EvaluatorView: complete state/trace inspection and run completion. Never given to agents.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from mailsim.auth.principal import Principal, Role
from mailsim.domain.errors import PermissionDenied
from mailsim.domain.models import AttachmentRef, CanonicalMessage
from mailsim.engine.core import (
    AGENT,
    CONTROLLER,
    EVALUATOR,
    MAX_ATTACHMENT_READ_BYTES,
    ReplayResult,
    SimEngine,
)
from mailsim.engine.pagination import Page
from mailsim.store.repos import Scope

# What an agent learns from a send / reply / forward / draft send. The engine's result also
# says how recipients were handled (simulated-mailbox copies, external recipients, the run's
# sink/event mode); that stays in the operator-only action log and is never shown to the agent.
AGENT_SEND_RESULT_FIELDS = ("message_id", "message_id_header")


def _agent_send_result(result: dict[str, Any]) -> dict[str, Any]:
    return {k: result[k] for k in AGENT_SEND_RESULT_FIELDS}


class AgentMailbox:
    """Agent-facing mailbox operations. No access to scenario, evaluation, clock or reset."""

    def __init__(self, engine: SimEngine, scope: Scope, run_id: str, mailbox_id: str) -> None:
        self._e, self._scope, self.run_id, self.mailbox_id = engine, scope, run_id, mailbox_id

    def _a(self) -> tuple[Scope, str, str]:
        return self._scope, self.run_id, self.mailbox_id

    # reads
    def list_messages(
        self,
        *,
        folder: str | None = None,
        page_size: int = 50,
        cursor: str | None = None,
        order: str = "asc",
    ) -> Page[CanonicalMessage]:
        return self._e.list_messages_page(*self._a(), folder=folder, page_size=page_size,
                                          cursor=cursor, order=order, actor=AGENT)  # fmt: skip

    def resolve_external_id(self, provider: str, entity_type: str, external_id: str) -> str:
        """Gmail/Graph id -> internal id, restricted to this mailbox."""
        return self._e.resolve_external_id(
            *self._a(), provider, entity_type, external_id, actor=AGENT
        )

    def get_message(self, message_id: str) -> CanonicalMessage:
        return self._e.get_message(*self._a(), message_id, actor=AGENT)

    def get_thread(self, thread_id: str) -> list[CanonicalMessage]:
        return self._e.get_thread(*self._a(), thread_id, actor=AGENT)

    def search_messages(self, query: str) -> list[CanonicalMessage]:
        return self._e.search_messages(*self._a(), query, actor=AGENT)

    def list_folders(self) -> list[dict[str, Any]]:
        return self._e.list_folders(*self._a(), actor=AGENT)

    def list_changes(self, since_sequence: int = 0, limit: int = 500) -> dict[str, Any]:
        return self._e.list_changes(*self._a(), since_sequence, limit=limit, actor=AGENT)

    # mutations
    def send(self, *, to: list[str], subject: str, text: str = "", cc: list[str] | None = None,
             bcc: list[str] | None = None, html: str | None = None) -> dict[str, Any]:  # fmt: skip
        return _agent_send_result(self._e.send(*self._a(), to=to, subject=subject, text=text,
                                               cc=cc, bcc=bcc, html=html, actor=AGENT))  # fmt: skip

    def reply(self, message_id: str, *, text: str, reply_all: bool = False,
              to: list[str] | None = None, cc: list[str] | None = None,
              bcc: list[str] | None = None, subject: str | None = None,
              html: str | None = None) -> dict[str, Any]:  # fmt: skip
        """One `reply` operation; `to`/`cc`/`bcc`/`subject`/`html` override the derived reply."""
        return _agent_send_result(self._e.reply(*self._a(), message_id, text=text,
                                                reply_all=reply_all, to=to, cc=cc, bcc=bcc,
                                                subject=subject, html=html, actor=AGENT))  # fmt: skip

    def get_raw(self, message_id: str) -> bytes | None:
        """Stored RFC 5322 bytes of one of this mailbox's messages (a read; None if not kept)."""
        return self._e.get_raw(*self._a(), message_id, actor=AGENT)

    def get_attachment(self, message_id: str, attachment_id: str, *,
                       max_bytes: int = MAX_ATTACHMENT_READ_BYTES) -> tuple[AttachmentRef, bytes]:  # fmt: skip
        """One attachment of one of this mailbox's messages: (metadata, bytes). A read; an
        attachment larger than `max_bytes` is UNSUPPORTED_OPERATION (bytes never loaded)."""
        return self._e.get_attachment(*self._a(), message_id, attachment_id, max_bytes=max_bytes,
                                      actor=AGENT)  # fmt: skip

    def find_by_message_id(self, header: str) -> list[CanonicalMessage]:
        """This mailbox's non-draft messages with that Message-ID header (a read)."""
        return self._e.find_by_message_id(*self._a(), header, actor=AGENT)

    def mark_seen(self, message_id: str) -> bool:
        """SEEN as a side effect of reading (recorded as a `read`, never budgeted)."""
        return self._e.mark_seen(*self._a(), message_id, actor=AGENT)

    def mailbox_info(self) -> dict[str, Any]:
        """{mailbox_id, email, run_id, virtual_time} of this agent's own mailbox (a read)."""
        return self._e.mailbox_info(*self._a(), actor=AGENT)

    def forward(self, message_id: str, *, to: list[str], text: str = "",
                cc: list[str] | None = None) -> dict[str, Any]:  # fmt: skip
        return _agent_send_result(self._e.forward(*self._a(), message_id, to=to, text=text, cc=cc,
                                                  actor=AGENT))  # fmt: skip

    def insert_message(self, raw: bytes, *, folder: str = "inbox") -> dict[str, Any]:
        return self._e.insert_message(*self._a(), raw, folder=folder, actor=AGENT)

    def create_draft(self, *, to: list[str], subject: str, text: str = "",
                     cc: list[str] | None = None, bcc: list[str] | None = None,
                     html: str | None = None, in_reply_to: str | None = None,
                     references: list[str] | None = None) -> dict[str, Any]:  # fmt: skip
        return self._e.create_draft(*self._a(), to=to, subject=subject, text=text, cc=cc, bcc=bcc,
                                    html=html, in_reply_to=in_reply_to, references=references,
                                    actor=AGENT)  # fmt: skip

    def update_draft(self, message_id: str, *, to: list[str], subject: str, text: str = "",
                     cc: list[str] | None = None, bcc: list[str] | None = None,
                     html: str | None = None, keep_attachments: bool = True) -> dict[str, Any]:  # fmt: skip
        return self._e.update_draft(*self._a(), message_id, to=to, subject=subject, text=text,
                                    cc=cc, bcc=bcc, html=html, keep_attachments=keep_attachments,
                                    actor=AGENT)  # fmt: skip

    def send_draft(self, message_id: str) -> dict[str, Any]:
        return _agent_send_result(self._e.send_draft(*self._a(), message_id, actor=AGENT))

    def move(self, message_id: str, folder: str) -> None:
        self._e.move(*self._a(), message_id, folder, actor=AGENT)

    def copy(self, message_id: str, folder: str) -> dict[str, Any]:
        return self._e.copy(*self._a(), message_id, folder, actor=AGENT)

    def trash(self, message_id: str) -> None:
        self._e.trash(*self._a(), message_id, actor=AGENT)

    def untrash(self, message_id: str) -> None:
        self._e.untrash(*self._a(), message_id, actor=AGENT)

    def modify_labels(
        self, message_id: str, *, add: Sequence[str] = (), remove: Sequence[str] = ()
    ) -> None:
        self._e.modify_labels(*self._a(), message_id, add=add, remove=remove, actor=AGENT)

    def set_flags(
        self, message_id: str, *, add: Sequence[str] = (), remove: Sequence[str] = ()
    ) -> None:
        self._e.set_flags(*self._a(), message_id, add=add, remove=remove, actor=AGENT)

    def delete(self, message_id: str) -> None:
        self._e.delete(*self._a(), message_id, actor=AGENT)

    def expunge(self, folder: str) -> list[str]:
        return self._e.expunge(*self._a(), folder, actor=AGENT)


class Controller:
    """Test-controller operations (spec 7.2 / 8.2)."""

    def __init__(self, engine: SimEngine, scope: Scope) -> None:
        self._e, self.scope = engine, scope

    def create_run(self, scenario_version_id: str, *, checks: str | None = None,
                   agent_build: str | None = None) -> str:  # fmt: skip
        """`checks`: per-run override of whether hidden deterministic checks are applied
        post-run ("on"/"off"; None = answer-key default). Never visible to the agent."""
        return self._e.create_run(self.scope, scenario_version_id, checks=checks,
                                  agent_build=agent_build)  # fmt: skip

    def start_run(self, run_id: str) -> None:
        self._e.start_run(self.scope, run_id)

    def cancel_run(self, run_id: str) -> None:
        self._e.cancel_run(self.scope, run_id)

    def fail_run_with_error(self, run_id: str, reason: str) -> None:
        self._e.fail_run_with_error(self.scope, run_id, reason)

    def reset_run(self, run_id: str) -> int:
        return self._e.reset_run(self.scope, run_id)

    def replay_run(self, run_id: str) -> ReplayResult:
        return self._e.replay_run(self.scope, run_id)

    def inject(self, run_id: str, mailbox_id: str, message: dict[str, Any] | bytes, *,
               event_id: str | None = None) -> dict[str, Any]:  # fmt: skip
        return self._e.inject(self.scope, run_id, mailbox_id, message, event_id=event_id)

    def advance(self, run_id: str, seconds: float) -> dict[str, Any]:
        return self._e.advance(self.scope, run_id, seconds)

    def tick(self, run_id: str) -> dict[str, Any]:
        return self._e.tick(self.scope, run_id)

    def persona_reply(self, run_id: str, persona_id: str, *, to: list[str], subject: str,
                      text: str, generation: dict[str, Any],
                      in_reply_to_message_id: str | None = None) -> dict[str, Any]:  # fmt: skip
        """Persona runtime entry point (WS-G): deliver + record a scripted/LLM reply."""
        return self._e.persona_reply(self.scope, run_id, persona_id, to=to, subject=subject,
                                     text=text, generation=generation,
                                     in_reply_to_message_id=in_reply_to_message_id)  # fmt: skip

    def persona_failed(self, run_id: str, persona_id: str, *, in_reply_to_message_id: str,
                       reason: str, generation: dict[str, Any] | None = None) -> None:  # fmt: skip
        self._e.persona_failed(self.scope, run_id, persona_id,
                               in_reply_to_message_id=in_reply_to_message_id, reason=reason,
                               generation=generation)  # fmt: skip

    def agent_mailbox_owner(self, run_id: str) -> str:
        return self._e.agent_mailbox_owner(self.scope, run_id)

    def persona_generations(self, run_id: str) -> list[dict[str, Any]]:
        return self._e.persona_generations(self.scope, run_id)

    def mailbox_id_for(self, run_id: str, persona_id: str) -> str:
        return self._e.mailbox_id_for(self.scope, run_id, persona_id)

    def run_info(self, run_id: str) -> dict[str, Any]:
        return self._e.run_info(self.scope, run_id)

    def actions(self, run_id: str) -> list[dict[str, Any]]:
        return self._e.actions(self.scope, run_id)

    def actions_page(self, run_id: str, *, after_id: int = 0,
                     limit: int = 100) -> list[dict[str, Any]]:  # fmt: skip
        return self._e.actions_page(self.scope, run_id, after_id=after_id, limit=limit)

    def state(self, run_id: str) -> dict[str, Any]:
        """Canonical mailbox state (spec 7.2). Contains no answer key."""
        return self._e.state(self.scope, run_id)

    def list_mailboxes(self, run_id: str) -> list[dict[str, Any]]:
        return self._e.list_mailboxes(self.scope, run_id)

    def inspect_messages(self, run_id: str, mailbox_id: str, *, folder: str | None = None,
                         page_size: int = 50, cursor: str | None = None,
                         order: str = "asc") -> Page[CanonicalMessage]:  # fmt: skip
        """Operator mailbox read (records nothing; never an agent action)."""
        return self._e.inspect_messages(self.scope, run_id, mailbox_id, folder=folder,
                                        page_size=page_size, cursor=cursor, order=order)  # fmt: skip

    def inspect_attachment(self, run_id: str, mailbox_id: str, message_id: str,
                           attachment_id: str) -> tuple[AttachmentRef, bytes]:  # fmt: skip
        """Operator read of one attachment of any mailbox in the run (records nothing)."""
        return self._e.inspect_attachment(self.scope, run_id, mailbox_id, message_id,
                                          attachment_id)  # fmt: skip

    def events_page(self, run_id: str, *, after_sequence: int = 0, limit: int = 100,
                    event_type: str | None = None, actor: str | None = None,
                    epoch: int | None = None) -> tuple[list[dict[str, Any]], int | None]:  # fmt: skip
        return self._e.events_page(self.scope, run_id, after_sequence=after_sequence,
                                   limit=limit, event_type=event_type, actor=actor, epoch=epoch)  # fmt: skip


class EvaluatorView:
    """Evaluator access (spec 8.3): full state, trace, actions; completes runs with a verdict."""

    def __init__(self, engine: SimEngine, scope: Scope) -> None:
        self._e, self.scope = engine, scope

    def run_info(self, run_id: str) -> dict[str, Any]:
        return self._e.run_info(self.scope, run_id)

    def state(self, run_id: str) -> dict[str, Any]:
        return self._e.state(self.scope, run_id)

    def state_hash(self, run_id: str) -> str:
        return self._e.state_hash(self.scope, run_id)

    def events(self, run_id: str) -> list[dict[str, Any]]:
        return self._e.events(self.scope, run_id)

    def actions(self, run_id: str) -> list[dict[str, Any]]:
        return self._e.actions(self.scope, run_id)

    def actions_page(self, run_id: str, *, after_id: int = 0,
                     limit: int = 100) -> list[dict[str, Any]]:  # fmt: skip
        return self._e.actions_page(self.scope, run_id, after_id=after_id, limit=limit)

    def scenario_spec(self, run_id: str) -> dict[str, Any]:
        return self._e.scenario_spec(self.scope, run_id)

    def list_mailboxes(self, run_id: str) -> list[dict[str, Any]]:
        return self._e.list_mailboxes(self.scope, run_id)

    def inspect_messages(self, run_id: str, mailbox_id: str, *, folder: str | None = None,
                         page_size: int = 50, cursor: str | None = None,
                         order: str = "asc") -> Page[CanonicalMessage]:  # fmt: skip
        """Operator mailbox read (records nothing; never an agent action)."""
        return self._e.inspect_messages(self.scope, run_id, mailbox_id, folder=folder,
                                        page_size=page_size, cursor=cursor, order=order)  # fmt: skip

    def inspect_attachment(self, run_id: str, mailbox_id: str, message_id: str,
                           attachment_id: str) -> tuple[AttachmentRef, bytes]:  # fmt: skip
        """Operator read of one attachment of any mailbox in the run (records nothing)."""
        return self._e.inspect_attachment(self.scope, run_id, mailbox_id, message_id,
                                          attachment_id)  # fmt: skip

    def events_page(self, run_id: str, *, after_sequence: int = 0, limit: int = 100,
                    event_type: str | None = None, actor: str | None = None,
                    epoch: int | None = None) -> tuple[list[dict[str, Any]], int | None]:  # fmt: skip
        return self._e.events_page(self.scope, run_id, after_sequence=after_sequence,
                                   limit=limit, event_type=event_type, actor=actor, epoch=epoch)  # fmt: skip

    def answer_key(self, run_id: str, version: int | None = None) -> dict[str, Any]:
        return self._e.answer_key_for_run(self.scope, run_id, version)

    def state_at(self, run_id: str, up_to_sequence: int) -> dict[str, Any]:
        return self._e.state_at(self.scope, run_id, up_to_sequence)

    def complete_run(self, run_id: str, verdict: str, *, evaluation_id: str,
                     evaluated_through_sequence: int) -> None:  # fmt: skip
        self._e.complete_run(self.scope, run_id, verdict, evaluation_id=evaluation_id,
                             evaluated_through_sequence=evaluated_through_sequence, actor=EVALUATOR)  # fmt: skip


Facade = AgentMailbox | Controller | EvaluatorView


def harness_parts(facade: Facade) -> tuple[SimEngine, Scope]:
    """TEST / HARNESS ONLY: the engine and scope behind a facade, so contract-test drivers can
    host an interface (REST, Gmail, ...) on the same canonical state. Interface code must never
    call this (enforced by tests/integration/test_safety.py)."""
    return facade._e, facade.scope if not isinstance(facade, AgentMailbox) else facade._scope


def facade_for(engine: SimEngine, principal: Principal) -> Facade:
    """Build the only facade a credential's role allows."""
    scope = Scope(principal.tenant_id, principal.project_id)
    role = principal.role
    if role == Role.AGENT:
        if not principal.run_id or not principal.mailbox_id:
            raise PermissionDenied("agent credentials must be bound to a run and a mailbox")
        return AgentMailbox(engine, scope, principal.run_id, principal.mailbox_id)
    if role == Role.CONTROLLER:
        return Controller(engine, scope)
    if role == Role.EVALUATOR:
        return EvaluatorView(engine, scope)
    raise PermissionDenied(f"unknown role {role!r}")  # fail closed, never the most privileged


__all__ = ["AgentMailbox", "CONTROLLER", "Controller", "EvaluatorView", "Facade", "facade_for"]
