"""Unit-level edges: partial responses, label mapping, MIME rendering, history mapping and the
remaining service paths."""

from __future__ import annotations

import base64
from datetime import UTC, datetime
from email.message import EmailMessage

import pytest

from mailsim.adapters.gmail import labels as lb
from mailsim.adapters.gmail import service as svc
from mailsim.adapters.gmail.fields import apply_fields, parse_fields, select
from mailsim.adapters.gmail.resources import message_resource
from mailsim.auth.principal import Principal
from mailsim.domain.errors import InvalidRequest, NotFound
from mailsim.domain.models import Address, AttachmentRef, CanonicalMessage
from tests.adapters.gmail.conftest import INVOICE, PO, b64, raw_message

NOW = datetime(2026, 10, 15, tzinfo=UTC)


def msg(**kw) -> CanonicalMessage:
    base = dict(id="m1", mailbox_id="mb", message_id_header="<a@x>", thread_id="t", folder="INBOX",
                from_=Address(email="a@x.test"), created_at=NOW, labels=["INBOX", "UNREAD"])  # fmt: skip
    base.update(kw)
    return CanonicalMessage(**base)


# ------------------------------------------------------------------------------- fields


@pytest.mark.parametrize(("spec", "tree"), [
    ("id", {"id": None}),
    ("id, threadId ", {"id": None, "threadId": None}),
    ("a/b/c", {"a": {"b": {"c": None}}}),
    ("a(b,c/d)", {"a": {"b": None, "c": {"d": None}}}),
    ("a/b,a/c", {"a": {"b": None, "c": None}}),
    ("a/b,a", {"a": None}), ("a,a/b", {"a": None}),
    ("*", {"*": None}),
])  # fmt: skip
def test_parse_fields(spec, tree):
    assert parse_fields(spec) == tree


@pytest.mark.parametrize("spec", ["", ",", "a,", "a/", "a/,b", "(a)", "a(b", "a)b", "a b", "a!"])
def test_bad_fields(spec):
    with pytest.raises(InvalidRequest):
        parse_fields(spec)


def test_apply_fields():
    data = {"messages": [{"id": 1, "x": 2}, {"id": 3}], "n": 4, "s": "v"}
    assert apply_fields(data, parse_fields("messages/id,s/zz,missing")) == {
        "messages": [{"id": 1}, {"id": 3}], "s": "v"}  # fmt: skip
    assert select(data, None) is data and select(data, "  ") is data
    assert select(data, "*") == data


# ------------------------------------------------------------------------------- labels


def test_label_mapping_edges():
    assert lb.user_label_name(lb.user_label_id("Ünïcode/Sub")) == "Ünïcode/Sub"
    with pytest.raises(NotFound):
        lb.user_label_name("Label_ff")  # not UTF-8
    assert lb.check_label_id("CATEGORY_NEW") == "unmodelled"
    odd = msg(folder="Custom", labels=["Mine", "UNREAD"])  # no folder marker first
    assert lb.folder_type(odd) == "mine" and lb.user_labels(odd) == ["Mine"]
    assert lb.user_labels(msg(folder="Custom", labels=[])) == []
    draft = msg(folder="Drafts", labels=["DRAFTS"], is_draft=True)
    assert lb.message_label_ids(draft) == ["DRAFT"]
    assert lb.system_label("MAILSIM_ANSWERED")["labelListVisibility"] == "labelHide"
    reg = lb.LabelRegistry()
    reg.drop(("r", "m"), "absent")
    reg.put(("r", "m"), "A", {"color": 1}, 5)
    assert reg.since(("r", "m")) == 5 and reg.since(("x", "y")) is None
    assert reg.get(("r", "m")) == {"A": {"color": 1}} and reg.get(("x", "y")) == {}


# ------------------------------------------------------------------------------ resources


