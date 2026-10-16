"""MCP test fixtures: one open-agent run with agent / controller / evaluator keys, and helpers
that drive the server through the official MCP client (in-process and streamable HTTP)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import anyio
import httpx2
import pytest
from fastapi import FastAPI
from mcp.client.streamable_http import streamable_http_client
from mcp_types import CallToolResult

from mailsim.api.app import create_app
from mailsim.api.auth import issue_key
from mailsim.evaluation.service import EvaluationService
from mailsim.mcp.http import router
from mailsim.mcp.server import build_server, key_resolver
from mcp import Client
from tests.scenarios import write_variant

pytestmark = pytest.mark.db
BASE = "http://127.0.0.1"


def _open(s: dict[str, Any]) -> None:
    """Every agent operation allowed (contract-test style)."""
    s["agent"]["allowed_operations"] = []
    s["agent"]["prohibited_operations"] = ["external_delivery"]
    s["evaluation"]["limits"]["max_agent_actions"] = 500
    # a decidable answer key: the judge-graded assertion + one machine-checked state check
    s["evaluation"]["assertions"] = [
        *(a for a in s["evaluation"]["assertions"] if a["type"] == "semantic"),
        {
            "id": "mailbox-kept",
            "type": "state",
            "requirement": "The mailbox still exists",
            "check": {"where": {"mailbox": "me"}, "count": {"min": 0}},
        },
    ]


@dataclass
class StubPersonas:
    """Persona runtime stand-in: answers two replies per step and records its controller."""

    seen: list[Any] = field(default_factory=list)

    def __call__(self, ctl: Any) -> StubPersonas:
        self.seen.append(ctl)
        return self

    def step(self, run_id: str) -> list[dict[str, Any]]:
        return [{"run_id": run_id}, {"run_id": run_id}]

    def next_due(self, run_id: str, *, after: Any = None) -> None:
        return None  # nothing scheduled: one advance, one step


class PassingJudge:
    def judge(self, *, requirement: str, rubric: str, agent_texts: list[str]) -> dict[str, Any]:
        return {"passed": True, "score": 1.0, "explanation": "stub", "llm_call_id": None}


class World:
    def __init__(self, env: Any, tmp_path: Any) -> None:
        self.env = env
        self.engine, self.sf = env.engine, env.session_factory
        self.run = env.run(write_variant(tmp_path, _open))
        self.agent_mb = env.mbx(self.run, "finance_manager")
        self.vendor_mb = env.mbx(self.run, "vendor")
        self.personas = StubPersonas()
        self.evaluations = EvaluationService(
            env.engine, env.session_factory, env.blobs, judge=PassingJudge()
        )
        self.keys = {
            "agent": self.issue("agent", run_id=self.run, mailbox_id=self.agent_mb),
            "controller": self.issue("controller"),
            "evaluator": self.issue("evaluator"),
        }
        self.app = self.make_app()

    def issue(self, role: str, **kw: Any) -> str:
        with self.sf.begin() as s:
            return issue_key(s, tenant_id=self.env.scope.tenant_id,
                             project_id=self.env.scope.project_id, role=role, **kw).api_key  # fmt: skip

    def make_app(self, **overrides: Any) -> FastAPI:
        state: dict[str, Any] = {"engine": self.engine, "session_factory": self.sf,
                                 "blobs": self.env.blobs, "evaluation_service": self.evaluations,
                                 "persona_runtime_factory": self.personas}  # fmt: skip
        state.update(overrides)
        app = create_app(**{k: v for k, v in state.items() if v is not None})
        app.include_router(router)
        return app

    def server(self, role_or_key: str, **kw: Any) -> Any:
        key = self.keys.get(role_or_key, role_or_key)
        deps: dict[str, Any] = {"evaluation_service": self.evaluations,
                                "persona_runtime_factory": self.personas}  # fmt: skip
        deps.update(kw)
        return build_server(self.engine, key_resolver(self.sf, key), **deps)

    # -- clients -------------------------------------------------------------------------
    def inproc(self, role_or_key: str, **kw: Any) -> Callable[[], Client]:
        return lambda: Client(self.server(role_or_key, **kw))

    def http(self, role_or_key: str, *, mode: str = "auto", app: FastAPI | None = None,
             headers: dict[str, str] | None = None) -> Callable[[], Any]:  # fmt: skip
        key = self.keys.get(role_or_key, role_or_key)
        hdrs = {"Authorization": f"Bearer {key}"} if headers is None else headers
        target = app or self.app

        class _Ctx:
            async def __aenter__(self) -> Client:
                self.http = httpx2.AsyncClient(transport=httpx2.ASGITransport(app=target),
                                               base_url=BASE, headers=hdrs)  # fmt: skip
                await self.http.__aenter__()
                self.client = Client(streamable_http_client(f"{BASE}/mcp", http_client=self.http),
                                     mode=mode)  # fmt: skip
                return await self.client.__aenter__()

            async def __aexit__(self, *exc: Any) -> None:
                await self.client.__aexit__(*exc)
                await self.http.__aexit__(*exc)

        return _Ctx


def call(
    factory: Callable[[], Any], tool: str, args: dict[str, Any] | None = None
) -> CallToolResult:
    async def go() -> CallToolResult:
        async with factory() as client:
            return await client.call_tool(tool, args or {})

    return anyio.run(go)


def ok(factory: Callable[[], Any], tool: str, args: dict[str, Any] | None = None) -> Any:
    result = call(factory, tool, args)
    assert not result.is_error, result.structured_content
    return result.structured_content


def err(
    factory: Callable[[], Any], tool: str, args: dict[str, Any] | None = None
) -> dict[str, Any]:
    result = call(factory, tool, args)
    assert result.is_error, result.structured_content
    error: dict[str, Any] = result.structured_content["error"]
    assert result.content[0].text.startswith(error["code"])
    return error


def tool_names(factory: Callable[[], Any]) -> list[str]:
    async def go() -> list[str]:
        async with factory() as client:
            return [t.name for t in (await client.list_tools()).tools]

    return anyio.run(go)


@pytest.fixture()
def world(env, tmp_path) -> World:
    return World(env, tmp_path)
