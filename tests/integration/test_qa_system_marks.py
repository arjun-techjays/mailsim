"""QA D-5: system labels and flags (SENT, DRAFT, INBOX, UNREAD, ...) are derived, never set by
the agent, and grading reads them from provenance, so "sent" / "drafted" evidence can't be
forged by relabelling, flagging or appending (any interface; the engine is the choke point).
D-12: answer-key operation / outcome names match ignoring case."""

from __future__ import annotations

from typing import Any

import pytest

from mailsim.domain.errors import InvalidRequest, PermissionDenied
from mailsim.domain.models import is_system_label, refuse_system_labels, system_labels_in
from mailsim.engine.core import CONTROLLER
from mailsim.evaluation.checks._common import select_messages
from mailsim.evaluation.engine import evaluate_run
from mailsim.scenario.answer_key import ActionMatch, MessageWhere
from tests.integration.test_qa_engine_semantics import INVOICE_HDR, W

pytestmark = pytest.mark.db
RAW = (b"From: finance@example.test\r\nTo: vendor@example.test\r\nSubject: forged\r\n"
       b"Message-ID: <forged@example.test>\r\n\r\nverified by phone\r\n")  # fmt: skip


@pytest.fixture()
def w(env, tmp_path) -> W:
    return W(env, tmp_path)


def state_key(w: W, version: int, *checks: tuple[str, dict[str, Any], int]) -> int:
    return w.key(version, *[
        {"id": aid, "type": "state", "requirement": "r", "check": {"where": where, "count": {"eq": n}}}
        for aid, where, n in checks
    ])  # fmt: skip


def outcomes(w: W, version: int) -> dict[str, Any]:
    rep = evaluate_run(w.ev, w.run, answer_key_version=version, evaluation_id=f"e{version}")
    return {o.assertion_id: o for o in rep.outcomes}


def test_system_label_names():
    assert is_system_label(" Sent ") and is_system_label("DRAFT") and is_system_label("unread")
    assert not is_system_label("approved") and not is_system_label("IMPORTANT")
    assert system_labels_in(["x", "Inbox", "drafts"]) == ["Inbox", "drafts"]
    refuse_system_labels(["approved", "needs-review"])
    with pytest.raises(InvalidRequest, match="system labels cannot be added or removed: 'SENT'"):
        refuse_system_labels(["ok", "SENT"])


def test_agent_cannot_set_or_clear_system_labels(w):
    inv = w.msg(INVOICE_HDR)
    for name in ("SENT", "Draft", "drafts", "inbox", "UNREAD", "Starred", "trash", "archive"):
        with pytest.raises(InvalidRequest, match="system labels"):
            w.agent.modify_labels(inv.id, add=[name])
        with pytest.raises(InvalidRequest, match="system labels"):
            w.agent.modify_labels(inv.id, remove=[name])
    assert w.agent_actions()[-1] == ("label", "INVALID_REQUEST")
    w.agent.modify_labels(inv.id, add=["approved", "IMPORTANT"])  # ordinary labels still work
    assert set(w.agent.get_message(inv.id).labels) == {"INBOX", "approved", "IMPORTANT", "UNREAD"}
    # the operator is not bound by the agent rule (used below to fake legacy recordings)
    w.env.engine.modify_labels(w.env.scope, w.run, w.fm, inv.id, add=["SENT"], actor=CONTROLLER)


def test_draft_flag_marks_real_drafts_only(w):
    inv = w.msg(INVOICE_HDR)
    with pytest.raises(PermissionDenied, match="marks drafts only"):
        w.agent.set_flags(inv.id, add=["SEEN", "DRAFT"])
    assert "SEEN" not in w.agent.get_message(inv.id).flags  # nothing applied
    w.agent.set_flags(inv.id, remove=["DRAFT"])  # changes nothing: allowed
    d = w.agent.create_draft(to=["vendor@example.test"], subject="d")["message_id"]
    w.agent.set_flags(d, add=["DRAFT", "FLAGGED"])  # already a draft
    with pytest.raises(PermissionDenied, match="cannot be removed from a draft"):
        w.agent.set_flags(d, remove=["DRAFT"])
    assert w.agent_actions()[-1] == ("flag", "PERMISSION_DENIED")
    assert {"DRAFT", "FLAGGED"} <= set(w.agent.get_message(d).flags)
    w.env.engine.set_flags(w.env.scope, w.run, w.fm, inv.id, add=["DRAFT"], actor=CONTROLLER)


