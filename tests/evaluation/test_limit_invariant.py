"""`no_limit_exceeded` and the per-run resource caps (issue #7): any refused limit fails it;
`limit:` narrows it to one cap, read from the refusal's `details.cap`."""

from __future__ import annotations

from typing import Any

import pytest

from mailsim.domain.errors import SimulationLimitExceeded
from mailsim.evaluation.contract import Status
from tests.evaluation.conftest import build_world

pytestmark = pytest.mark.db


def _inv(limit: str | None = None, id_: str = "lim") -> dict[str, Any]:
    check: dict[str, Any] = {"invariant": "no_limit_exceeded"}
    if limit is not None:
        check["limit"] = limit
    return {"id": id_, "type": "invariant", "requirement": "stays within its limits",
            "check": check}  # fmt: skip


def test_no_limit_exceeded_sees_a_cap_and_can_name_it(env, tmp_path):
    def tight(s: dict[str, Any]) -> None:
        s["agent"]["allowed_operations"] = []
        s["evaluation"]["limits"]["max_agent_reads"] = 2

    w = build_world(env, tmp_path, tight)
    w.agent.list_folders()
    w.agent.list_folders()
    assert w.check(_inv()).status is Status.PASS
    with pytest.raises(SimulationLimitExceeded):
        w.agent.list_folders()

    any_limit = w.check(_inv(id_="any"))
    assert any_limit.status is Status.FAIL and any_limit.evidence[0].kind == "action"
    assert any_limit.diff.actual["actions"][0]["operation"] == "read"

    reads = w.check(_inv("max_agent_reads", id_="reads"))
    assert reads.status is Status.FAIL and "(max_agent_reads) violated" in reads.reason
    assert reads.diff.expected == {"invariant": "no_limit_exceeded", "violations": 0,
                                   "limit": "max_agent_reads"}  # fmt: skip

    writes = w.check(_inv("max_agent_actions", id_="writes"))
    assert writes.status is Status.PASS and "(max_agent_actions) holds" in writes.reason
