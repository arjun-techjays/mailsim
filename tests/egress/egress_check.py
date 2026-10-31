"""Network-level egress probe, run INSIDE a simulator container (spec section 10).

Standard library only, so it runs with the image's bare interpreter (``python -I``) and needs
nothing installed. Compose mounts it read-only; see docs/egress.md and the ``egress`` CI job.

Default mode (a simulator container on the internal ``sandbox`` network), every check must pass:

1. Direct TCP connects to external hosts fail on the SMTP ports 25/465/587 and on HTTP 80/443,
   both by name (smtp.gmail.com, ...) and by IP literal (so a DNS failure alone cannot pass it).
2. With ``--proxy``: the allowlist proxy refuses CONNECT to SMTP and to non-allowlisted hosts
   (403) and refuses plain HTTP forwarding (405).
3. With ``--proxy --allowed host:port``: CONNECT to each allowlisted endpoint is established
   (200), i.e. the LLM path still works. The tunnel is closed right away; nothing is sent.
4. With ``--reachable host:port``: in-sandbox services (postgres, the API) stay reachable, so
   the block is not just "no network at all".

``--expect-open`` is the control: run from a container on a normal bridge network it asserts
that direct HTTPS egress WORKS there, which proves the probe can tell open from blocked (no SMTP
connection is attempted in this mode).

A connection attempt in the default mode is only ever a negative test target: it must fail.
"""

from __future__ import annotations

import argparse
import errno
import socket
import sys
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from urllib.parse import urlsplit

SMTP_PORTS = (25, 465, 587)
HTTP_PORTS = (80, 443)
# Named mail servers (blocked via DNS and routing) and IP literals (routing only).
SMTP_HOSTS = ("smtp.gmail.com", "smtp.office365.com", "1.1.1.1", "8.8.8.8")
HTTP_HOSTS = ("example.com", "1.1.1.1", "8.8.8.8")
CONTROL_TARGETS = (("1.1.1.1", 443), ("example.com", 443))
DEFAULT_TIMEOUT = 5.0


@dataclass(frozen=True)
class Result:
    name: str
    ok: bool
    detail: str


def default_blocked_targets() -> list[tuple[str, int]]:
    targets = [(h, p) for h in SMTP_HOSTS for p in SMTP_PORTS]
    targets += [(h, p) for h in HTTP_HOSTS for p in HTTP_PORTS]
    return targets


def classify(exc: BaseException) -> str:
    if isinstance(exc, socket.gaierror):
        return "dns-failure"
    if isinstance(exc, TimeoutError):
        return "timeout"
    if isinstance(exc, ConnectionRefusedError):
        return "refused"
    if isinstance(exc, OSError) and exc.errno in (errno.ENETUNREACH, errno.EHOSTUNREACH):
        return "no-route"
    return f"error:{type(exc).__name__}:{exc}"


