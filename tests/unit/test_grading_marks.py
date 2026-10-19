"""QA D-5 grading units (no database): address and Message-ID normal forms shared by the engine
and grading, and ANSWERED provenance matching."""

from __future__ import annotations

from typing import Any

from mailsim.domain.models import MAX_LABEL_CHARS, MAX_LABELS_PER_CHANGE, normalize_address
from mailsim.evaluation.checks._common import Answered, RunIndex, select_messages
from mailsim.mime.parse import normalize_message_id
from mailsim.scenario.answer_key import MessageWhere
from mailsim.scenario.references import OPERATIONS
from mailsim.scenario.schema import AGENT_OPERATIONS


def test_shared_normal_forms():
    assert normalize_address("  Vendor@Example.TEST ") == "vendor@example.test"
    assert normalize_address(None) == ""
    assert normalize_message_id(" <a@b> ") == "<a@b>"
    assert normalize_message_id("a@b") == "<a@b>"
    assert normalize_message_id("junk <x@y> <z@w>") == "<x@y>"
    assert normalize_message_id("  ") is None and normalize_message_id("a b") is None
    assert normalize_message_id(None) is None
    assert (MAX_LABEL_CHARS, MAX_LABELS_PER_CHANGE) == (225, 50)


def test_sent_copy_is_gradable_but_not_a_scenario_permission():
    assert "sent_copy" in OPERATIONS["agent"] and "sent_copy" not in AGENT_OPERATIONS


def _event(seq: int, kind: str, msg: dict[str, Any] | None = None, actor: str = "agent"):
    return {
        "sequence": seq,
        "type": kind,
        "actor": actor,
        "payload": {"message": msg} if msg else {},
    }


def _msg(mid: str, sender: str, header: str, **kw: Any) -> dict[str, Any]:
    return {"id": mid, "mailbox_id": "mb", "folder_id": "f", "message_id_header": header,
            "from": {"email": sender}, "labels": [], "flags": ["ANSWERED"], **kw}  # fmt: skip


def test_answered_by_header_needs_the_sender_and_normalises_both_sides():
    events = [
        _event(1, "message.sent", {"id": "s1", "in_reply_to": "x@corp", "to": [{"email": "Me@corp"}]}),
        _event(2, "message.sent", {"id": "s2", "in_reply_to": " <y@corp> ",
                                   "to": [{"email": "OTHER@corp"}], "cc": [{"email": "bob@corp"}]}),
        _event(3, "message.sent", {"id": "s3", "in_reply_to": "<z@corp>",
                                   "bcc": [{"email": "help@corp"}]}),
        _event(4, "message.sent", {"id": "s4", "in_reply_to": "<w@corp>", "to": [{"email": "a@b"}]},
               actor="simulator"),
    ]  # fmt: skip
    answered = RunIndex(events, []).answered_at(4)
    assert isinstance(answered, Answered)
    state = {
        "folders": [{"id": "f", "mailbox_id": "mb", "system_type": "inbox"}],
        "messages": [
            _msg("m1", "boss@corp", "<x@corp>"),  # answered to me@corp only: not its sender
            _msg("m2", "Bob@Corp", "<y@corp>"),  # its sender in cc: answered
            _msg("m3", "noreply@corp", "<z@corp>", headers={"Reply-To": ["Help <help@corp>"]}),
            _msg("m4", "a@b", "<w@corp>"),  # answered by someone else (not the agent)
            _msg("m5", "bob@corp", "<y@corp>"),  # a repeated Message-ID, same sender: answered
            _msg("m6", "eve@corp", "<y@corp>"),  # repeated Message-ID, sender not addressed
        ],
    }
    sel = select_messages(state, MessageWhere(has_flag="ANSWERED"), agent_mailbox_id="mb",
                          agent_created=set(), answered=answered)  # fmt: skip
    assert [m["id"] for m in sel.messages] == ["m2", "m3", "m5"]
    plain = select_messages(state, MessageWhere(has_flag="ANSWERED"), agent_mailbox_id="mb",
                            agent_created=set())  # fmt: skip
    assert len(plain.messages) == 6  # without provenance the stored flag stands
