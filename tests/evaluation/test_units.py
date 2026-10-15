"""Edge cases of the evaluator machinery on synthetic recordings (no database)."""

from __future__ import annotations

from typing import Any

import pytest

from mailsim.domain.errors import InvalidRequest
from mailsim.evaluation import registry
from mailsim.evaluation.checks import _common as c
from mailsim.evaluation.contract import Status, Verdict
from mailsim.evaluation.engine import evaluate_run, read_consistent
from mailsim.evaluation.registry import EvaluationContext
from mailsim.evaluation.reports import render
from mailsim.scenario.answer_key import AnswerAssertion, MessageWhere


def ev(seq: int, type_: str, *, actor: str = "simulator", operation: str = "x",
       outcome: str = "ok", payload: dict[str, Any] | None = None,
       vt: str | None = None) -> dict[str, Any]:  # fmt: skip
    return {"sequence": seq, "epoch": 0, "type": type_, "actor": actor, "operation": operation,
            "outcome": outcome, "payload": payload or {}, "virtual_time": vt}  # fmt: skip


def act(actor: str, op: str, outcome: str = "ok", result: dict[str, Any] | None = None) -> dict:
    return {"actor": actor, "operation": op, "input": {}, "result": result or {},
            "outcome": outcome}  # fmt: skip


def ctx(events: list[dict[str, Any]], actions: list[dict[str, Any]] | None = None,
        state: dict[str, Any] | None = None, **extras: Any) -> EvaluationContext:  # fmt: skip
    calls: list[int] = []

    def state_at(seq: int) -> dict[str, Any]:
        calls.append(seq)
        return {"seq": seq}

    out = EvaluationContext(run={}, scenario={}, final_state=state or {}, events=events,
                            actions=actions or [], agent_mailbox_id="mbx_a", persona_mailboxes={},
                            state_at=state_at, judge=None, extras=dict(extras))  # fmt: skip
    out.extras["calls"] = calls
    return out


def assertion(type_: str, check: dict[str, Any] | None = None) -> AnswerAssertion:
    return AnswerAssertion(id="x", type=type_, requirement="r", check=check)


def test_counted_matrix():
    assert c.counted("agent", "reply", "ok", {})
    assert c.counted("agent", "reply", "NOT_FOUND", {"message": "message m not found"})
    assert not c.counted("agent", "reply", "NOT_FOUND", {"message": "mailbox m not found"})
    assert not c.counted("agent", "read", "ok", {})
    assert not c.counted("agent", "reply", "PERMISSION_DENIED", {})
    assert not c.counted("controller", "advance", "ok", {})


def test_anchoring_unmatched_and_failed_actions():
    events = [ev(1, "run.created", actor="controller", operation="create_run"),
              ev(2, "message.sent", actor="agent", operation="reply"),
              ev(3, "agent.action_counted", actor="agent", operation="reply")]  # fmt: skip
    actions = [act("controller", "create_run"), act("controller", "start", "CONFLICT"),
               act("agent", "reply", "PERMISSION_DENIED"), act("agent", "reply"),
               act("controller", "start")]  # fmt: skip
    idx = c.RunIndex(events, actions)
    created, failed, denied, reply, start = idx.occurrences
    assert (created.first_seq, created.last_seq) == (1, 1)
    assert failed.last_seq is None and failed.before_seq == 1 and failed.seq == 1
    assert denied.last_seq is None and denied.evidence()[0].ref == "#3"
    assert (reply.first_seq, reply.last_seq, reply.before_seq) == (2, 3, 1)
    assert start.last_seq is None and start.before_seq == 3
    assert c.RunIndex([], []).last_seq == 0


def test_time_and_state_caches():
    cx = ctx([ev(1, "run.created"), ev(3, "clock.set", vt="2026-10-15T09:00:00Z")])
    idx = c.index(cx)
    assert c.index(cx) is idx
    assert idx.time_at(1) is None and idx.time_at(2) is None
    assert idx.time_at(4) is not None
    assert c.state_at(cx, 2) == c.state_at(cx, 2) == {"seq": 2}
    assert cx.extras["calls"] == [2]


def test_temporal_without_virtual_time():
    events = [ev(1, "event.fired", payload={"id": "e1"}),
              ev(2, "event.fired", payload={"id": "e2"})]  # fmt: skip
    plugin = registry.get("temporal")
    assert plugin is not None
    o = plugin.evaluate(
        ctx(events),
        assertion(
            "temporal", {"first": {"event": "e1"}, "then": {"event": "e2"}, "within_seconds": 0}
        ),
    )
    assert o.status is Status.PASS and o.diff.actual["first_time"] is None
    assert o.diff.actual["then_time"] is None and o.diff.actual["gap_seconds"] == 0
    off = plugin.evaluate(ctx(events, checks_enabled=False),
                          assertion("temporal", {"first": {"event": "e1"},
                                                 "then": {"event": "e2"}}))  # fmt: skip
    assert off.status is Status.NOT_APPLICABLE