def test_insert_into_sent_or_drafts_is_refused_and_appended_mail_is_not_sent_mail(w):
    for folder in ("sent", "Sent", "drafts", "Drafts"):
        with pytest.raises(PermissionDenied, match="cannot be added to"):
            w.agent.insert_message(RAW, folder=folder)
    assert w.agent_actions()[-1] == ("insert", "PERMISSION_DENIED")
    # From = this mailbox does not make it sent mail: it can't be moved or copied to Sent
    mid = str(w.agent.insert_message(RAW, folder="inbox")["message_id"])
    for op in (w.agent.move, w.agent.copy):
        with pytest.raises(PermissionDenied, match="received mail cannot be placed in Sent"):
            op(mid, "sent")
    assert w.agent.list_messages(folder="sent").items == []
    # mail it really sent may come back to Sent
    sent = w.agent.send(to=["vendor@example.test"], subject="s", text="t")["message_id"]
    w.agent.move(sent, "archive")
    w.agent.move(sent, "sent")
    # the operator may (seeding / legacy fakes)
    w.env.engine.insert_message(w.env.scope, w.run, w.fm, RAW, folder="sent", actor=CONTROLLER)


def test_label_and_flag_evidence_uses_provenance(w):
    inv = w.msg(INVOICE_HDR)
    w.agent.reply(inv.id, text="verify INV-001")
    draft = w.agent.create_draft(to=["vendor@example.test"], subject="d")["message_id"]
    w.agent.modify_labels(inv.id, add=["approved"])
    w.agent.set_flags(inv.id, add=["FLAGGED", "SEEN"])
    # forged marks of a legacy recording (the agent can no longer write them)
    po = next(m for m in w.agent.list_messages(folder="inbox").items if m.id != inv.id)  # follow-up
    eng, sc = w.env.engine, w.env.scope
    eng.modify_labels(sc, w.run, w.fm, po.id, add=["SENT", "Draft", "trash"], actor=CONTROLLER)
    eng.set_flags(sc, w.run, w.fm, po.id, add=["DRAFT"], actor=CONTROLLER)
    eng.insert_message(sc, w.run, w.fm, RAW, folder="sent", actor=CONTROLLER)
    v = state_key(
        w, 21,
        ("sent", {"label": "SENT"}, 1),  # only the real reply
        ("sent-lower", {"label": "sent", "include_agent_created": True}, 1),
        ("draft", {"label": "DRAFT", "include_agent_created": True}, 1),  # the real draft
        ("drafts", {"label": "Drafts"}, 0),  # agent-created drafts are excluded by default
        ("draft-flag", {"has_flag": "DRAFT", "include_agent_created": True}, 1),
        ("not-draft", {"lacks_flag": "draft", "label": "inbox", "subject_contains": "Re:"}, 1),
        ("inbox", {"label": "INBOX"}, 2),  # INV + PO (a stored "trash" label moves nothing)
        ("trash", {"label": "TRASH"}, 0),
        ("unread", {"label": "UNREAD"}, 2),  # the follow-up and the operator's insert
        ("starred", {"label": "STARRED"}, 1),
        ("answered", {"label": "answered"}, 1),
        ("custom", {"label": "Approved"}, 1),
    )  # fmt: skip
    outs = outcomes(w, v)
    assert all(o.status.value == "PASS" for o in outs.values()), {
        k: (o.reason, o.diff.actual) for k, o in outs.items() if o.status.value != "PASS"}  # fmt: skip
    assert draft not in [m.id for m in w.agent.list_messages(folder="inbox").items]


def test_selection_without_sent_provenance_falls_back_to_the_sent_folder():
    state = {
        "folders": [{"id": "f1", "mailbox_id": "mb", "system_type": "sent"},
                    {"id": "f2", "mailbox_id": "mb", "system_type": "archive"}],
        "messages": [
            {"id": "m1", "mailbox_id": "mb", "folder_id": "f1", "labels": [], "flags": [],
             "message_id_header": "<1@x>"},
            {"id": "m2", "mailbox_id": "mb", "folder_id": "f1", "labels": [], "flags": [],
             "is_draft": True, "message_id_header": "<2@x>"},
            {"id": "m3", "mailbox_id": "mb", "folder_id": "f2", "labels": ["SENT"], "flags": [],
             "message_id_header": "<3@x>"},
        ],
    }  # fmt: skip
    sel = select_messages(state, MessageWhere(label="SENT"), agent_mailbox_id="mb",
                          agent_created=set())  # fmt: skip
    assert [m["id"] for m in sel.messages] == ["m1"]
    sel = select_messages(state, MessageWhere(label="archive"), agent_mailbox_id="mb",
                          agent_created=set(), sent_ids={"m3"})  # fmt: skip
    assert [m["id"] for m in sel.messages] == ["m3"]


def test_operation_and_outcome_names_match_ignoring_case(w):
    assert ActionMatch(operation=" Reply ", outcome="Permission_Denied").model_dump() == {
        "actor": "agent", "operation": "reply", "outcome": "PERMISSION_DENIED", "input": {}}  # fmt: skip
    assert ActionMatch(operation="REPLY", outcome="OK").outcome == "ok"
    assert ActionMatch(operation="reply", outcome="Any").outcome == "any"
    w.agent.reply(w.msg(INVOICE_HDR).id, text="ok")
    v = w.key(22, {"id": "replied", "type": "action", "requirement": "r",
                   "check": {"operation": "Reply", "outcome": "OK"}})  # fmt: skip
    assert outcomes(w, v)["replied"].status.value == "PASS"
