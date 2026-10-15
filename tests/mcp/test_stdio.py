"""stdio transport: startup key checks, an in-process stdio session, and the real thing (a
subprocess driven by the official MCP client)."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import anyio
import pytest

from mailsim.mcp.stdio import StdioConfigError, run_stdio
from mcp import Client, StdioServerParameters
from tests.mcp.conftest import call

pytestmark = pytest.mark.db
ROOT = Path(__file__).resolve().parents[2]


class FakeStdout:
    def __init__(self) -> None:
        self.lines: list[str] = []

    async def write(self, data: str) -> None:
        self.lines.append(data)

    async def flush(self) -> None:
        return None

    def responses(self) -> dict[int, dict]:
        out = {}
        for line in self.lines:
            msg = json.loads(line)
            if "id" in msg:
                out[msg["id"]] = msg
        return out


class FakeStdin:
    """Yields the requests, then holds EOF until every request has been answered."""

    def __init__(self, messages: list[dict], stdout: FakeStdout) -> None:
        self.messages, self.stdout = messages, stdout

    def __aiter__(self):
        return self._lines()

    async def _lines(self):
        for msg in self.messages:
            yield json.dumps(msg) + "\n"
        wanted = {m["id"] for m in self.messages if "id" in m}
        with anyio.fail_after(20):
            while not wanted <= set(self.stdout.responses()):
                await anyio.sleep(0.01)


def _messages(*calls: tuple[str, dict]) -> list[dict]:
    msgs: list[dict] = [
        {"jsonrpc": "2.0", "id": 0, "method": "initialize",
         "params": {"protocolVersion": "2025-11-25", "capabilities": {},
                    "clientInfo": {"name": "t", "version": "0"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
    ]  # fmt: skip
    for i, (name, args) in enumerate(calls, start=2):
        msgs.append({"jsonrpc": "2.0", "id": i, "method": "tools/call",
                     "params": {"name": name, "arguments": args}})  # fmt: skip
    return msgs


def test_startup_requires_a_valid_key(world):
    with pytest.raises(StdioConfigError, match="MAILSIM_API_KEY"):
        run_stdio(world.engine, environ={"MAILSIM_API_KEY": "  "})
    with pytest.raises(StdioConfigError, match="revoked"):
        run_stdio(world.engine, "msk_000000000000_" + "x" * 40, environ={})


def test_stdio_session_in_process(world):
    out = FakeStdout()
    stdin = FakeStdin(_messages(("list_messages", {"folder": "inbox"}),
                                ("get_test_results", {"run_id": world.run})), out)  # fmt: skip
    run_stdio(world.engine, environ={"MAILSIM_API_KEY": world.keys["agent"]},
              evaluation_service=world.evaluations, stdin=stdin, stdout=out)  # fmt: skip
    res = out.responses()
    assert res[0]["result"]["serverInfo"]["name"] == "mailsim"
    names = [t["name"] for t in res[1]["result"]["tools"]]
    assert names[0] == "list_messages" and "get_test_results" not in names
    assert len(res[2]["result"]["structuredContent"]["items"]) == 2
    assert res[3]["result"]["isError"] is True
    assert res[3]["result"]["structuredContent"]["error"]["code"] == "PERMISSION_DENIED"


def _params(world, key: str) -> StdioServerParameters:
    url = world.sf.kw["bind"].url.render_as_string(hide_password=False)
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(ROOT / "src"), str(ROOT)]),
           "MAILSIM_DATABASE_URL": url, "MAILSIM_BLOB_DIR": str(world.env.blobs.root),
           "MAILSIM_API_KEY": key}  # fmt: skip
    return StdioServerParameters(command=sys.executable, args=["-m", "tests.mcp.stdio_entry"],
                                 env=env, cwd=str(ROOT))  # fmt: skip


@pytest.mark.parametrize("mode", ["auto", "legacy"])
def test_stdio_subprocess_with_the_official_client(world, mode):
    agent = lambda: Client(_params(world, world.keys["agent"]), mode=mode)  # noqa: E731

    async def session():
        async with agent() as client:
            names = [t.name for t in (await client.list_tools()).tools]
            inbox = await client.call_tool("list_messages", {"folder": "inbox"})
            sent = await client.call_tool("send_message", {"to": ["vendor@example.test"],
                                                           "subject": "via stdio"})  # fmt: skip
            denied = await client.call_tool(
                "advance_simulation", {"run_id": world.run, "seconds": 1}
            )
            return names, inbox, sent, denied

    names, inbox, sent, denied = anyio.run(session)
    assert "send_message" in names and "advance_simulation" not in names
    assert len(inbox.structured_content["items"]) == 2
    assert sent.structured_content["message_id_header"]
    assert denied.structured_content["error"]["code"] == "PERMISSION_DENIED"
    ctl = lambda: Client(_params(world, world.keys["controller"]), mode=mode)  # noqa: E731
    status = call(ctl, "get_run_status", {"run_id": world.run})
    assert status.structured_content["agent_actions"] >= 1  # the send


def test_stdio_hides_credential_check_failures(world, monkeypatch):
    def broken(_sf, _key):
        def resolve(_request):
            raise RuntimeError("secret detail")

        return resolve

    monkeypatch.setattr("mailsim.mcp.stdio.key_resolver", broken)
    out = FakeStdout()
    stdin = FakeStdin(_messages(("list_folders", {})), out)
    run_stdio(world.engine, world.keys["agent"], stdin=stdin, stdout=out)
    res = out.responses()
    assert res[1]["error"]["message"] == "internal error"
    assert res[2]["result"]["structuredContent"]["error"]["code"] == "INTERNAL"
    assert "secret" not in "".join(out.lines)