def test_message_selection_filters():
    state = {
        "mailboxes": [{"id": "mbx_a", "owner_id": "me"}],
        "folders": [{"id": "f1", "mailbox_id": "mbx_a", "system_type": "inbox"}],
        "attachments": [{"message_id": "m1"}],
        "messages": [
            {"id": "m1", "mailbox_id": "mbx_a", "message_id_header": "<1>", "folder_id": "f1",
             "flags": ["SEEN"], "labels": [], "from": {"email": "a@x.test"}},
            {"id": "m2", "mailbox_id": "mbx_a", "message_id_header": "<2>", "folder_id": "gone",
             "flags": [], "labels": [], "from": {"email": "b@x.test", "name": "Bee"}},
        ],
    }  # fmt: skip

    def ids(**where: Any) -> list[str]:
        sel = c.select_messages(state, MessageWhere.model_validate(where),
                                agent_mailbox_id="mbx_a", agent_created=set())  # fmt: skip
        return [m["id"] for m in sel.messages]

    assert ids(lacks_flag="seen") == ["m2"]
    assert ids(has_attachment=False) == ["m2"]
    assert ids(has_attachment=True) == ["m1"]
    assert ids(folder="inbox") == ["m1"]
    assert ids(from_="bee") == ["m2"]
    assert ids(mailbox="me", has_flag="SEEN") == ["m1"]
    assert c.count_ok(c.Count(max=1), 2) is False and c.count_ok(c.Count(), 0) is True


class FakeView:
    def __init__(self, events: list[dict[str, Any]], *, grow: bool = False,
                 key: dict[str, Any] | None = None) -> None:  # fmt: skip
        self._events, self._grow, self.n = events, grow, 0
        self._key = key or {"answer_key_version": "1.0", "answer_key": {
            "scenario": "s", "version": 1, "assertions": [
                {"id": "st", "type": "state", "requirement": "r",
                 "check": {"where": {}, "count": {"min": 0}}}]}}  # fmt: skip
        self.state_at_calls: list[int] = []

    def run_info(self, run_id: str) -> dict[str, Any]:
        return {"id": run_id, "status": "RUNNING", "epoch": 0}

    def state(self, run_id: str) -> dict[str, Any]:
        return {"run": {"status": "RUNNING", "epoch": 0}, "mailboxes": [], "messages": []}

    def events(self, run_id: str) -> list[dict[str, Any]]:
        self.n += 1
        if self._grow:
            return [*self._events, *(ev(100 + i, "noise") for i in range(self.n))]
        return list(self._events)

    def actions(self, run_id: str) -> list[dict[str, Any]]:
        return []

    def scenario_spec(self, run_id: str) -> dict[str, Any]:
        return {"scenario_version_id": "sv_1", "version": 3, "spec": {"scenario": {"id": "s"}}}

    def answer_key(self, run_id: str, version: int | None = None) -> dict[str, Any]:
        return {"id": "akv_1", "version": 1, "spec_hash": "h", "spec": self._key,
                "source": "file"}  # fmt: skip

    def state_at(self, run_id: str, up_to_sequence: int) -> dict[str, Any]:
        self.state_at_calls.append(up_to_sequence)
        return self.state(run_id)


def test_read_consistent_falls_back_to_a_pinned_rebuild():
    view = FakeView([ev(1, "clock.set")], grow=True)
    events, _state, _actions = read_consistent(view, "run_1")
    assert view.state_at_calls == [events[-1]["sequence"]]


def test_engine_tolerates_missing_and_broken_plugins(monkeypatch):
    key = {"answer_key_version": "1.0", "answer_key": {"scenario": "s", "version": 1,
           "assertions": [{"id": "st", "type": "state", "requirement": "r",
                           "check": {"where": {}, "count": {"min": 0}}},
                          {"id": "ac", "type": "action", "requirement": "r",
                           "check": {"operation": "reply", "count": {"min": 0}}}]}}  # fmt: skip
    view = FakeView([ev(1, "clock.set", vt="2026-10-15T09:00:00Z")], key=key)

    class Boom:
        type = "action"

        def evaluate(self, ctx: Any, a: Any) -> Any:
            raise RuntimeError("kaput")

    monkeypatch.setitem(registry._REGISTRY, "action", Boom())
    monkeypatch.delitem(registry._REGISTRY, "state")
    r = evaluate_run(view, "run_1", evaluation_id="evl_1")
    st, ac = r.outcomes
    assert st.status is Status.INCONCLUSIVE and "no evaluator registered" in st.reason
    assert ac.status is Status.INCONCLUSIVE and ac.reason == "evaluator error: RuntimeError: kaput"
    assert r.verdict is Verdict.INCONCLUSIVE and r.agent_build is None
    assert r.reproducibility["seed"] is None and r.scenario_version == 3
    assert r.efficiency.virtual_duration_seconds == 0


def test_duration_needs_stamped_events():
    view = FakeView([ev(1, "run.status_changed", payload={"to": "RUNNING"}), ev(2, "x")])
    r = evaluate_run(view, "run_1", evaluation_id="evl_1")
    assert r.efficiency.virtual_duration_seconds == 0 and r.verdict is Verdict.PASSED


def test_unknown_report_format():
    view = FakeView([ev(1, "clock.set")])
    report = evaluate_run(view, "run_1", evaluation_id="evl_1")
    with pytest.raises(InvalidRequest):
        render(report, "pdf")