def test_full_payload_with_nested_message_and_unmatched_part():
    inner = EmailMessage()
    inner["Subject"] = "inner"
    inner.set_content("inner body")
    outer = EmailMessage()
    outer["From"], outer["Subject"] = "a@x.test", "outer"
    outer.set_content("outer body")
    outer.add_attachment(inner)  # message/rfc822 part
    outer.add_attachment(b"\x89PNG", maintype="image", subtype="png", filename="p.png")
    outer.add_attachment(b"", maintype="application", subtype="octet-stream", filename="empty.bin")
    raw = outer.as_bytes()
    from mailsim.mime.parse import parse_mime

    parsed = parse_mime(raw)
    import hashlib

    refs = [AttachmentRef(id=f"att{i}", filename=a.filename, mime_type=a.mime_type,
                          size=len(a.content), checksum=hashlib.sha256(a.content).hexdigest(),
                          blob_ref="b") for i, a in enumerate(parsed.attachments[:1])]  # fmt: skip
    res = message_resource(msg(attachments=refs, body_text=None, body_html="<b>x</b>"), raw,
                           "full", "7")  # fmt: skip
    body, nested, png, empty = res["payload"]["parts"]
    assert nested["mimeType"] == "message/rfc822" and "attachmentId" in nested["body"]
    assert base64.urlsafe_b64decode(png["body"]["data"]) == b"\x89PNG"  # no canonical match
    assert empty["body"] == {"size": 0}
    assert res["snippet"] == "x" and res["historyId"] == "7"


def test_history_record_mapping():
    m = msg()
    rec, kinds = svc.history_record(
        {"sequence": 5, "type": "message.delivered", "message_id": "m1"}, None
    )
    assert kinds == {"messageAdded"} and "threadId" not in rec["messages"][0]
    rec, kinds = svc.history_record(
        {"sequence": 6, "type": "something.else", "message_id": "m1"}, m
    )
    assert kinds == set() and rec["messages"][0]["threadId"]
    assert svc.page_size(None) == svc.DEFAULT_PAGE and svc.page_size(10**6) == svc.MAX_PAGE


# ------------------------------------------------------------------------- service (db)


@pytest.mark.db
def test_service_facts_and_raw_fallback(gm):
    from mailsim.engine.facades import AgentMailbox

    p = Principal(gm.env.scope.tenant_id, gm.env.scope.project_id, "agent", gm.run, "mbx_nope")
    other = AgentMailbox(gm.env.engine, gm.env.scope, gm.run, "mbx_nope")
    s = svc.GmailService(other, p, gm.env.blobs, lb.LabelRegistry())
    with pytest.raises(NotFound):
        s.email()
    p = Principal(gm.env.scope.tenant_id, gm.env.scope.project_id, "agent", gm.run, gm.mailbox_id)
    s = svc.GmailService(gm.mb, p, gm.env.blobs, lb.LabelRegistry())
    assert s.email() == "finance@example.test" and s.now().tzinfo is not None
    m = s.message(gm.gid(INVOICE)).model_copy(update={"raw_blob_ref": None})
    assert b"Subject: Invoice INV-001" in s.raw(m)


@pytest.mark.db
def test_paged_listing_and_relative_dates(gm, monkeypatch):
    monkeypatch.setattr(svc, "MAX_PAGE", 1)
    assert gm.get("/profile")["messagesTotal"] == 2
    assert gm.ids(q="newer_than:1d") == [gm.gid(PO), gm.gid(INVOICE)]  # virtual now, not wall
    assert gm.ids(q="older_than:1d") == []
    gm.ctl.advance(gm.run, 3 * 86400)
    assert {gm.gid(PO), gm.gid(INVOICE)} <= set(gm.ids(q="older_than:2d"))


@pytest.mark.db
def test_reply_with_other_recipients_is_one_reply(gm):
    gm.inject(From="vendor@example.test", To="finance@example.test", Subject="Ping",
              **{"Message-ID": "<ping@example.test>"})  # fmt: skip
    raw = raw_message({"To": "finance@example.test", "Subject": "Re: Ping",
                       "In-Reply-To": "<ping@example.test>"})  # fmt: skip
    gm.post("/messages/send", json={"raw": b64(raw)})
    ops = [a for a in gm.actions() if a[0] != "read"]
    assert ("reply", "ok") in ops and ("draft", "ok") not in ops
