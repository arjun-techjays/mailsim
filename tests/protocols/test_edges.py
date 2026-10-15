"""Edge cases: paging through large folders, raw-MIME fallback, auth corner cases."""

from __future__ import annotations

import imaplib
from datetime import UTC, datetime

import pytest

from mailsim.api.auth import issue_key
from mailsim.domain.models import Address, CanonicalMessage
from mailsim.protocols import imap as imap_mod
from mailsim.protocols import smtp as smtp_mod
from mailsim.protocols.auth import Authenticator, AuthFailed
from mailsim.protocols.imap import ImapSession, user_labels
from mailsim.protocols.smtp import SandboxHandler
from tests.protocols.conftest import AGENT, VENDOR

pytestmark = pytest.mark.db


def canonical(i: int = 0, **kw) -> CanonicalMessage:
    data = {"id": f"msg_{i}", "mailbox_id": "mbx", "message_id_header": f"<m{i}@example.test>",
            "thread_id": "thr", "folder": "INBOX", "from": Address(email="a@example.test"),
            "to": [Address(email="b@example.test")], "subject": f"s{i}", "body_text": "hi",
            "created_at": datetime(2026, 10, 15, tzinfo=UTC), "imap_uid": i + 1}  # fmt: skip
    data.update(kw)
    return CanonicalMessage(**data)


def test_user_labels_and_rebuilt_raw():
    assert user_labels(canonical(labels=["custom", "UNREAD"])) == ["custom"]
    assert user_labels(canonical(labels=["UNREAD"], flags=["SEEN"])) == ["UNREAD"]
    session = ImapSession(Authenticator(None, session_factory=object()), blobs=None)  # type: ignore[arg-type]
    raw, root = session._raw(canonical())
    assert b"Message-ID: <m0@example.test>" in raw and root.body.startswith(b"hi")
    assert session._raw(canonical())[0] is raw  # cached
    for i in range(1, 70):
        session._raw(canonical(i))
    assert len(session._raw_cache) == 64


def test_folder_listing_pages(proto, monkeypatch):
    monkeypatch.setattr(imap_mod, "PAGE_SIZE", 1)
    monkeypatch.setattr(smtp_mod, "PAGE_SIZE", 1)
    c = proto.imap()
    assert c.select("INBOX")[1] == [b"2"]
    orig = proto.mailbox.list_messages(folder="inbox").items[1]
    s = proto.smtp()
    assert s.sendmail(AGENT, [VENDOR],
                      b"From: finance@example.test\r\nTo: vendor@example.test\r\n"
                      b"In-Reply-To: " + orig.message_id_header.encode() + b"\r\n"
                      b"Subject: Re\r\n\r\nok\r\n") == {}  # fmt: skip
    assert ("agent", "draft", "ok") in proto.actions()  # PO came from procurement@: no match


def test_key_for_a_missing_mailbox(proto):
    with proto.env.session_factory.begin() as s:
        key = issue_key(s, tenant_id=proto.env.scope.tenant_id,
                        project_id=proto.env.scope.project_id, role="agent", run_id=proto.run,
                        mailbox_id="mbx_missing").api_key  # fmt: skip
    with pytest.raises(AuthFailed, match="does not exist"):
        proto.servers.authenticator.login(AGENT, key)
    c = proto.imap(login=False)
    with pytest.raises(imaplib.IMAP4.error):
        c.login(AGENT, key)


def test_smtp_authenticator_rejects_unknown_auth_data(proto):
    handler = SandboxHandler(proto.servers.authenticator)
    result = handler.authenticate(None, None, None, "XOAUTH2", object())  # type: ignore[arg-type]
    assert result.success is False


def test_misc_imap_paths(proto):
    c = proto.raw()
    c.cmd(f"a LOGIN {AGENT} {proto.key}".encode())
    assert c.cmd(b"b STATUS INBOX")[-1].startswith(b"b BAD usage: STATUS")
    c.cmd(b"c SELECT INBOX")
    assert c.cmd(b"d UID MOVE 999 Archive") == [b"d OK UID MOVE completed\r\n"]
    assert c.cmd(b"e UID EXPUNGE 1:*") == [b"e OK UID EXPUNGE completed\r\n"]  # none \Deleted
    raw = (b"From: a@example.test\r\nX-Long: first\r\n  continued\r\nSubject: folded\r\n"
           b"  subject\r\nTo: finance@example.test\r\n\r\nx\r\n")  # fmt: skip
    proto.mailbox.insert_message(raw, folder="inbox")
    lines = b"".join(c.cmd(b"f FETCH 3 (BODY.PEEK[HEADER.FIELDS (SUBJECT)])"))
    assert b"Subject: folded\r\n  subject\r\n\r\n" in lines
    lines = c.cmd(b"g FETCH 3 (BODY.PEEK[HEADER.FIELDS.NOT (X-LONG FROM TO)])")[0]
    assert b"continued" not in lines and b"subject" in lines
    assert b'"folded  subject"' in c.cmd(b"h FETCH 3 ENVELOPE")[0]
