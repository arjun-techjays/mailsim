"""Graph adapter fixtures: a real app (REST + Graph router) over real PostgreSQL, one run with
every agent operation allowed, and an agent key bound to the agent's mailbox."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from fastapi.testclient import TestClient

from mailsim.adapters.graph.api import router
from mailsim.api.app import create_app
from mailsim.api.auth import issue_key
from mailsim.engine.facades import AgentMailbox, Controller, EvaluatorView
from tests.scenarios import write_variant

BASE = "/graph/v1.0"


def open_agent(s: dict[str, Any]) -> None:
    s["agent"]["allowed_operations"] = []
    s["agent"]["prohibited_operations"] = ["external_delivery"]
    s["evaluation"]["limits"]["max_agent_actions"] = 500


def make_app(env: Any, **extra: Any) -> Any:
    app = create_app(engine=env.engine, session_factory=env.session_factory, blobs=env.blobs,
                     **extra)  # fmt: skip
    app.include_router(router)
    return app


@dataclass
class World:
    env: Any
    ctl: Controller
    ev: EvaluatorView
    run: str
    mailbox: str
    vendor: str
    key: str
    app: Any
    client: TestClient

    def issue(self, role: str = "agent", mailbox: str | None = None) -> str:
        with self.env.session_factory.begin() as s:
            kw: dict[str, Any] = {}
            if role == "agent":
                kw = {"run_id": self.run, "mailbox_id": mailbox or self.mailbox}
            return issue_key(s, tenant_id=self.env.scope.tenant_id,
                             project_id=self.env.scope.project_id, role=role, **kw).api_key  # fmt: skip

    def mb(self) -> AgentMailbox:
        return AgentMailbox(self.env.engine, self.env.scope, self.run, self.mailbox)

    def call(self, method: str, path: str, expect: int | None = None, **kw: Any) -> Any:
        r = self.client.request(method, BASE + path, **kw)
        if expect is not None:
            assert r.status_code == expect, (r.status_code, r.text)
        return r

    def get(self, path: str, expect: int = 200, **kw: Any) -> Any:
        return self.call("GET", path, expect, **kw).json()

    def post(self, path: str, json: Any = None, expect: int = 201, **kw: Any) -> Any:
        r = self.call("POST", path, expect, json=json, **kw)
        return r.json() if r.content else None

    def patch(self, path: str, json: Any, expect: int = 200, **kw: Any) -> Any:
        return self.call("PATCH", path, expect, json=json, **kw).json()

    def messages(self, folder: str | None = None, **params: Any) -> list[dict[str, Any]]:
        path = f"/me/mailFolders/{folder}/messages" if folder else "/me/messages"
        return self.get(path, params={"$top": 1000, **params})["value"]

    def by_subject(self, subject: str, folder: str | None = None) -> dict[str, Any]:
        return next(m for m in self.messages(folder) if m["subject"] == subject)

    def inject(self, subject: str, **kw: Any) -> None:
        self.ctl.inject(self.run, self.mailbox, {"from": "vendor@example.test",
                        "to": ["finance@example.test"], "subject": subject,
                        "text": kw.pop("text", "t"), **kw})  # fmt: skip


@pytest.fixture()
def world(env, tmp_path) -> World:
    ctl, ev = Controller(env.engine, env.scope), EvaluatorView(env.engine, env.scope)
    run = ctl.create_run(env.version(write_variant(tmp_path, open_agent)))
    ctl.start_run(run)
    app = make_app(env)
    client = TestClient(app, raise_server_exceptions=False)
    w = World(env, ctl, ev, run, ctl.mailbox_id_for(run, "finance_manager"),
              ctl.mailbox_id_for(run, "vendor"), "", app, client)  # fmt: skip
    w.key = w.issue()
    client.headers["Authorization"] = f"Bearer {w.key}"
    return w
