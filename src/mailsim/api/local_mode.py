"""Local no-auth mode (`serve --local-no-auth`): opt-in, loopback-only operator access.

The dashboard does not ask for an API key: the server mints a random launch token at startup
and prints a dashboard link carrying it in the URL fragment (never sent to a server). Requests
WITHOUT an ``Authorization`` header that carry the token in ``X-Mailsim-Local-Token`` act as the
operator (controller + evaluator for one project). Why a token at all:

- the agent under test usually runs on the same machine; dropping its key must not make it
  the operator (hidden answer-key material is reachable through evaluation reports);
- a web page the developer visits (cross-site requests, DNS rebinding) cannot read or send
  the token: a custom header needs a CORS preflight, which this API never grants.

Defence in depth, ``LoopbackOnly`` refuses any request from a non-loopback peer or with a
non-loopback ``Host`` header while the mode is on (whatever server launched the app).
Requests that DO carry ``Authorization`` are always verified as keys (bad keys answer 401).
"""

from __future__ import annotations

import hmac
import ipaddress
import secrets
from dataclasses import dataclass, field

from starlette.requests import Request
from starlette.types import ASGIApp, Receive, Scope, Send

from mailsim.api.errors import envelope
from mailsim.auth.principal import Principal
from mailsim.domain.errors import ErrorCode

TOKEN_HEADER = "x-mailsim-local-token"  # noqa: S105 - a header name, not a secret
MODE_HEADER = "X-Mailsim-Local-No-Auth"  # on key-less 401s, so the dashboard can explain
LOOPBACK_NAMES = frozenset({"localhost"})


@dataclass(frozen=True)
class LocalAccess:
    operator: Principal
    token: str = field(repr=False)  # never in reprs / logs

    def admits(self, token: str | None) -> bool:
        return bool(token) and hmac.compare_digest(str(token).encode(), self.token.encode())


def new_token() -> str:
    return secrets.token_urlsafe(32)


def is_loopback(host: str | None) -> bool:
    name = (host or "").strip().strip("[]").lower()
    if name in LOOPBACK_NAMES:
        return True
    try:
        ip = ipaddress.ip_address(name)
    except ValueError:
        return False
    mapped = getattr(ip, "ipv4_mapped", None)
    return ip.is_loopback or bool(mapped and mapped.is_loopback)


def host_name(header: str) -> str:
    """Hostname of a Host header value: ``[::1]:8000`` -> ``::1``, ``a:80`` -> ``a``."""
    value = header.strip()
    if value.startswith("["):
        return value[1 : value.find("]")] if "]" in value else value
    return value.rsplit(":", 1)[0] if value.count(":") == 1 else value


class LoopbackOnly:
    """403 for non-loopback peers or Host headers while local no-auth mode is on."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        app = scope.get("app")
        on = getattr(getattr(app, "state", None), "local_access", None) is not None
        if scope["type"] == "http" and on:
            client = scope.get("client") or ("", 0)
            host = dict(scope.get("headers") or []).get(b"host", b"").decode("latin-1")
            if not (is_loopback(client[0]) and is_loopback(host_name(host))):
                response = envelope(Request(scope), 403, str(ErrorCode.PERMISSION_DENIED),
                                    "local no-auth mode serves loopback clients only", {})  # fmt: skip
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)
