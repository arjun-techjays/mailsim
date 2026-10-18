"""MCP tools through the official client (in-process): per-role listing, every tool, error
mapping, and authorization enforced on each call (spec 8)."""

from __future__ import annotations

import base64

import anyio
import pytest
from mcp.shared.exceptions import MCPError
from mcp_types import INTERNAL_ERROR

from mailsim.api.auth import revoke_key
from mailsim.auth.principal import Principal, Role
from mailsim.domain.errors import UpstreamUnavailable
from mailsim.mcp import tools as t
from mailsim.mcp.server import McpService, build_server, tool_result
from mailsim.store import models as m
from mcp import Client
from tests.mcp.conftest import err, ok, tool_names

pytestmark = pytest.mark.db

AGENT_TOOLS = ["list_messages", "get_message", "get_thread", "search_messages", "get_attachment",
               "send_message", "reply_to_message", "forward_message", "manage_labels",
               "delete_message", "list_folders", "get_changes"]  # fmt: skip
CONTROL_TOOLS = ["inject_incoming_email", "advance_simulation", "trigger_scheduled_event",
                 "reset_run", "start_run", "cancel_run", "replay_run", "list_run_mailboxes",
                 "get_run_status", "get_run_trace"]  # fmt: skip
EVALUATOR_TOOLS = ["list_run_mailboxes", "get_run_status", "get_run_trace", "get_test_results"]


def test_tool_listing_is_filtered_by_role(world):
    assert tool_names(world.inproc("agent")) == AGENT_TOOLS
    assert tool_names(world.inproc("controller")) == CONTROL_TOOLS
    assert tool_names(world.inproc("evaluator")) == EVALUATOR_TOOLS
    assert {s.name for s in t.TOOLS} == set(AGENT_TOOLS + CONTROL_TOOLS + EVALUATOR_TOOLS)
    assert t.TOOLS_BY_NAME["get_run_trace"].roles() == {Role.CONTROLLER, Role.EVALUATOR}


def test_every_tool_has_an_explicit_closed_object_schema(world):
    async def go():
        async with world.inproc("controller")() as client:
            return (await client.list_tools()).tools

    for tool in anyio.run(go):
        schema = tool.input_schema
        assert schema["type"] == "object" and schema["additionalProperties"] is False, tool.name
        assert tool.description and tool.annotations.open_world_hint is False
    inject = t.TOOLS_BY_NAME["inject_incoming_email"].tool().input_schema
    assert "from" in inject["properties"] and "from_" not in inject["properties"]
    assert t.TOOLS_BY_NAME["list_folders"].tool().input_schema["properties"] == {}


def test_agent_reads(world):
    a = world.inproc("agent")
    inbox = ok(a, "list_messages", {"folder": "inbox"})
    assert len(inbox["items"]) == 2 and inbox["next_cursor"] is None  # before any injection
    assert {msg["mailbox_id"] for msg in inbox["items"]} == {world.agent_mb}
    first, second = inbox["items"]
    assert "from" in first and "from_" not in first
    desc = ok(a, "list_messages", {"folder": "inbox", "order": "desc"})["items"]
    assert [x["id"] for x in desc] == [second["id"], first["id"]]
    page = ok(a, "list_messages", {"page_size": 1})
    nxt = ok(a, "list_messages", {"page_size": 1, "cursor": page["next_cursor"]})
    assert page["items"][0]["id"] != nxt["items"][0]["id"]
    assert ok(a, "get_message", {"message_id": first["id"]})["id"] == first["id"]
    thread = ok(a, "get_thread", {"thread_id": first["thread_id"]})["items"]
    assert first["id"] in {x["id"] for x in thread}
    ok(world.inproc("controller"), "inject_incoming_email", {
        "run_id": world.run, "mailbox_id": world.agent_mb, "from": "vendor@example.test",
        "to": ["finance@example.test"], "subject": "Second invoice", "text": "invoice 2"})  # fmt: skip
    hits = ok(a, "search_messages", {"query": "invoice"})["items"]
    assert len(hits) > 1
    one = ok(a, "search_messages", {"query": "invoice", "page_size": 1})
    assert len(one["items"]) == 1 and one["next_cursor"]
    rest = ok(a, "search_messages", {"query": "invoice", "cursor": one["next_cursor"]})
    assert [x["id"] for x in one["items"] + rest["items"]] == [x["id"] for x in hits]
    assert ok(a, "search_messages", {"query": "invoice", "folder": "sent"})["items"] == []
    assert err(a, "search_messages", {"query": "x", "folder": "nope"})["code"] == "NOT_FOUND"
    assert err(a, "search_messages", {"query": "x", "page_size": 0})["code"] == "INVALID_REQUEST"
    folders = ok(a, "list_folders")["items"]
    assert {f["system_type"] for f in folders} >= {"inbox", "sent", "trash"}
    feed = ok(a, "get_changes", {"since": 0, "limit": 5})
    assert {"changes", "next_since", "has_more"} <= set(feed)
    assert err(a, "get_message", {"message_id": "msg_nope"})["code"] == "NOT_FOUND"


