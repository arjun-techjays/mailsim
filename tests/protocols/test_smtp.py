"""SMTP submission listener driven with the stdlib `smtplib` client over loopback."""

from __future__ import annotations

import smtplib
from email import policy
from email.message import EmailMessage

import pytest

from mailsim.protocols import smtp as smtp_mod
from tests.protocols.conftest import AGENT, VENDOR, client_tls, open_agent

pytestmark = pytest.mark.db


def message(to=(VENDOR,), cc=(), subject="hello", text="body text", **headers) -> bytes:
    msg = EmailMessage()
    msg["From"] = AGENT
    if to:
        msg["To"] = ", ".join(to)
    if cc:
        msg["Cc"] = ", ".join(cc)
    msg["Subject"] = subject
    for k, v in headers.items():
        msg[k.replace("_", "-")] = v
    msg.set_content(text)
    return msg.as_bytes(policy=policy.SMTP)


def vendor_inbox(p):
    return [m for m in p.ev.state(p.run)["messages"]
            if m["mailbox_id"] == p.ctl.mailbox_id_for(p.run, "vendor")]  # fmt: skip


def test_ehlo_capabilities(proto):
    s = proto.smtp(login=False)
    assert s.esmtp_features["size"] == "26214400"
    assert "8bitmime" in s.esmtp_features
    assert set(s.esmtp_features["auth"].split()) == {"PLAIN", "LOGIN"}
    assert "starttls" not in s.esmtp_features


def test_auth_is_required_before_mail(proto):
    s = proto.smtp(login=False)
    assert s.mail(AGENT) == (530, b"5.7.0 Authentication required")


@pytest.mark.parametrize("mechanism", ["PLAIN", "LOGIN"])
def test_auth_mechanisms(proto, mechanism):
    s = proto.smtp(login=False)
    s.user, s.password = AGENT, proto.key
    code, _ = s.auth(mechanism, getattr(s, f"auth_{mechanism.lower()}"))
    assert code == 235


@pytest.mark.parametrize("case", ["bad-key", "wrong-user", "other-mailbox"])
def test_auth_failures(proto, case):
    s = proto.smtp(login=False)
    user, key = AGENT, proto.key
    if case == "bad-key":
        key = "msk_000000000000_" + "A" * 43
    elif case == "wrong-user":
        user = VENDOR
    else:
        user, key = VENDOR, proto.key_for("vendor")
    with pytest.raises(smtplib.SMTPAuthenticationError) as ei:
        s.login(user, key)
    assert ei.value.smtp_code == 535
    assert (b"PERMISSION_DENIED" in ei.value.smtp_error) == (case == "other-mailbox")


def test_mail_from_must_be_the_authenticated_mailbox(proto):
    s = proto.smtp()
    code, text = s.mail(VENDOR)
    assert code == 553 and b"not the authenticated mailbox" in text


def test_send_to_an_internal_recipient(proto):
    s = proto.smtp()
    s.mail(AGENT)
    assert s.rcpt(VENDOR) == (250, b"2.1.5 OK")
    code, text = s.data(message(subject="smtp hello"))
    assert code == 250 and text.startswith(b"2.0.0 OK queued as <")
    header = text.split()[4].decode()
    assert [m["message_id_header"] for m in vendor_inbox(proto)] == [header]
    sent = proto.mailbox.list_messages(folder="sent").items
    assert [m.subject for m in sent] == ["smtp hello"]
    assert ("agent", "send", "ok") in proto.actions()


def test_partial_recipient_rejection(proto):
    s = proto.smtp()
    refused = s.sendmail(AGENT, [VENDOR, "boss@real-company.example"],
                         message(to=(VENDOR, "boss@real-company.example")))  # fmt: skip
    code, text = refused["boss@real-company.example"]
    assert code == 550 and text.endswith(b"relay not permitted")
    assert len(vendor_inbox(proto)) == 1
    types = {e["type"] for e in proto.ev.events(proto.run)}
    assert not types & {"delivery.external_attempt", "delivery.sunk"}


def test_all_recipients_rejected(proto):
    s = proto.smtp()
    with pytest.raises(smtplib.SMTPRecipientsRefused):
        s.sendmail(AGENT, ["x@gmail.com"], message(to=("x@gmail.com",)))


