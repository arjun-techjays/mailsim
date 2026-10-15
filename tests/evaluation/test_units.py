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


def act(aid: int, actor: str, op: str, start: int, end: int, outcome: str = "ok",
        vt: str | None = None) -> dict[str, Any]:  # fmt: skip
    return {"id": aid, "actor": actor, "operation": op, "input": {}, "result": {},
            "outcome": outcome, "seq_start": start, "seq_end": end, "virtual_time": vt}  # fmt: skip


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


def test_occurrences_follow_recorded_windows():
    events = [ev(1, "run.created"), ev(2, "message.sent", actor="agent"),
              ev(3, "agent.action_counted", actor="agent"), ev(4, "run.reset"),
              ev(5, "seeded")]  # fmt: skip
    actions = [act(1, "controller", "create_run", 1, 1), act(2, "agent", "reply", 2, 3),
               act(3, "agent", "read", 4, 3), act(4, "controller", "reset", 4, 5),
               act(5, "agent", "move", 6, 5, "PERMISSION_DENIED"),
               {"id": 6, "actor": "agent", "operation": "read", "outcome": "ok"}]  # fmt: skip
    idx = c.RunIndex(events, actions)
    created, reply, read, reset, denied, legacy = idx.occurrences
    assert (reply.seq, reply.before_seq, reply.wrote_events) == (3, 1, True)
    assert (read.seq, read.wrote_events, read.evidence()[0].ref) == (3, False, "3")
    assert len(read.evidence()) == 1 and len(reply.evidence()) == 2
    assert denied.seq == 5 and denied.before_seq == 5
    assert legacy.seq == -1 and legacy.time is None
    assert [o.id for o in idx.occurrences_in(5)] == ["4", "5"]  # current epoch only
    assert c.action_position(actions[2]) == 3 and c.RunIndex([], []).last_seq == 0


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
    assert o.status is Status.INCONCLUSIVE and "no recorded virtual time" in o.reason
    assert [e.ref for e in o.evidence] == ["1", "2"]
    stamped = [ev(1, "event.fired", payload={"id": "e1"}, vt="2026-10-15T09:00:00Z")]
    acts = [act(1, "agent", "read", 2, 1),
            act(2, "agent", "read", 2, 1, vt="2026-10-15T09:00:05Z")]  # fmt: skip
    rd = {"first": {"event": "e1"}, "then": {"action": {"operation": "read"}}}
    o = plugin.evaluate(ctx(stamped, acts), assertion("temporal", rd))
    assert o.status is Status.INCONCLUSIVE  # the first read after e1 has no time
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
                 "check": {"where": {"mailbox": "me"}, "count": {"min": 0}}}]}}  # fmt: skip
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
        return [act(1, "agent", "read", 2, 1), act(2, "agent", "read", 10_000, 9_999)]

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
    events, _state, actions = read_consistent(view, "run_1")
    assert view.state_at_calls == [events[-1]["sequence"]]
    assert [x["id"] for x in actions] == [1]  # the action after the pinned sequence is cut
    _e, _s, actions = read_consistent(FakeView([ev(1, "clock.set")]), "run_1")
    assert [x["id"] for x in actions] == [1]


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


def test_nested_quantifier_detection():
    from mailsim.evaluation.checks.content import nested_quantifier

    for bad in (r"(\w+\s?)+:", r"(a+)+", r"(x{2,})*", r"((a)+b)+", r"(a*){3,}"):
        assert nested_quantifier(bad), bad
    for ok in (r"(ab)+", r"bank\s+details", r"([+*])+", r"(a{2})+", r"\(a+\)+", r"(?:a|b)+",
               r"a+)"):  # fmt: skip
        assert not nested_quantifier(ok), ok


@pytest.mark.parametrize("type_,check", [
    ("state", {"where": {}}),
    ("content", {"contains": ["x"]}),
    ("policy", {"forbid": {"operation": "send"}, "unless": {"where": {}}}),
])  # fmt: skip
def test_unknown_agent_mailbox_is_inconclusive(type_, check):
    cx = ctx([ev(1, "clock.set")])
    cx.agent_mailbox_id = ""
    plugin = registry.get(type_)
    assert plugin is not None
    o = plugin.evaluate(cx, assertion(type_, check))
    assert o.status is Status.INCONCLUSIVE and "mailbox is unknown" in o.reason


def test_markdown_cells_neutralise_links_and_entities():
    from mailsim.evaluation.reports.markdown import cell

    out = cell("see https://evil.example & www.evil.example <x>")
    assert "://" not in out and "www." not in out and "&amp;" in out and "\\<x\\>" in out
