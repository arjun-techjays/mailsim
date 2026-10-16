"""Attachment downloads (agent + operator), REST forward, display-name recipients and the
agent-facing send result (B-8, B-10, QA-A2)."""

from __future__ import annotations

import base64
from email.message import EmailMessage
from urllib.parse import quote

import pytest

from tests.api.conftest import open_agent

pytestmark = pytest.mark.db


def err(r, status, code):
    assert r.status_code == status, r.text
    assert r.json()["error"]["code"] == code
    return r.json()["error"]


@pytest.fixture()
def agent(api):
    api.publish(open_agent)
    created = api.create_run()
    key, mbx = api.agent(created)
    return created["run"]["id"], mbx, key


def inbox(api, mbx, key):
    url = f"/v1/mailboxes/{mbx}/messages?folder=inbox"
    return api.call("GET", url, key=key, expect=200).json()["items"]


def invoice(api, mbx, key):
    return next(m for m in inbox(api, mbx, key) if m["attachments"])


def inject_html_attachment(api, rid, mbx, filename):
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = "vendor@example.test", "finance@example.test", "page"
    msg["Message-ID"] = "<page-1@example.test>"
    msg.set_content("see attached")
    msg.add_attachment(b"<script>alert(1)</script>", maintype="text", subtype="html",
                       filename=filename)  # fmt: skip
    raw = base64.b64encode(msg.as_bytes()).decode()
    api.call("POST", f"/v1/runs/{rid}/mailboxes/{mbx}/inject", json={"raw_base64": raw},
             expect=201)  # fmt: skip


def assert_download_headers(r, *, filename_star):
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["content-security-policy"] == "sandbox"
    assert r.headers["cache-control"] == "private, no-store"
    disposition = r.headers["content-disposition"]
    assert disposition.startswith('attachment; filename="')
    assert disposition.endswith(f"filename*=UTF-8''{filename_star}")


def test_agent_downloads_attachment_bytes(api, agent):
    rid, mbx, key = agent
    inv = invoice(api, mbx, key)
    att = inv["attachments"][0]
    url = f"/v1/mailboxes/{mbx}/messages/{inv['id']}/attachments/{att['id']}"
    r = api.call("GET", url, key=key, expect=200)
    assert r.content.startswith(b"%PDF") and len(r.content) == att["size"]
    assert r.headers["content-type"] == "application/pdf"
    assert_download_headers(r, filename_star="INV-001.pdf")
    reads = [a for a in api.call("GET", f"/v1/runs/{rid}/actions", expect=200).json()
             if a["input"].get("attachment_id") == att["id"]]  # fmt: skip
    assert [(a["actor"], a["operation"]) for a in reads] == [("agent", "read")]
    # unknown attachment, an attachment id of another message, another mailbox: 404
    err(api.call("GET", f"{url}x", key=key), 404, "NOT_FOUND")
    other = next(m for m in inbox(api, mbx, key) if m["id"] != inv["id"])
    err(api.call("GET", f"/v1/mailboxes/{mbx}/messages/{other['id']}/attachments/{att['id']}",
                 key=key), 404, "NOT_FOUND")  # fmt: skip
    vendor = next(b["id"] for b in api.call("GET", f"/v1/runs/{rid}/mailboxes", expect=200).json()
                  if b["owner_id"] == "vendor")  # fmt: skip
    r = api.call("GET", f"/v1/mailboxes/{vendor}/messages/{inv['id']}/attachments/{att['id']}",
                 key=key)  # fmt: skip
    assert r.status_code == 403


def test_hostile_attachment_is_served_as_a_download(api, agent):
    rid, mbx, key = agent
    inject_html_attachment(api, rid, mbx, 'evil "x"\\/..;é.html')
    msg = next(m for m in inbox(api, mbx, key) if m["subject"] == "page")
    att = msg["attachments"][0]
    r = api.call("GET", f"/v1/mailboxes/{mbx}/messages/{msg['id']}/attachments/{att['id']}",
                 key=key, expect=200)  # fmt: skip
    assert r.content == b"<script>alert(1)</script>"
    assert r.headers["content-type"].startswith("text/html")  # stored type, but never rendered
    disposition = r.headers["content-disposition"]
    assert '"x"' not in disposition and "\\" not in disposition and "/" not in disposition
    assert att["filename"].endswith(".html") and "/" not in att["filename"]
    assert_download_headers(r, filename_star=quote(att["filename"], safe=""))


