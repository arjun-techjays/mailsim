"""ProviderProfile registry. Declares capabilities and quirks; implements no adapter."""

from __future__ import annotations

from dataclasses import dataclass, field

from mailsim.domain.errors import NotFound


@dataclass(frozen=True)
class ProviderProfile:
    provider: str
    version: str
    capabilities: frozenset[str]
    quirks: tuple[str, ...] = field(default_factory=tuple)


_PROFILES: dict[tuple[str, str], ProviderProfile] = {}


def register(profile: ProviderProfile) -> None:
    _PROFILES[(profile.provider, profile.version)] = profile


def get(provider: str, version: str) -> ProviderProfile:
    try:
        return _PROFILES[(provider, version)]
    except KeyError:
        raise NotFound(f"unknown provider profile {provider}@{version}") from None


def all_profiles() -> list[ProviderProfile]:
    return sorted(_PROFILES.values(), key=lambda p: (p.provider, p.version))


# The neutral profile is the only one in Phase 1.
register(
    ProviderProfile(
        provider="neutral",
        version="1",
        capabilities=frozenset(
            {"deliver", "send", "reply", "move", "label", "flag", "draft", "delete"}
        ),
        quirks=("no provider id mapping yet",),
    )
)
