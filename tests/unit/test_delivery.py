import pytest

from mailsim.domain.errors import ExternalDeliveryBlocked
from mailsim.engine.delivery import DeliveryPolicy, ExternalMode

BOXES = {"a@example.test": "mbx_a", "b@example.test": "mbx_b"}


def test_internal_only_and_dedupe_and_case():
    plan = DeliveryPolicy().plan(["A@example.test", "a@example.test", " b@example.test"], BOXES)
    assert plan.internal == [("a@example.test", "mbx_a"), ("b@example.test", "mbx_b")]
    assert plan.external == []


def test_deny_blocks_whole_send_regardless_of_domain_realness():
    with pytest.raises(ExternalDeliveryBlocked) as ei:
        DeliveryPolicy("deny").plan(["a@example.test", "someone@gmail.com"], BOXES)
    assert ei.value.details == {"recipients": ["someone@gmail.com"]}


@pytest.mark.parametrize("mode", [ExternalMode.SINK, ExternalMode.EVENT])
def test_sink_and_event_modes_never_deliver(mode):
    plan = DeliveryPolicy(mode).plan(["a@example.test", "x@real.com"], BOXES)
    assert plan.internal == [("a@example.test", "mbx_a")] and plan.external == ["x@real.com"]


def test_unknown_mode_rejected():
    with pytest.raises(ValueError):
        DeliveryPolicy("allow")