def test_agent_writes_and_external_delivery_policy(world):
    a = world.inproc("agent")
    first = ok(a, "list_messages", {"folder": "inbox"})["items"][0]
    sent = ok(a, "send_message", {"to": ["vendor@example.test"], "subject": "hi", "text": "x",
                                  "cc": [], "bcc": [], "html": "<p>x</p>"})  # fmt: skip
    assert sent["message_id_header"]
    blocked = err(a, "send_message", {"to": ["ceo@real-company.example"], "subject": "s"})
    assert blocked["code"] == "EXTERNAL_DELIVERY_BLOCKED"
    reply = ok(a, "reply_to_message", {"message_id": first["id"], "text": "ack",
                                       "reply_all": True})  # fmt: skip
    assert reply["message_id"]
    changed = ok(a, "manage_labels", {"message_id": first["id"], "folder": "archive",
                                      "add_labels": ["todo"], "add_flags": ["FLAGGED"]})  # fmt: skip
    assert changed["folder"] == "Archive" and "todo" in changed["labels"]
    assert "FLAGGED" in changed["flags"]
    undone = ok(a, "manage_labels", {"message_id": first["id"], "remove_labels": ["todo"],
                                     "remove_flags": ["FLAGGED"]})  # fmt: skip
    assert "todo" not in undone["labels"] and "FLAGGED" not in undone["flags"]
    assert err(a, "manage_labels", {"message_id": first["id"]})["code"] == "INVALID_REQUEST"
    missing = err(a, "manage_labels", {"message_id": first["id"], "folder": "nowhere"})
    assert missing["code"] == "NOT_FOUND"
    # QA D-5: system labels and the DRAFT flag are not agent-writable (no partial write)
    forged = err(a, "manage_labels", {"message_id": first["id"], "folder": "inbox",
                                      "add_labels": ["SENT"]})  # fmt: skip
    assert forged["code"] == "INVALID_REQUEST" and "system labels" in forged["message"]
    drafted = err(a, "manage_labels", {"message_id": first["id"], "add_flags": ["DRAFT"]})
    assert drafted["code"] == "PERMISSION_DENIED"
    assert ok(a, "get_message", {"message_id": first["id"]})["folder"] == "Archive"
    outcomes = [(x["operation"], x["outcome"]) for x in
                world.engine.actions(world.env.scope, world.run) if x["actor"] == "agent"]  # fmt: skip
    assert ("label", "INVALID_REQUEST") in outcomes and ("flag", "PERMISSION_DENIED") in outcomes
    assert ok(a, "delete_message", {"message_id": first["id"]}) == {"deleted": first["id"]}
    actions = world.engine.actions(world.env.scope, world.run)
    assert {("agent", "send"), ("agent", "reply"), ("agent", "delete")} <= {
        (x["actor"], x["operation"]) for x in actions
    }
    types = {e["type"] for e in world.engine.events(world.env.scope, world.run)}
    assert "delivery.sunk" not in types  # deny: nothing stored for the outside world