def tcp_connect(host: str, port: int, timeout: float) -> str | None:
    """None when the connection was established (then closed at once), else why it failed."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return None
    except OSError as exc:
        return classify(exc)


def check_direct_blocked(
    targets: Iterable[tuple[str, int]], timeout: float, connect: Callable[..., str | None]
) -> list[Result]:
    out = []
    for host, port in targets:
        why = connect(host, port, timeout)
        if why is None:
            out.append(Result(f"direct {host}:{port}", False, "CONNECTED: egress is open"))
        else:
            out.append(Result(f"direct {host}:{port}", True, f"blocked ({why})"))
    return out


def check_direct_open(
    targets: Iterable[tuple[str, int]],
    timeout: float,
    connect: Callable[..., str | None],
    label: str = "control",
) -> list[Result]:
    out = []
    for host, port in targets:
        why = connect(host, port, timeout)
        out.append(Result(f"{label} {host}:{port}", why is None, why or "connected"))
    return out


def proxy_status(proxy: tuple[str, int], request: bytes, timeout: float) -> int | str:
    """HTTP status the proxy answers to ``request``, or why the proxy was unreachable."""
    try:
        with socket.create_connection(proxy, timeout=timeout) as sock:
            sock.sendall(request)
            data = b""
            while b"\r\n" not in data and len(data) < 4096:
                chunk = sock.recv(1024)
                if not chunk:
                    break
                data += chunk
    except OSError as exc:
        return f"proxy unreachable ({classify(exc)})"
    parts = data.split(b"\r\n", 1)[0].split()
    if len(parts) >= 2 and parts[1].isdigit():
        return int(parts[1])
    return f"unparseable proxy reply {data[:80]!r}"


def connect_request(host: str, port: int) -> bytes:
    return f"CONNECT {host}:{port} HTTP/1.1\r\nHost: {host}:{port}\r\n\r\n".encode()


def check_proxy(
    proxy: tuple[str, int],
    allowed: Sequence[tuple[str, int]],
    timeout: float,
    denied: Sequence[tuple[str, int]] | None = None,
) -> list[Result]:
    if denied is None:
        denied = [("smtp.gmail.com", p) for p in SMTP_PORTS] + [("example.com", 443)]
    out = []
    for host, port in denied:
        got = proxy_status(proxy, connect_request(host, port), timeout)
        out.append(Result(f"proxy CONNECT {host}:{port}", got == 403, f"expected 403, got {got}"))
    get = b"GET http://example.com/ HTTP/1.1\r\nHost: example.com\r\n\r\n"
    got = proxy_status(proxy, get, timeout)
    out.append(Result("proxy GET http://example.com/", got == 405, f"expected 405, got {got}"))
    for host, port in allowed:
        got = proxy_status(proxy, connect_request(host, port), timeout)
        out.append(Result(f"proxy CONNECT {host}:{port} (allowlisted)", got == 200,
                          f"expected 200, got {got}"))  # fmt: skip
    return out


def parse_target(value: str) -> tuple[str, int]:
    host, _, port = value.rpartition(":")
    if not host or not port.isdigit():
        raise argparse.ArgumentTypeError(f"expected host:port, got {value!r}")
    return host.strip("[]"), int(port)


def parse_proxy(value: str) -> tuple[str, int]:
    parts = urlsplit(value if "://" in value else f"http://{value}")
    if parts.scheme != "http" or not parts.hostname:
        raise argparse.ArgumentTypeError(f"expected http://host:port, got {value!r}")
    return parts.hostname, parts.port or 3128


def run(argv: Sequence[str] | None = None, connect: Callable[..., str | None] = tcp_connect) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    p.add_argument(
        "--proxy", type=parse_proxy, help="allowlist proxy, e.g. http://egress-proxy:3128"
    )
    p.add_argument("--allowed", type=parse_target, action="append", default=[],
                   help="allowlisted host:port that must tunnel through --proxy (repeatable)")  # fmt: skip
    p.add_argument("--blocked", type=parse_target, action="append", default=None,
                   help="override the direct targets that must fail (repeatable)")  # fmt: skip
    p.add_argument("--reachable", type=parse_target, action="append", default=[],
                   help="in-sandbox host:port that must stay reachable, e.g. postgres:5432")  # fmt: skip
    p.add_argument("--expect-open", action="store_true",
                   help="control mode: direct HTTPS egress must WORK (no SMTP attempted)")  # fmt: skip
    p.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    args = p.parse_args(argv)
    if args.expect_open:
        results = check_direct_open(args.blocked or CONTROL_TARGETS, args.timeout, connect)
    else:
        targets = args.blocked or default_blocked_targets()
        results = check_direct_blocked(targets, args.timeout, connect)
        if args.proxy is not None:
            results += check_proxy(args.proxy, args.allowed, args.timeout)
        results += check_direct_open(args.reachable, args.timeout, connect, "internal")
    for r in results:
        print(f"{'PASS' if r.ok else 'FAIL'}  {r.name}: {r.detail}")
    failed = [r for r in results if not r.ok]
    print(f"{len(results) - len(failed)}/{len(results)} egress checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(run())
