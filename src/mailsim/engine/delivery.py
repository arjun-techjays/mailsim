"""Delivery isolation: the single choke point deciding where a message may go.

There is deliberately no code path in this package that can contact a mail server:
recipients resolve to simulated mailboxes or are handled by the configured external mode
(`deny` / `sink` / `event`), none of which touch the network. This is enforced three ways:
no mail/socket imports in `src/` (ruff banned-api), tests that patch sockets to raise, and
an internal-only Compose network for the sandbox container.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from mailsim.domain.errors import ExternalDeliveryBlocked
from mailsim.domain.models import normalize_address


class ExternalMode(StrEnum):
    DENY = "deny"  # reject the whole send (default)
    SINK = "sink"  # keep a copy in a sandbox-only external-recipient store
    EVENT = "event"  # emit a test event only


@dataclass
class DeliveryPlan:
    internal: list[tuple[str, str]] = field(default_factory=list)  # (email, mailbox_id)
    external: list[str] = field(default_factory=list)  # handled per mode, never delivered


class DeliveryPolicy:
    def __init__(self, mode: ExternalMode | str = ExternalMode.DENY) -> None:
        self.mode = ExternalMode(mode)

    def plan(self, recipients: list[str], mailbox_by_email: dict[str, str]) -> DeliveryPlan:
        plan = DeliveryPlan()
        seen: set[str] = set()
        for raw in recipients:
            email = normalize_address(raw)
            if email in seen:
                continue
            seen.add(email)
            mailbox_id = mailbox_by_email.get(email)
            if mailbox_id is not None:
                plan.internal.append((email, mailbox_id))
            else:
                plan.external.append(email)
        if plan.external and self.mode is ExternalMode.DENY:
            raise ExternalDeliveryBlocked(
                f"delivery to {', '.join(plan.external)} is not permitted",
                details={"recipients": plan.external},
            )
        return plan
