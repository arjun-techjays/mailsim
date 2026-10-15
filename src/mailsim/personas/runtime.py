"""Persona runtime: scripted and LLM persona replies, delivered and recorded for replay.

``PersonaRuntime(controller, gateway).step(run_id)`` is idempotent. It reads the run state
through the Controller facade, finds messages delivered to persona mailboxes (never the
agent's mailbox) that have no persona reply yet and whose response delay has elapsed in
VIRTUAL time, and answers each through ``Controller.persona_reply`` with generation
provenance. Replay consumes the recorded ``persona.generated`` events and never calls a
model; the runtime is not involved in replay at all.

Dedupe and ``once`` bookkeeping are derived from state alone: a persona's reply leaves a
Sent copy in its own mailbox whose ``in_reply_to`` names the answered message, and rule
selection is re-derived deterministically over the persona's received messages in
(received_at, id) order, a rule with ``once: true`` being consumed by the first message it
is selected for. (Replies injected manually via ``Controller.persona_reply`` count as
answered but may shift that derivation.)

Personas answer only senders that have a simulated mailbox (never outsiders). Loop guard:
an LLM persona does not answer messages from another LLM persona.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from mailsim.domain.clock import parse_ts
from mailsim.domain.errors import InvalidRequest
from mailsim.domain.states import RunStatus
from mailsim.engine.facades import Controller
from mailsim.llm.gateway import LLMGateway, provider_error
from mailsim.personas.prompt import build_persona_request
from mailsim.scenario.schema import BehaviorSpec, ScriptRule

AgentOwnerResolver = Callable[[str], str]


def reply_subject(subject: str) -> str:
    s = (subject or "").strip()
    return s if s.lower().startswith("re:") else f"Re: {s}".rstrip()


def _ts(msg: dict[str, Any]) -> datetime:
    return parse_ts(msg.get("received_at") or msg["created_at"])


def _sender(msg: dict[str, Any]) -> str:
    return str((msg.get("from") or {}).get("email", "")).lower()


def rule_matches(rule: ScriptRule, msg: dict[str, Any], emails: dict[str, str]) -> bool:
    on = rule.on
    if on.from_ is not None and _sender(msg) != emails.get(on.from_, "\0"):
        return False
    if (
        on.subject_contains
        and on.subject_contains.lower() not in (msg.get("subject") or "").lower()
    ):
        return False
    body = msg.get("body_text") or msg.get("body_html") or ""
    return not (on.body_contains and on.body_contains.lower() not in body.lower())


class PersonaRuntime:
    def __init__(self, controller: Controller, gateway: LLMGateway, *,
                 agent_owner: AgentOwnerResolver | None = None) -> None:  # fmt: skip
        self.controller = controller
        self.gateway = gateway
        self._agent_owner = agent_owner

    def agent_owner(self, run_id: str) -> str:
        """Persona id whose mailbox the agent drives: it must never auto-reply."""
        if self._agent_owner is not None:
            return self._agent_owner(run_id)
        resolver = getattr(self.controller, "agent_mailbox_owner", None)
        if callable(resolver):
            return str(resolver(run_id))
        raise InvalidRequest(
            "persona runtime needs the agent mailbox owner (pass agent_owner=...); "
            "refusing to risk replying as the agent"
        )

    def step(self, run_id: str) -> list[dict[str, Any]]:
        state = self.controller.state(run_id)
        run = state["run"]
        if run["status"] != RunStatus.RUNNING or not run.get("virtual_time"):
            return []
        now = parse_ts(run["virtual_time"])
        agent = self.agent_owner(run_id)
        personas = sorted(state.get("personas", []), key=lambda p: p["id"])
        emails = {p["id"]: str(p["email"]).lower() for p in personas}
        behaviors = {p["id"]: BehaviorSpec.model_validate(p.get("response_policy") or {})
                     for p in personas}  # fmt: skip
        llm_emails = {emails[p] for p, b in behaviors.items() if b.mode == "llm"}
        mailboxes = {mb["owner_id"]: mb["id"] for mb in state.get("mailboxes", [])}
        # only simulated mailboxes are answered: a reply to an outsider would be blocked
        reachable = {str(mb["email"]).lower() for mb in state.get("mailboxes", [])}
        messages = [m for m in state.get("messages", []) if not m.get("is_draft")]
        done: list[dict[str, Any]] = []
        for persona in personas:
            pid, behavior = persona["id"], behaviors[persona["id"]]
            if pid == agent or behavior.mode == "silent" or pid not in mailboxes:
                continue
            own = [m for m in messages if m["mailbox_id"] == mailboxes[pid]
                   and (_sender(m) in reachable or _sender(m) == emails[pid])]  # fmt: skip
            done += self._persona_step(run_id, persona, behavior, own, emails, llm_emails, now)
        return done

    def _persona_step(self, run_id: str, persona: dict[str, Any], behavior: BehaviorSpec,
                      own: list[dict[str, Any]], emails: dict[str, str], llm_emails: set[str],
                      now: datetime) -> list[dict[str, Any]]:  # fmt: skip
        me = emails[persona["id"]]
        replied = {m["in_reply_to"] for m in own if _sender(m) == me and m.get("in_reply_to")}
        received = sorted((m for m in own if _sender(m) != me), key=lambda m: (_ts(m), m["id"]))
        consumed: set[int] = set()
        out: list[dict[str, Any]] = []
        for msg in received:
            rule_index: int | None = None
            if behavior.mode == "scripted":
                rule_index = next(
                    (i for i, r in enumerate(behavior.script)
                     if i not in consumed and rule_matches(r, msg, emails)), None,
                )  # fmt: skip
                if rule_index is None:
                    continue
                rule = behavior.script[rule_index]
                if rule.once:
                    consumed.add(rule_index)
                delay = rule.delay_seconds
                if delay is None:
                    delay = behavior.response_delay_seconds
            else:  # llm
                if _sender(msg) in llm_emails:
                    continue
                delay = behavior.response_delay_seconds
            if msg["message_id_header"] in replied or _ts(msg) + timedelta(seconds=delay) > now:
                continue
            if rule_index is not None:
                result = self._scripted(run_id, persona, behavior.script[rule_index],
                                        rule_index, msg)  # fmt: skip
            else:
                thread = [m for m in own if m["thread_id"] == msg["thread_id"]
                          and (_ts(m), m["id"]) <= (_ts(msg), msg["id"])]  # fmt: skip
                thread.sort(key=lambda m: (_ts(m), m["id"]))
                result = self._llm(run_id, persona, behavior, thread, msg)
            replied.add(msg["message_id_header"])
            out.append(result)
        return out

    def _deliver(self, run_id: str, persona: dict[str, Any], msg: dict[str, Any], subject: str,
                 text: str, generation: dict[str, Any]) -> dict[str, Any]:  # fmt: skip
        res = self.controller.persona_reply(
            run_id, persona["id"], to=[_sender(msg)], subject=subject, text=text,
            generation=generation, in_reply_to_message_id=msg["id"],
        )  # fmt: skip
        return {"persona_id": persona["id"], "in_reply_to_message_id": msg["id"],
                "mode": generation["mode"], "subject": subject, "result": res}  # fmt: skip

    def _scripted(self, run_id: str, persona: dict[str, Any], rule: ScriptRule, index: int,
                  msg: dict[str, Any]) -> dict[str, Any]:  # fmt: skip
        subject = rule.reply.subject or reply_subject(msg.get("subject") or "")
        return self._deliver(run_id, persona, msg, subject, rule.reply.body,
                             {"mode": "scripted", "rule_index": index})  # fmt: skip

    def _llm(self, run_id: str, persona: dict[str, Any], behavior: BehaviorSpec,
             thread: list[dict[str, Any]], msg: dict[str, Any]) -> dict[str, Any]:  # fmt: skip
        request = build_persona_request(persona, behavior.response_style, thread, run_id)
        result = self.gateway.call(request)
        text = result.response.text.strip()
        if not text:
            raise provider_error(
                result.response.provider, "empty_output", "persona reply is empty", retryable=False
            )
        generation = {
            "mode": "llm", "provider": result.response.provider, "model": result.response.model,
            "prompt_hash": result.prompt_hash, "input_tokens": result.response.input_tokens,
            "output_tokens": result.response.output_tokens, "llm_call_id": result.llm_call_id,
        }  # fmt: skip
        return self._deliver(run_id, persona, msg, reply_subject(msg.get("subject") or ""),
                             text, generation)  # fmt: skip
