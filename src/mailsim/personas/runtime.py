"""Persona runtime: scripted and LLM persona replies, delivered and recorded for replay.

``PersonaRuntime(controller, gateway).step(run_id)`` is idempotent. It reads the run state
through the Controller facade, finds messages delivered to persona mailboxes (never the
agent's mailbox, ``Controller.agent_mailbox_owner``) whose response delay has elapsed in
VIRTUAL time and that have no recorded outcome yet, and answers each through
``Controller.persona_reply`` with generation provenance. Replay consumes the recorded
``persona.generated`` events and never calls a model; the runtime is not involved in replay.

Caller model: one scheduler loop per run calls ``step()`` (e.g. after every clock advance /
agent action). Concurrent callers are still safe: the engine records at most one outcome
per (epoch, persona, message) under the run lock, and a duplicate attempt surfaces as
``Conflict``, which the runtime treats as "already answered".

Bookkeeping comes from ``Controller.persona_generations`` (current epoch): a message is
done once it has a ``persona.generated`` (reply) or ``persona.generation_failed`` record, and
a scripted rule with ``once: true`` is consumed by any recorded reply with its rule_index.

Per-message error isolation:
- non-retryable failures (refusal, truncated/empty/malformed output, rejected request) are
  recorded with ``Controller.persona_failed`` and never retried;
- ``SimulationLimitExceeded`` (LLM budget) stops LLM personas for the rest of this step;
  scripted personas continue;
- retryable failures (``RATE_LIMITED``: timeouts, 429/5xx) skip the message for this step;
- ``RUN_NOT_ACTIVE`` ends the step.

Loop guards: personas answer only senders with a simulated mailbox (never outsiders); an
LLM persona never answers another LLM persona; a message is answered only if its thread
(in the persona's mailbox) contains a message that was NOT persona-runtime generated - i.e.
sent by the agent, seeded, injected or scheduled by the scenario - and the thread is at
most ``MAX_THREAD_DEPTH`` messages deep.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from mailsim.domain.clock import parse_ts
from mailsim.domain.errors import (
    Conflict,
    RateLimited,
    RunNotActive,
    SimError,
    SimulationLimitExceeded,
)
from mailsim.domain.states import RunStatus
from mailsim.engine.facades import Controller
from mailsim.llm.gateway import LLMGateway
from mailsim.personas.prompt import build_persona_request
from mailsim.scenario.schema import BehaviorSpec, ScriptRule

log = logging.getLogger("mailsim.personas")
MAX_THREAD_DEPTH = 20
Msg = dict[str, Any]


def reply_subject(subject: str) -> str:
    s = (subject or "").strip()
    return s if s.lower().startswith("re:") else f"Re: {s}".rstrip()


def _ts(msg: Msg) -> datetime:
    return parse_ts(msg.get("received_at") or msg["created_at"])


def _key(msg: Msg) -> tuple[datetime, str]:
    return _ts(msg), msg["id"]


def _sender(msg: Msg) -> str:
    return str((msg.get("from") or {}).get("email", "")).lower()


def rule_matches(rule: ScriptRule, msg: Msg, emails: dict[str, str]) -> bool:
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


class _StepState:
    """Facts shared by all personas during one step."""

    def __init__(self, state: dict[str, Any], generations: list[dict[str, Any]]) -> None:
        personas = sorted(state.get("personas", []), key=lambda p: p["id"])
        self.personas = personas
        self.emails = {p["id"]: str(p["email"]).lower() for p in personas}
        self.behaviors = {p["id"]: BehaviorSpec.model_validate(p.get("response_policy") or {})
                          for p in personas}  # fmt: skip
        self.llm_emails = {self.emails[p] for p, b in self.behaviors.items() if b.mode == "llm"}
        self.mailboxes = {mb["owner_id"]: mb["id"] for mb in state.get("mailboxes", [])}
        self.reachable = {str(mb["email"]).lower() for mb in state.get("mailboxes", [])}
        self.messages = [x for x in state.get("messages", []) if not x.get("is_draft")]
        self.generations = generations
        # (sender email, subject, in_reply_to) of every runtime-authored message
        self.generated_sigs = {
            (self.emails.get(g["persona_id"], ""), g.get("subject"), g.get("in_reply_to"))
            for g in generations if g["type"] == "persona.generated"
        }  # fmt: skip
        self.llm_stopped = False

    def runtime_authored(self, msg: Msg) -> bool:
        return (_sender(msg), msg.get("subject"), msg.get("in_reply_to")) in self.generated_sigs


class PersonaRuntime:
    def __init__(self, controller: Controller, gateway: LLMGateway) -> None:
        self.controller = controller
        self.gateway = gateway

    def step(self, run_id: str) -> list[dict[str, Any]]:
        """Answer every due message once; returns what was delivered in this step."""
        state = self.controller.state(run_id)
        run = state["run"]
        if run["status"] != RunStatus.RUNNING or not run.get("virtual_time"):
            return []
        now = parse_ts(run["virtual_time"])
        agent = self.controller.agent_mailbox_owner(run_id)
        st = _StepState(state, self.controller.persona_generations(run_id))
        done: list[dict[str, Any]] = []
        try:
            for persona in st.personas:
                pid, behavior = persona["id"], st.behaviors[persona["id"]]
                if pid == agent or behavior.mode == "silent" or pid not in st.mailboxes:
                    continue
                done += self._persona_step(run_id, persona, behavior, st, now)
        except RunNotActive:
            log.info("persona step stopped: run %s is no longer active", run_id)
        return done

    def _persona_step(self, run_id: str, persona: dict[str, Any], behavior: BehaviorSpec,
                      st: _StepState, now: datetime) -> list[dict[str, Any]]:  # fmt: skip
        pid, me = persona["id"], st.emails[persona["id"]]
        own = [x for x in st.messages if x["mailbox_id"] == st.mailboxes[pid]
               and (_sender(x) in st.reachable or _sender(x) == me)]  # fmt: skip
        mine = [g for g in st.generations if g["persona_id"] == pid]
        answered = {g.get("in_reply_to") for g in mine if g["type"] == "persona.generated"}
        failed = {g.get("message_id") for g in mine if g["type"] == "persona.generation_failed"}
        consumed = {
            (g.get("generation") or {}).get("rule_index") for g in mine
            if g["type"] == "persona.generated"
            and (g.get("generation") or {}).get("mode") == "scripted"
        }  # fmt: skip
        out: list[dict[str, Any]] = []
        for msg in sorted((x for x in own if _sender(x) != me), key=_key):
            if msg["message_id_header"] in answered or msg["id"] in failed:
                continue
            rule_index: int | None = None
            if behavior.mode == "scripted":
                rule_index = next(
                    (i for i, r in enumerate(behavior.script)
                     if not (r.once and i in consumed) and rule_matches(r, msg, st.emails)), None,
                )  # fmt: skip
                if rule_index is None:
                    continue
                rule = behavior.script[rule_index]
                if rule.once:  # reserved by the earliest pending message, even if not yet due
                    consumed.add(rule_index)
                delay = rule.delay_seconds
                if delay is None:
                    delay = behavior.response_delay_seconds
            else:  # llm
                if st.llm_stopped or _sender(msg) in st.llm_emails:
                    continue
                delay = behavior.response_delay_seconds
            if _ts(msg) + timedelta(seconds=delay) > now:
                continue
            thread = sorted((x for x in own if x["thread_id"] == msg["thread_id"]
                             and _key(x) <= _key(msg)), key=_key)  # fmt: skip
            if len(thread) > MAX_THREAD_DEPTH or all(st.runtime_authored(x) for x in thread):
                continue
            result = self._attempt(run_id, persona, behavior, msg, thread, st, rule_index)
            if result is not None:
                out.append(result)
        return out

    def _attempt(self, run_id: str, persona: dict[str, Any], behavior: BehaviorSpec, msg: Msg,
                 thread: list[Msg], st: _StepState,
                 rule_index: int | None) -> dict[str, Any] | None:  # fmt: skip
        """Run one reply with per-message error isolation."""
        mode = behavior.mode
        try:
            if rule_index is not None:
                rule = behavior.script[rule_index]
                return self._scripted(run_id, persona, rule, rule_index, msg)
            return self._llm(run_id, persona, behavior, thread, msg)
        except RunNotActive:
            raise  # ends the whole step
        except Conflict:
            return None  # another runtime already recorded an outcome for this message
        except SimulationLimitExceeded:
            st.llm_stopped = True
            log.warning("persona LLM budget exhausted for run %s", run_id)
            return None
        except RateLimited as exc:
            log.warning("persona %s retryable failure on %s: %s", persona["id"], msg["id"],
                        exc.code)  # fmt: skip
            return None
        except SimError as exc:
            generation: dict[str, Any] = {"mode": mode}
            if rule_index is not None:
                generation["rule_index"] = rule_index
            if exc.details.get("llm_call_id"):
                generation["llm_call_id"] = exc.details["llm_call_id"]
            reason = f"{exc.code}: {exc.details.get('kind') or exc.message}"
            self.controller.persona_failed(run_id, persona["id"],
                                           in_reply_to_message_id=msg["id"], reason=reason,
                                           generation=generation)  # fmt: skip
            log.warning("persona %s failed on %s: %s", persona["id"], msg["id"], reason)
            return None

    def _deliver(self, run_id: str, persona: dict[str, Any], msg: Msg, subject: str, text: str,
                 generation: dict[str, Any]) -> dict[str, Any]:  # fmt: skip
        res = self.controller.persona_reply(
            run_id, persona["id"], to=[_sender(msg)], subject=subject, text=text,
            generation=generation, in_reply_to_message_id=msg["id"],
        )  # fmt: skip
        return {"persona_id": persona["id"], "in_reply_to_message_id": msg["id"],
                "mode": generation["mode"], "subject": subject, "result": res}  # fmt: skip

    def _scripted(self, run_id: str, persona: dict[str, Any], rule: ScriptRule, index: int,
                  msg: Msg) -> dict[str, Any]:  # fmt: skip
        subject = rule.reply.subject or reply_subject(msg.get("subject") or "")
        return self._deliver(run_id, persona, msg, subject, rule.reply.body,
                             {"mode": "scripted", "rule_index": index})  # fmt: skip

    def _llm(self, run_id: str, persona: dict[str, Any], behavior: BehaviorSpec,
             thread: list[Msg], msg: Msg) -> dict[str, Any]:  # fmt: skip
        request = build_persona_request(persona, behavior.response_style, thread, run_id)
        result = self.gateway.call(request)  # empty / truncated output raises (gateway)
        generation = {
            "mode": "llm", "provider": result.response.provider, "model": result.response.model,
            "prompt_hash": result.prompt_hash, "input_tokens": result.response.input_tokens,
            "output_tokens": result.response.output_tokens, "llm_call_id": result.llm_call_id,
        }  # fmt: skip
        return self._deliver(run_id, persona, msg, reply_subject(msg.get("subject") or ""),
                             result.response.text.strip(), generation)  # fmt: skip