def test_sink_mode_accepts_external_recipients_without_delivering(make_proto):
    def sink(s):
        open_agent(s)
        s["agent"]["prohibited_operations"] = []
        s["environment"]["delivery"]["external_delivery"] = "sink"

    p = make_proto(sink)
    s = p.smtp()
    s.mail(AGENT)
    code, text = s.rcpt("x@gmail.com")
    assert (code, text) == (250, b"2.1.5 OK")  # the delivery mode is not revealed
    assert s.data(message(to=("x@gmail.com",)))[0] == 250
    assert "delivery.sunk" in {e["type"] for e in p.ev.events(p.run)}


def test_recipient_syntax_and_limits(make_proto, monkeypatch):
    monkeypatch.setattr(smtp_mod, "MAX_RECIPIENTS", 2)
    p = make_proto()
    s = p.smtp()
    s.mail(AGENT)
    assert s.rcpt("not-an-address")[0] == 501
    assert s.rcpt(VENDOR)[0] == 250
    assert s.rcpt(AGENT)[0] == 250
    assert s.rcpt("procurement@example.test")[0] == 452


def test_cc_bcc_and_html(proto):
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Cc"], msg["Subject"] = AGENT, VENDOR, AGENT, "multi"
    msg.set_content("plain part")
    msg.add_alternative("<p>html part</p>", subtype="html")
    s = proto.smtp()
    assert s.sendmail(AGENT, [VENDOR, AGENT], msg.as_bytes()) == {}
    sent = proto.mailbox.list_messages(folder="sent").items[0]
    assert [a.email for a in sent.to] == [VENDOR] and [a.email for a in sent.cc] == [AGENT]
    assert sent.body_html == "<p>html part</p>\n"
    # an envelope recipient missing from To/Cc is a Bcc recipient
    assert s.sendmail(AGENT, [VENDOR, AGENT], message(subject="blind")) == {}
    blind = next(m for m in proto.mailbox.list_messages(folder="sent").items
                 if m.subject == "blind")  # fmt: skip
    assert [a.email for a in blind.to] == [VENDOR] and [a.email for a in blind.bcc] == [AGENT]


@pytest.mark.parametrize(
    ("raw", "code", "needle"),
    [
        (b"\r\n", 554, b"INVALID_REQUEST"),
        (b"From: finance@example.test\r\nTo: vendor@example.test\r\nSubject: s\r\n"
         b"MIME-Version: 1.0\r\nContent-Type: multipart/mixed; boundary=b\r\n\r\n--b\r\n"
         b"Content-Type: text/plain\r\n\r\nhi\r\n--b\r\nContent-Type: application/pdf\r\n"
         b"Content-Disposition: attachment; filename=a.pdf\r\n\r\nPDF\r\n--b--\r\n",
         554, b"attachments are not accepted"),
        (b"From: finance@example.test\r\nTo: vendor@example.test\r\nSubject: s\r\n"
         b"MIME-Version: 1.0\r\nContent-Type: multipart/mixed; boundary=b\r\n\r\n--b\r\n"
         b"Content-Type: application/x-msdownload\r\n"
         b"Content-Disposition: attachment; filename=a.exe\r\n\r\nMZ\r\n--b--\r\n",
         554, b"attachment type not allowed"),
    ],
)  # fmt: skip
def test_rejected_content(proto, raw, code, needle):
    s = proto.smtp()
    s.mail(AGENT)
    s.rcpt(VENDOR)
    got, text = s.data(raw)
    assert got == code and needle in text


def test_size_limit(proto, monkeypatch):
    s = proto.smtp()
    s.mail(AGENT)
    s.rcpt(VENDOR)
    big = message(text="x" * 100) + (b"y" * 998 + b"\r\n") * (26 * 1024 + 100)
    code, _ = s.data(big)
    assert code == 552


def test_reply_uses_the_engine_reply(proto):
    orig = proto.mailbox.list_messages(folder="inbox").items[0]  # from vendor
    s = proto.smtp()
    refused = s.sendmail(AGENT, [VENDOR], message(subject="Re: Invoice INV-001", text="ack",
                         In_Reply_To=orig.message_id_header))  # fmt: skip
    assert refused == {}
    acts = proto.actions()
    assert ("agent", "reply", "ok") in acts and ("agent", "send", "ok") not in acts
    assert "ANSWERED" in proto.mailbox.get_message(orig.id).flags
    sent = proto.mailbox.list_messages(folder="sent").items[0]
    assert sent.in_reply_to == orig.message_id_header and sent.thread_id == orig.thread_id


