"""Shared behavioural contract (spec 5.5) expressed against a small driver interface.

Phase 1 runs it against the engine only. Phases 3-4 add drivers (Gmail, Graph, IMAP/SMTP)
to `DRIVERS` and must pass the same tests: read-after-write, identity stability, threading
consistency, error semantics and reset.
"""

from __future__ import annotations

import pytest

from mailsim.domain.errors import NotFound, SimError
from mailsim.engine.core import CONTROLLER

pytestmark = pytest.mark.db


class EngineDriver:
    name = "neutral-engine"

    def __init__(self, env):
        self.env = env
        self.run = env.run()
        self.mailbox = env.mbx(self.run, "finance_manager")
        self.other = env.mbx(self.run, "vendor")

    def inbox(self, mailbox=None):
        e = self.env
        return e.engine.list_messages(
            e.scope, self.run, mailbox or self.mailbox, folder="inbox", actor=CONTROLLER
        )

    def get(self, message_id, mailbox=None):
        e = self.env
        return e.engine.get_message(
            e.scope, self.run, mailbox or self.mailbox, message_id, actor=CONTROLLER
        )

    def send(self, to, subject):
        e = self.env
        return e.engine.send(
            e.scope, self.run, self.mailbox, to=to, subject=subject, text="b", actor=CONTROLLER
        )

    def reply(self, message_id, text):
        e = self.env
        return e.engine.reply(
            e.scope, self.run, self.mailbox, message_id, text=text, actor=CONTROLLER
        )

    def thread(self, thread_id):
        e = self.env
        return e.engine.get_thread(e.scope, self.run, self.mailbox, thread_id, actor=CONTROLLER)

    def reset(self):
        self.env.engine.reset_run(self.env.scope, self.run)
        self.env.engine.start_run(self.env.scope, self.run)


DRIVERS = [EngineDriver]


@pytest.fixture(params=DRIVERS, ids=lambda d: d.name)
def driver(request, env):
    return request.param(env)


def test_send_isolation_and_read_after_write(driver):
    res = driver.send(["vendor@example.test"], "contract")
    received = driver.inbox(driver.other)
    assert [m.message_id_header for m in received] == [res["message_id_header"]]


def test_identity_is_stable_across_reads(driver):
    first = driver.inbox()[0]
    again = driver.get(first.id)
    assert (again.id, again.message_id_header, again.thread_id) == (
        first.id,
        first.message_id_header,
        first.thread_id,
    )
    assert driver.inbox()[0].id == first.id


def test_threading_consistent_after_reply(driver):
    original = driver.inbox()[0]
    driver.reply(original.id, "ack")
    thread = driver.thread(original.thread_id)
    assert original.id in {m.id for m in thread}
    assert any(m.in_reply_to == original.message_id_header for m in thread)


def test_error_semantics(driver):
    with pytest.raises(NotFound) as ei:
        driver.get("msg_does_not_exist")
    assert isinstance(ei.value, SimError) and ei.value.code == "NOT_FOUND"


def test_reset_restores_baseline_content(driver):
    before = sorted(m.message_id_header for m in driver.inbox())
    driver.send(["finance@example.test"], "extra")
    assert len(driver.inbox()) == len(before) + 1
    driver.reset()
    assert sorted(m.message_id_header for m in driver.inbox()) == before