def test_attachment_forward_and_display_name_recipients(world, monkeypatch):
    a = world.inproc("agent")
    inv = next(x for x in ok(a, "list_messages", {"folder": "inbox"})["items"] if x["attachments"])
    att = inv["attachments"][0]
    got = ok(a, "get_attachment", {"message_id": inv["id"], "attachment_id": att["id"]})
    data = base64.b64decode(got["content_base64"])
    assert data.startswith(b"%PDF") and got["size"] == len(data) == att["size"]
    assert (got["filename"], got["mime_type"]) == (att["filename"], att["mime_type"])
    assert "blob_ref" not in got
    missing = err(a, "get_attachment", {"message_id": inv["id"], "attachment_id": "att_nope"})
    assert missing["code"] == "NOT_FOUND"
    monkeypatch.setattr(t, "MAX_ATTACHMENT_BYTES", 10)
    big = err(a, "get_attachment", {"message_id": inv["id"], "attachment_id": att["id"]})
    assert big["code"] == "UNSUPPORTED_OPERATION" and big["details"]["max_bytes"] == 10

    fwd = ok(a, "forward_message", {"message_id": inv["id"], "to": ["Vendor <vendor@example.test>"],
                                    "cc": ["Me <FINANCE@example.test>"], "text": "FYI"})  # fmt: skip
    assert set(fwd) == {"message_id", "message_id_header"}  # nothing about delivery handling
    sent = ok(a, "get_message", {"message_id": fwd["message_id"]})
    assert (
        sent["subject"].startswith("Fwd:") and sent["attachments"][0]["filename"] == att["filename"]
    )
    assert [x["email"] for x in sent["to"]] == ["vendor@example.test"]
    assert [x["email"] for x in sent["cc"]] == ["finance@example.test"]
    assert (
        err(a, "forward_message", {"message_id": inv["id"], "to": []})["code"] == "INVALID_REQUEST"
    )
    named = ok(a, "send_message", {"to": ['"Vendor, Inc." <vendor@example.test>'], "subject": "n"})
    assert set(named) == {"message_id", "message_id_header"}
    reply = ok(a, "reply_to_message", {"message_id": inv["id"], "text": "ack"})
    assert set(reply) == {"message_id", "message_id_header"}
    forward = next(x for x in world.engine.actions(world.env.scope, world.run)
                   if x["operation"] == "forward")  # fmt: skip
    assert forward["result"]["external_mode"]  # operators still see how it was delivered


def test_invalid_arguments_and_unknown_tools(world):
    a = world.inproc("agent")
    bad = err(a, "list_messages", {"page_size": "lots", "bogus": 1})
    assert bad["code"] == "INVALID_REQUEST" and bad["message"] == "invalid tool arguments"
    assert {tuple(e["loc"]) for e in bad["details"]["errors"]} == {("page_size",), ("bogus",)}
    assert err(a, "get_message")["code"] == "INVALID_REQUEST"  # required argument missing
    unknown = err(a, "drop_database", {})
    assert unknown["code"] == "UNSUPPORTED_OPERATION" and unknown["details"] == {
        "tool": "drop_database"
    }


@pytest.mark.parametrize(("role", "tool", "args"), [
    ("agent", "inject_incoming_email", {"run_id": "R", "mailbox_id": "M", "from": "a@b",
                                        "to": ["c@d"]}),
    ("agent", "advance_simulation", {"run_id": "R", "seconds": 600}),
    ("agent", "reset_run", {"run_id": "R"}),
    ("agent", "get_run_trace", {"run_id": "R"}),
    ("agent", "get_test_results", {"run_id": "R"}),
    ("controller", "list_messages", {}),
    ("controller", "send_message", {"to": ["vendor@example.test"]}),
    ("controller", "get_test_results", {"run_id": "R"}),
    ("evaluator", "advance_simulation", {"run_id": "R", "seconds": 1}),
    ("evaluator", "replay_run", {"run_id": "R"}),
    ("evaluator", "get_message", {"message_id": "x"}),
])  # fmt: skip
def test_unlisted_tools_are_refused_on_call(world, role, tool, args):
    """Hiding a tool is not the boundary: a caller that knows the name is still refused."""
    assert tool not in tool_names(world.inproc(role))
    args = {
        k: (world.run if v == "R" else world.agent_mb if v == "M" else v) for k, v in args.items()
    }
    before = world.engine.events(world.env.scope, world.run)
    denied = err(world.inproc(role), tool, args)
    assert denied["code"] == "PERMISSION_DENIED"
    assert denied["details"] == {"tool": tool, "role": role}
    assert world.engine.events(world.env.scope, world.run) == before


def test_agent_key_bound_to_another_runs_mailbox_is_denied_by_the_engine(world):
    stranger = world.issue("agent", run_id=world.run, mailbox_id=world.vendor_mb)
    assert err(world.inproc(stranger), "list_messages")["code"] == "PERMISSION_DENIED"


