"""Assertion-type plugins. Importing this package registers one evaluator per type."""

from __future__ import annotations

from mailsim.evaluation.checks import (
    action,
    content,
    policy,
    protocol,
    safety,
    semantic,
    state,
    temporal,
)

__all__ = ["action", "content", "policy", "protocol", "safety", "semantic", "state", "temporal"]
