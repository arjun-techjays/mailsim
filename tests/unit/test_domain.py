import pytest

from mailsim.domain import errors
from mailsim.domain.ids import IdGenerator, is_valid_id, new_control_id
from mailsim.domain.models import CanonicalMessage
from mailsim.domain.states import TERMINAL, TRANSITIONS, RunStatus, check_transition
from mailsim.providers import registry


def test_every_error_has_distinct_code_and_serialises():
    classes = [
        errors.InvalidRequest,
        errors.NotFound,
        errors.PermissionDenied,
        errors.UnsupportedOperation,
        errors.Conflict,
        errors.RateLimited,
        errors.SimulationLimitExceeded,
        errors.RunNotActive,
        errors.ExternalDeliveryBlocked,
        errors.UpstreamUnavailable,
    ]
    codes = {c.code for c in classes}
    assert len(codes) == 10 == len(errors.ErrorCode)
    err = errors.NotFound("x", details={"id": 1})
    assert err.to_dict() == {"code": "NOT_FOUND", "message": "x", "details": {"id": 1}}
    assert errors.Conflict("y").details == {}


def test_id_generator_is_deterministic_and_resumable():
    a, b = IdGenerator(7), IdGenerator(7)
    assert [a.next("msg") for _ in range(5)] == [b.next("msg") for _ in range(5)]
    resumed = IdGenerator(7, counter=5)
    assert resumed.next("msg") == a.next("msg")
    assert IdGenerator(8).next("msg") != IdGenerator(7).next("msg")
    assert a.message_id_header("example.test").endswith("@example.test>")
    assert is_valid_id(a.next("msg"))
    assert not is_valid_id("nope")
    assert new_control_id("run").startswith("run_")


def test_ids_unique_within_stream():
    g = IdGenerator(1)
    ids = {g.next("m") for _ in range(2000)}
    assert len(ids) == 2000


def test_state_machine_allows_lifecycle_and_rejects_illegal():
    check_transition(RunStatus.DRAFT, RunStatus.VALIDATED)
    check_transition(RunStatus.VALIDATED, RunStatus.READY)
    check_transition(RunStatus.READY, RunStatus.RUNNING)
    check_transition(RunStatus.RUNNING, RunStatus.PASSED)
    check_transition(RunStatus.RUNNING, RunStatus.FAILED)
    with pytest.raises(errors.Conflict):
        check_transition(RunStatus.DRAFT, RunStatus.RUNNING)
    for terminal in TERMINAL:
        assert TRANSITIONS[terminal] == frozenset()


def test_canonical_message_aliases_from():
    m = CanonicalMessage.model_validate(
        {
            "id": "msg_1",
            "mailbox_id": "mb_1",
            "message_id_header": "<a@b>",
            "thread_id": "thr_1",
            "folder": "INBOX",
            "from": {"email": "a@b.test"},
            "created_at": "2026-10-15T09:00:00Z",
        }
    )
    assert m.from_.email == "a@b.test"


def test_provider_registry():
    p = registry.get("neutral", "1")
    assert "send" in p.capabilities
    assert registry.all_profiles()[0].provider == "neutral"
    with pytest.raises(errors.NotFound):
        registry.get("gmail", "v1")