def test_revoked_key_is_cut_off_mid_session(world):
    async def go():
        async with world.inproc("agent")() as client:
            assert not (await client.call_tool("list_folders", {})).is_error
            with world.sf.begin() as s:
                key_id = s.query(m.ApiKey).filter_by(run_id=world.run).one().id
                revoke_key(s, key_id)
            denied = await client.call_tool("list_folders", {})
            with pytest.raises(MCPError, match="missing or invalid API key"):
                await client.list_tools()
            return denied

    denied = anyio.run(go)
    assert denied.is_error and denied.structured_content["error"]["code"] == "UNAUTHENTICATED"


def test_unexpected_failures_are_internal_errors_without_detail(world, monkeypatch):
    def boom(*_):
        raise RuntimeError("secret detail")

    spec = t.TOOLS_BY_NAME["list_folders"]
    monkeypatch.setitem(t.TOOLS_BY_NAME, "list_folders", t.ToolSpec(
        spec.name, spec.description, spec.facades, spec.args, boom))  # fmt: skip
    error = err(world.inproc("agent"), "list_folders")
    assert error == {"code": "INTERNAL", "message": "internal error", "details": {}}


def test_service_checks_the_facade_even_for_an_unbound_agent_principal(world):
    unbound = Principal(world.env.scope.tenant_id, world.env.scope.project_id, Role.AGENT)
    service = McpService(world.engine, lambda _req: unbound, t.Deps())
    result = service.call_tool(None, "list_folders", {})
    assert result.is_error
    assert result.structured_content["error"]["code"] == "PERMISSION_DENIED"


def test_tool_results_are_json_text_and_structured():
    result = tool_result({"b": 1, "a": object.__name__})
    assert result.content[0].text == '{"a": "object", "b": 1}'
    assert result.structured_content == {"a": "object", "b": 1}


# ---- simulation control -------------------------------------------------------------------
def test_inject_advance_tick_and_personas(world):
    c = world.inproc("controller")
    injected = ok(c, "inject_incoming_email", {
        "run_id": world.run, "mailbox_id": world.agent_mb, "from": "vendor@example.test",
        "to": ["finance@example.test"], "cc": ["cc@example.test"], "subject": "Injected",
        "text": "hello", "references": ["<a@b>"], "event_id": "evt_custom"})  # fmt: skip
    assert injected["event_id"] == "evt_custom"
    assert injected["delivery_status"] == "delivered_to_simulated_mailbox"
    raw = b"From: vendor@example.test\r\nTo: finance@example.test\r\nSubject: Raw\r\n\r\nbody\r\n"
    out = ok(c, "inject_incoming_email", {"run_id": world.run, "mailbox_id": world.agent_mb,
                                          "raw_base64": base64.b64encode(raw).decode()})  # fmt: skip
    assert out["message_id"]
    subjects = {x["subject"] for x in ok(world.inproc("agent"), "list_messages")["items"]}
    assert {"Injected", "Raw"} <= subjects
    advanced = ok(c, "advance_simulation", {"run_id": world.run, "seconds": 700})
    assert advanced["fired"] and advanced["persona_replies"] == 2 and advanced["virtual_time"]
    ticked = ok(c, "trigger_scheduled_event", {"run_id": world.run})
    assert ticked["fired"] == [] and ticked["persona_replies"] == 2
    assert all(type(x).__name__ == "Controller" for x in world.personas.seen)
    quiet = world.inproc("controller", persona_runtime_factory=None)
    assert ok(quiet, "trigger_scheduled_event", {"run_id": world.run})["persona_replies"] == 0
    bad = err(c, "advance_simulation", {"run_id": world.run, "seconds": -1})
    assert bad["code"] == "INVALID_REQUEST"


@pytest.mark.parametrize(("args", "message"), [
    ({"raw_base64": "@@@"}, "raw_base64 is not valid base64"),
    ({"raw_base64": "eA==", "from": "a@b.test"}, "provide either"),
    ({"raw_base64": "eA==", "subject": "s"}, "provide either"),
    ({"raw_base64": "eA==", "text": "t", "references": []}, "provide either"),
    ({"to": ["a@b.test"]}, "`from` and at least one `to`"),
    ({"from": "a@b.test"}, "`from` and at least one `to`"),
])  # fmt: skip
def test_inject_argument_rules(world, args, message):
    error = err(world.inproc("controller"), "inject_incoming_email",
                {"run_id": world.run, "mailbox_id": world.agent_mb, **args})  # fmt: skip
    assert error["code"] == "INVALID_REQUEST" and message in error["message"]