def test_operator_attachment_route_records_nothing_and_works_after_cancel(api, agent):
    rid, mbx, key = agent
    inv = invoice(api, mbx, key)
    att = inv["attachments"][0]
    url = f"/v1/runs/{rid}/mailboxes/{mbx}/messages/{inv['id']}/attachments/{att['id']}"
    before = api.call("GET", f"/v1/runs/{rid}/actions", expect=200).json()
    for role in ("controller", "evaluator"):
        r = api.call("GET", url, role=role, expect=200)
        assert r.content.startswith(b"%PDF") and r.headers["content-type"] == "application/pdf"
        assert_download_headers(r, filename_star="INV-001.pdf")
    assert api.call("GET", f"/v1/runs/{rid}/actions", expect=200).json() == before
    api.call("POST", f"/v1/runs/{rid}/cancel", expect=200)
    api.call("GET", url, expect=200)
    err(api.call("GET", f"/v1/runs/{rid}/mailboxes/mbx_nope/messages/{inv['id']}/attachments/"
                 f"{att['id']}"), 404, "NOT_FOUND")  # fmt: skip
    err(api.call("GET", f"{url}x"), 404, "NOT_FOUND")


def test_operator_attachment_route_is_tenant_scoped(api, other_api, agent):
    rid, mbx, key = agent
    inv = invoice(api, mbx, key)
    url = (f"/v1/runs/{rid}/mailboxes/{mbx}/messages/{inv['id']}/attachments/"
           f"{inv['attachments'][0]['id']}")  # fmt: skip
    err(other_api.call("GET", url), 404, "NOT_FOUND")


def test_forward_with_attachments_cc_and_display_names(api, agent):
    rid, mbx, key = agent
    inv = invoice(api, mbx, key)
    out = api.call("POST", f"/v1/mailboxes/{mbx}/messages/{inv['id']}/forward", key=key,
                   json={"to": ["Vendor Desk <Vendor@Example.test>"],
                         "cc": ["\"Me, Finance\" <finance@example.test>"], "text": "FYI"},
                   expect=201).json()  # fmt: skip
    assert set(out) == {"message_id", "message_id_header"}
    sent = api.call("GET", f"/v1/mailboxes/{mbx}/messages/{out['message_id']}", key=key,
                    expect=200).json()  # fmt: skip
    assert sent["subject"] == "Fwd: Invoice INV-001" and sent["body_text"].startswith("FYI")
    assert [a["email"] for a in sent["to"]] == ["vendor@example.test"]
    assert [a["email"] for a in sent["cc"]] == ["finance@example.test"]
    assert [a["filename"] for a in sent["attachments"]] == ["INV-001.pdf"]
    actions = api.call("GET", f"/v1/runs/{rid}/actions", expect=200).json()
    fwd = next(a for a in actions if a["operation"] == "forward")
    assert fwd["input"]["cc"] == ["finance@example.test"]
    assert fwd["result"]["external_mode"] == "deny"  # operators keep the delivery details
    url = f"/v1/mailboxes/{mbx}/messages/{inv['id']}/forward"
    err(api.call("POST", url, key=key, json={"to": []}), 400, "INVALID_REQUEST")
    body = err(api.call("POST", url, key=key, json={"to": ["ceo@real-company.example"]}),
               422, "EXTERNAL_DELIVERY_BLOCKED")  # fmt: skip
    assert body["details"]["recipients"] == ["ceo@real-company.example"]
    err(api.call("POST", url, key=key, json={"to": ["Nobody <>"]}), 400, "INVALID_REQUEST")


def test_send_accepts_display_names_and_hides_delivery_handling(api, agent):
    rid, mbx, key = agent
    out = api.call("POST", f"/v1/mailboxes/{mbx}/messages", key=key, expect=201,
                   json={"to": ["Vendor <vendor@example.test>, finance@example.test"],
                         "subject": "s", "text": "t"}).json()  # fmt: skip
    assert set(out) == {"message_id", "message_id_header"}
    sent = api.call("GET", f"/v1/mailboxes/{mbx}/messages/{out['message_id']}", key=key,
                    expect=200).json()  # fmt: skip
    assert [a["email"] for a in sent["to"]] == ["vendor@example.test", "finance@example.test"]


def test_sink_mode_is_invisible_to_the_agent(api, tmp_path):
    def sink(s):
        open_agent(s)
        s["agent"]["prohibited_operations"] = []
        s["environment"]["delivery"]["external_delivery"] = "sink"

    api.publish(sink)
    created = api.create_run()
    key, mbx = api.agent(created)
    rid = created["run"]["id"]
    first = inbox(api, mbx, key)[0]
    out = api.call("POST", f"/v1/mailboxes/{mbx}/messages", key=key, expect=201,
                   json={"to": ["ceo@real-company.example"], "subject": "s"}).json()  # fmt: skip
    fwd = api.call("POST", f"/v1/mailboxes/{mbx}/messages/{first['id']}/forward", key=key,
                   json={"to": ["ceo@real-company.example"]}, expect=201).json()  # fmt: skip
    for res in (out, fwd):
        assert set(res) == {"message_id", "message_id_header"}
    sunk = [e for e in api.call("GET", f"/v1/runs/{rid}/events?page_size=500", expect=200)
            .json()["items"] if e["type"] == "delivery.sunk"]  # fmt: skip
    assert len(sunk) == 2