def test_reply_keeps_the_clients_recipients_and_resolves_references(proto):
    orig = proto.mailbox.list_messages(folder="inbox").items[0]
    s = proto.smtp()
    refs = f"<unknown@example.test> {orig.message_id_header}"  # newest entry resolves
    assert s.sendmail(AGENT, [VENDOR, AGENT],
                      message(to=(VENDOR,), cc=(AGENT,), subject="custom subject",
                              References=refs)) == {}  # fmt: skip
    acts = [a for a in proto.actions() if a[0] == "agent" and a[1] != "read"]
    assert acts == [("agent", "reply", "ok")]
    sent = proto.mailbox.list_messages(folder="sent").items[0]
    assert sent.subject == "custom subject" and sent.in_reply_to == orig.message_id_header
    assert [a.email for a in sent.to] == [VENDOR] and [a.email for a in sent.cc] == [AGENT]


def test_unresolvable_reply_headers_use_a_draft(proto):
    s = proto.smtp()
    assert s.sendmail(AGENT, [VENDOR], message(In_Reply_To="<nope@example.test>")) == {}
    assert s.sendmail(AGENT, [VENDOR], message(References="<gone@example.test>")) == {}
    ops = [a[1] for a in proto.actions() if a[0] == "agent"]
    assert ops.count("draft") == 2 and ops.count("send") == 2
    sent = proto.mailbox.list_messages(folder="sent").items
    assert {m.in_reply_to for m in sent} == {"<nope@example.test>", None}
    assert proto.mailbox.list_messages(folder="drafts").items == []


def test_finance_scenario_html_reply_is_one_reply(make_proto):
    p = make_proto(None)  # the example scenario: reply allowed, send not
    orig = p.mailbox.list_messages(folder="inbox").items[0]
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = AGENT, VENDOR, "Verifying the bank details"
    msg["In-Reply-To"] = orig.message_id_header
    msg.set_content("We will verify the bank details independently.")
    msg.add_alternative("<p>We will verify the bank details independently.</p>", subtype="html")
    s = p.smtp()
    assert s.sendmail(AGENT, [VENDOR], msg.as_bytes(policy=policy.SMTP)) == {}
    writes = [a for a in p.actions() if a[0] == "agent" and a[1] != "read"]
    assert writes == [("agent", "reply", "ok")]
    assert "ANSWERED" in p.mailbox.get_message(orig.id).flags
    sent = p.mailbox.list_messages(folder="sent").items[0]
    assert sent.subject == "Verifying the bank details" and sent.body_html
    assert "event.armed" in {e["type"] for e in p.ev.events(p.run)}


def test_engine_permission_errors_map_to_smtp_codes(make_proto):
    def reply_only(s):
        s["agent"]["allowed_operations"] = ["read", "search", "reply", "move"]

    p = make_proto(reply_only)
    s = p.smtp()
    s.mail(AGENT)
    s.rcpt(VENDOR)
    code, text = s.data(message())
    assert code == 550 and b"PERMISSION_DENIED" in text
    # a reply to the inbox message is permitted in the same scenario
    orig = p.mailbox.list_messages(folder="inbox").items[0]
    assert s.sendmail(AGENT, [VENDOR], message(In_Reply_To=orig.message_id_header)) == {}


def test_budget_exhaustion(make_proto):
    def tiny(s):
        open_agent(s)
        s["evaluation"]["limits"]["max_agent_actions"] = 1

    p = make_proto(tiny)
    s = p.smtp()
    assert s.sendmail(AGENT, [VENDOR], message()) == {}
    s.mail(AGENT)
    s.rcpt(VENDOR)
    code, text = s.data(message())
    assert (code, text) == (452, b"4.5.3 quota exceeded")


def test_starttls(make_proto):
    p = make_proto(tls=True)
    s = p.smtp(login=False)
    assert "starttls" in s.esmtp_features
    assert s.starttls(context=client_tls())[0] == 220
    s.ehlo()
    s.login(AGENT, p.key)
    assert s.sendmail(AGENT, [VENDOR], message()) == {}


def test_error_mapping_defaults():
    from mailsim.domain.errors import Conflict, UpstreamUnavailable
    from mailsim.protocols.smtp import smtp_error

    assert smtp_error(Conflict("x\ny")).startswith("451 4.3.0 CONFLICT: x y")
    assert smtp_error(UpstreamUnavailable("down")).startswith("451 4.3.0 UPSTREAM_UNAVAILABLE")