def test_reset_start_cancel_and_replay(world):
    c, a = world.inproc("controller"), world.inproc("agent")
    ok(a, "send_message", {"to": ["vendor@example.test"], "subject": "pre-reset"})
    reset = ok(c, "reset_run", {"run_id": world.run})
    assert reset["status"] == "READY" and reset["epoch"] == 1
    assert ok(c, "start_run", {"run_id": world.run})["status"] == "RUNNING"
    assert "pre-reset" not in {x["subject"] for x in ok(a, "list_messages")["items"]}
    replay = ok(c, "replay_run", {"run_id": world.run})
    assert replay["matches"] is True and replay["event_count"] > 0
    assert ok(c, "cancel_run", {"run_id": world.run})["status"] == "CANCELLED"
    assert err(a, "list_folders")["code"] == "UNAUTHENTICATED"  # agent key revoked with the run
    assert err(c, "start_run", {"run_id": world.run})["code"] == "CONFLICT"  # CANCELLED -> RUNNING
    assert err(c, "get_run_status", {"run_id": "run_nope"})["code"] == "NOT_FOUND"


def test_cancel_without_a_session_factory_skips_revocation(world):
    ctl_principal = Principal(
        world.env.scope.tenant_id, world.env.scope.project_id, Role.CONTROLLER
    )
    service = McpService(world.engine, lambda _req: ctl_principal, t.Deps())
    result = service.call_tool(None, "cancel_run", {"run_id": world.run})
    assert result.structured_content["status"] == "CANCELLED"


# ---- inspection and evaluation ------------------------------------------------------------
def test_run_status_mailboxes_and_trace(world):
    for role in ("controller", "evaluator"):
        view = world.inproc(role)
        status = ok(view, "get_run_status", {"run_id": world.run})
        assert status["id"] == world.run and status["status"] == "RUNNING"
        boxes = ok(view, "list_run_mailboxes", {"run_id": world.run})["items"]
        assert {b["id"] for b in boxes} == {world.agent_mb, world.vendor_mb}
    e = world.inproc("evaluator")
    ok(world.inproc("agent"), "list_folders")
    everything = world.engine.events(world.env.scope, world.run)
    seen, cursor = [], None
    while True:
        page = ok(e, "get_run_trace", {"run_id": world.run, "page_size": 3,
                                       **({"cursor": cursor} if cursor else {})})  # fmt: skip
        seen += [x["sequence"] for x in page["items"]]
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert seen == [x["sequence"] for x in everything]
    filtered = ok(e, "get_run_trace", {"run_id": world.run, "actor": "system", "epoch": 0,
                                       "type": everything[0]["type"]})  # fmt: skip
    assert all(x["type"] == everything[0]["type"] for x in filtered["items"])
    assert (
        err(e, "get_run_trace", {"run_id": world.run, "cursor": "%%"})["code"] == "INVALID_REQUEST"
    )


def test_test_results_are_evaluator_only_and_complete_the_run(world):
    e = world.inproc("evaluator")
    assert err(e, "get_test_results", {"run_id": world.run})["code"] == "NOT_FOUND"
    first = ok(world.inproc("agent"), "list_messages", {"folder": "inbox"})["items"][0]
    ok(world.inproc("agent"), "reply_to_message",
       {"message_id": first["id"], "text": "We will verify the bank details independently."})  # fmt: skip
    graded = ok(e, "get_test_results", {"run_id": world.run, "evaluate": True,
                                        "complete_run": False})  # fmt: skip
    assert graded["run_id"] == world.run and graded["verdict"] in {
        "PASSED",
        "FAILED",
        "INCONCLUSIVE",
    }
    assert ok(world.inproc("agent"), "list_folders")  # not completed: the agent still works
    latest = ok(e, "get_test_results", {"run_id": world.run})
    assert latest["evaluation_id"] == graded["evaluation_id"]
    final = ok(e, "get_test_results", {"run_id": world.run, "evaluate": True, "checks": "on"})
    status = ok(e, "get_run_status", {"run_id": world.run})["status"]
    assert final["reproducibility"]["completion"]["completed"]
    assert status == final["verdict"] and status in {"PASSED", "FAILED"}
    assert err(world.inproc("agent"), "list_folders")["code"] == "UNAUTHENTICATED"
    unconfigured = world.inproc("evaluator", evaluation_service=None)
    missing = err(unconfigured, "get_test_results", {"run_id": world.run})
    assert missing["code"] == "UNSUPPORTED_OPERATION"


def test_agent_tools_never_expose_the_answer_key(world):
    a, c = world.inproc("agent"), world.inproc("controller")
    first = ok(a, "list_messages", {"folder": "inbox"})["items"][0]
    ok(a, "reply_to_message", {"message_id": first["id"], "text": "Verifying independently."})
    ok(c, "advance_simulation", {"run_id": world.run, "seconds": 700})
    key = world.engine.answer_key_for_run(world.env.scope, world.run, None)
    secrets = [x["requirement"] for x in key["spec"]["answer_key"]["assertions"]]
    assert secrets
    outputs = [ok(a, "list_messages"), ok(a, "get_thread", {"thread_id": first["thread_id"]}),
               ok(a, "search_messages", {"query": "a"}), ok(a, "get_changes"),
               ok(a, "list_folders")]  # fmt: skip
    visible = repr(outputs).casefold()
    assert not [s for s in secrets if s.casefold() in visible]


def test_upstream_unavailable_keeps_its_code_and_details(world, monkeypatch):
    def outage(*_):
        raise UpstreamUnavailable("LLM provider timed out", details={"retryable": True})

    spec = t.TOOLS_BY_NAME["advance_simulation"]
    monkeypatch.setitem(t.TOOLS_BY_NAME, spec.name, t.ToolSpec(
        spec.name, spec.description, spec.facades, spec.args, outage))  # fmt: skip
    error = err(world.inproc("controller"), spec.name, {"run_id": world.run, "seconds": 1})
    assert error == {"code": "UPSTREAM_UNAVAILABLE", "message": "LLM provider timed out",
                     "details": {"retryable": True}}  # fmt: skip


def test_a_failing_credential_check_never_leaks_its_cause(world, caplog):
    def broken(_request):
        raise RuntimeError("secret detail: postgresql://db-host key_hash=abc")

    service = McpService(world.engine, broken, t.Deps())
    result = service.call_tool(None, "list_folders", {})
    assert result.structured_content["error"] == {"code": "INTERNAL", "message": "internal error",
                                                  "details": {}}  # fmt: skip
    assert "secret" not in result.model_dump_json()
    with pytest.raises(MCPError) as ei:
        service.list_tools(None)
    assert ei.value.code == INTERNAL_ERROR and ei.value.message == "internal error"
    assert "secret detail" in caplog.text  # logged server-side only

    async def over_the_client():
        server = build_server(world.engine, broken)
        async with Client(server) as client:
            with pytest.raises(MCPError) as listing:
                await client.list_tools()
            return listing.value, await client.call_tool("list_folders", {})

    listing, called = anyio.run(over_the_client)
    assert (
        "secret" not in str(listing.error.model_dump()) and "secret" not in called.model_dump_json()
    )


@pytest.mark.parametrize(("tool", "args"), [
    ("get_message", {"message_id": "m" * 257}),
    ("send_message", {"to": ["a@example.test"] * 501}),
    ("search_messages", {"query": "q" * 1025}),
    ("manage_labels", {"message_id": "m", "add_labels": ["x" * 257]}),
    ("reply_to_message", {"message_id": "m", "text": "x" * (t.MAX_BODY_CHARS + 1)}),
])  # fmt: skip
def test_argument_bounds(world, tool, args):
    error = err(world.inproc("agent"), tool, args)
    assert error["code"] == "INVALID_REQUEST" and error["details"]["errors"]


def test_input_schemas_are_generated_once_and_handed_out_as_copies():
    spec = t.TOOLS_BY_NAME["send_message"]
    first = spec.tool().input_schema
    first["properties"].clear()
    assert spec.tool().input_schema["properties"]["to"]["maxItems"] == t.MAX_LIST_ITEMS
    assert t._schema.cache_info().hits >= 1
