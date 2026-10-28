"""Allowlist egress proxy + in-container egress probe, exercised on loopback only.

The network-level guarantee itself (no route out of the `sandbox` network) is proven by the
`egress` CI job, which runs tests/egress/egress_check.py inside the simulator containers. These
tests pin the proxy's allowlist decisions and the probe's pass/fail logic without any external
connection: every socket here is on 127.0.0.1.
"""

from __future__ import annotations

import asyncio
import errno
import socket
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml
from deploy.egress_proxy import proxy as px

from tests.egress import egress_check as ec

ALLOW = "api.anthropic.com:443"


def cfg(allow: str = ALLOW, **kw: object) -> px.Config:
    return px.Config(allow=px.parse_allowlist(allow.split(",")), **kw)  # type: ignore[arg-type]


# --- allowlist parsing and decisions ---------------------------------------------------------


def test_parse_allowlist_defaults_port_and_normalises() -> None:
    allow = px.parse_allowlist(["API.Anthropic.com.", " llm.internal:8443 ", "[::1]:9000", ""])
    assert allow == {("api.anthropic.com", 443), ("llm.internal", 8443), ("::1", 9000)}
    assert px.parse_allowlist(["[::1]"]) == {("::1", 443)}


@pytest.mark.parametrize(
    "bad", ["[::1", "[::1]x", "::1:443", "host:", "host:abc", ":443", "host:0", "host:70000"]
)
def test_split_hostport_rejects(bad: str) -> None:
    with pytest.raises(ValueError):
        px.split_hostport(bad, None if bad.endswith(":") else 443)


def test_split_hostport_requires_port_without_default() -> None:
    with pytest.raises(ValueError):
        px.split_hostport("api.anthropic.com", None)


def test_config_from_env() -> None:
    c = px.Config.from_env({"EGRESS_ALLOWLIST": "a.test:1,b.test", "EGRESS_LISTEN_HOST": "0.0.0.0",
                            "EGRESS_LISTEN_PORT": "9", "EGRESS_CONNECT_TIMEOUT": "2",
                            "EGRESS_MAX_CONNECTIONS": "3"})  # fmt: skip
    assert c.allow == {("a.test", 1), ("b.test", 443)}
    assert (c.listen_host, c.listen_port, c.connect_timeout, c.max_connections) == (
        "0.0.0.0", 9, 2.0, 3)  # fmt: skip
    assert px.Config.from_env({}).allow == {("api.anthropic.com", 443)}


@pytest.mark.parametrize(
    ("line", "status"),
    [
        ("CONNECT api.anthropic.com:443 HTTP/1.1", 200),
        ("connect API.ANTHROPIC.COM.:443 HTTP/1.1", 200),
        ("CONNECT api.anthropic.com:80 HTTP/1.1", 403),
        ("CONNECT smtp.gmail.com:25 HTTP/1.1", 403),
        ("CONNECT smtp.gmail.com:465 HTTP/1.1", 403),
        ("CONNECT smtp.gmail.com:587 HTTP/1.1", 403),
        ("CONNECT evil.api.anthropic.com:443 HTTP/1.1", 403),
        ("CONNECT api.anthropic.com.evil.test:443 HTTP/1.1", 403),
        ("CONNECT 1.1.1.1:443 HTTP/1.1", 403),
        ("CONNECT [2606:4700::1111]:443 HTTP/1.1", 403),
        ("GET http://example.com/ HTTP/1.1", 405),
        ("GET https://api.anthropic.com/ HTTP/1.1", 405),
        ("POST / HTTP/1.1", 405),
        ("CONNECT api.anthropic.com HTTP/1.1", 400),
        ("CONNECT api.anthropic.com:443", 400),
        ("CONNECT api.anthropic.com:443 SMTP/1.0", 400),
        ("", 400),
    ],
)
def test_decide(line: str, status: int) -> None:
    got, _reason, target = px.decide(cfg(), line)
    assert got == status
    assert (target is not None) == (status == 200)


# --- the proxy on loopback -------------------------------------------------------------------


class Loop:
    """An asyncio loop in a background thread hosting an echo upstream and the proxy."""

    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()

    def call(self, coro: object) -> object:
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(10)  # type: ignore[arg-type]

    def close(self) -> None:
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(5)
        self.loop.close()


async def _echo(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    while data := await reader.read(1024):
        writer.write(data)
        await writer.drain()
    writer.close()


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture
def stack() -> Iterator[tuple[Loop, int, int, px.EgressProxy]]:
    lp = Loop()

    async def start() -> tuple[int, int, px.EgressProxy]:
        upstream = await asyncio.start_server(_echo, "127.0.0.1", 0)
        up_port = upstream.sockets[0].getsockname()[1]
        proxy = px.EgressProxy(cfg(f"127.0.0.1:{up_port}", listen_port=0, connect_timeout=2))
        server = await proxy.start()
        return up_port, server.sockets[0].getsockname()[1], proxy

    up_port, proxy_port, proxy = lp.call(start())  # type: ignore[misc]
    yield lp, up_port, proxy_port, proxy
    lp.close()


def _ask(port: int, request: bytes) -> tuple[socket.socket, bytes]:
    sock = socket.create_connection(("127.0.0.1", port), timeout=5)
    sock.sendall(request)
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = sock.recv(1024)
        if not chunk:
            break
        data += chunk
    return sock, data


def test_proxy_tunnels_only_allowlisted(stack: tuple[Loop, int, int, px.EgressProxy]) -> None:
    _lp, up_port, proxy_port, _ = stack
    sock, head = _ask(proxy_port, ec.connect_request("127.0.0.1", up_port))
    with sock:
        assert head.startswith(b"HTTP/1.1 200 ")
        sock.sendall(b"ping")
        assert sock.recv(16) == b"ping"
    for request in (ec.connect_request("127.0.0.1", up_port + 1 if up_port < 65535 else 1),
                    ec.connect_request("smtp.gmail.com", 587),
                    b"GET http://example.com/ HTTP/1.1\r\nHost: example.com\r\n\r\n"):  # fmt: skip
        sock, head = _ask(proxy_port, request)
        sock.close()
        assert head.split()[1] in (b"403", b"405")


def test_proxy_upstream_down_is_502() -> None:
    lp = Loop()
    try:
        dead = _free_port()
        proxy = px.EgressProxy(cfg(f"127.0.0.1:{dead}", listen_port=0, connect_timeout=2))
        server = lp.call(proxy.start())
        port = server.sockets[0].getsockname()[1]  # type: ignore[attr-defined]
        sock, head = _ask(port, ec.connect_request("127.0.0.1", dead))
        sock.close()
        assert head.startswith(b"HTTP/1.1 502 ")
    finally:
        lp.close()


def test_proxy_rejects_oversized_and_truncated_heads(
    stack: tuple[Loop, int, int, px.EgressProxy],
) -> None:
    _lp, _up, proxy_port, _ = stack
    sock, head = _ask(proxy_port, b"CONNECT " + b"a" * (px.MAX_HEADER_BYTES + 10))
    sock.close()
    assert head.startswith(b"HTTP/1.1 431 ")
    sock = socket.create_connection(("127.0.0.1", proxy_port), timeout=5)
    with sock:
        sock.sendall(b"GET / HTTP/1.1\r\n")
        sock.shutdown(socket.SHUT_WR)
        assert sock.recv(64).startswith(b"HTTP/1.1 405 ")
    sock = socket.create_connection(("127.0.0.1", proxy_port), timeout=5)
    with sock:
        sock.shutdown(socket.SHUT_WR)
        assert sock.recv(64).startswith(b"HTTP/1.1 431 ")


def test_proxy_header_timeout(
    stack: tuple[Loop, int, int, px.EgressProxy], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(px, "HEADER_TIMEOUT", 0.2)
    _lp, _up, proxy_port, _ = stack
    with socket.create_connection(("127.0.0.1", proxy_port), timeout=5) as sock:
        assert sock.recv(64).startswith(b"HTTP/1.1 408 ")


def test_proxy_connection_cap(stack: tuple[Loop, int, int, px.EgressProxy]) -> None:
    lp, _up, proxy_port, proxy = stack

    async def exhaust() -> None:
        proxy._slots = asyncio.Semaphore(0)

    lp.call(exhaust())
    sock, head = _ask(proxy_port, b"")
    sock.close()
    assert head.startswith(b"HTTP/1.1 503 ")


# --- the in-container probe ------------------------------------------------------------------


def test_default_targets_cover_smtp_and_http_ports() -> None:
    targets = ec.default_blocked_targets()
    for port in (25, 465, 587, 80, 443):
        assert any(p == port and h[0].isdigit() for h, p in targets), port  # IP literal
        assert any(p == port and not h[0].isdigit() for h, p in targets), port  # by name


def test_classify() -> None:
    assert ec.classify(socket.gaierror(-2, "x")) == "dns-failure"
    assert ec.classify(TimeoutError()) == "timeout"
    assert ec.classify(ConnectionRefusedError()) == "refused"
    assert ec.classify(OSError(errno.ENETUNREACH, "x")) == "no-route"
    assert ec.classify(OSError(errno.EHOSTUNREACH, "x")) == "no-route"
    assert ec.classify(OSError(errno.EACCES, "x")).startswith("error:")


def test_tcp_connect_loopback() -> None:
    with socket.socket() as srv:
        srv.bind(("127.0.0.1", 0))
        srv.listen()
        assert ec.tcp_connect("127.0.0.1", srv.getsockname()[1], 2) is None
    assert ec.tcp_connect("127.0.0.1", _free_port(), 2) == "refused"


def test_run_blocked_and_open_modes(capsys: pytest.CaptureFixture[str]) -> None:
    def closed(host: str, port: int, timeout: float) -> str | None:
        return "no-route"

    def open_(host: str, port: int, timeout: float) -> str | None:
        return None

    assert ec.run([], connect=closed) == 0
    assert "18/18 egress checks passed" in capsys.readouterr().out
    assert ec.run(["--blocked", "smtp.gmail.com:587"], connect=open_) == 1
    assert "FAIL  direct smtp.gmail.com:587: CONNECTED" in capsys.readouterr().out
    assert ec.run(["--blocked", "1.1.1.1:25", "--reachable", "postgres:5432"], connect=closed) == 1
    assert "FAIL  internal postgres:5432: no-route" in capsys.readouterr().out
    assert ec.run(["--expect-open"], connect=open_) == 0
    assert ec.run(["--expect-open"], connect=closed) == 1
    capsys.readouterr()


def test_run_with_proxy(stack: tuple[Loop, int, int, px.EgressProxy]) -> None:
    _lp, up_port, proxy_port, _ = stack
    args = ["--blocked", "127.0.0.1:1", "--proxy", f"http://127.0.0.1:{proxy_port}",
            "--allowed", f"127.0.0.1:{up_port}"]  # fmt: skip
    assert ec.run(args, connect=lambda *a: "no-route") == 0
    # an allowlisted endpoint the proxy does not allow fails the probe
    assert ec.run(args[:-1] + ["127.0.0.1:2"], connect=lambda *a: "no-route") == 1


def test_proxy_status_errors() -> None:
    assert str(ec.proxy_status(("127.0.0.1", _free_port()), b"x", 2)).startswith(
        "proxy unreachable")  # fmt: skip
    with socket.socket() as srv:
        srv.bind(("127.0.0.1", 0))
        srv.listen()

        def reply() -> None:
            conn, _ = srv.accept()
            with conn:
                conn.recv(64)
                conn.sendall(b"garbage")

        t = threading.Thread(target=reply)
        t.start()
        got = ec.proxy_status(srv.getsockname(), b"CONNECT x:1 HTTP/1.1\r\n\r\n", 2)
        t.join(5)
    assert str(got).startswith("unparseable proxy reply")


@pytest.mark.parametrize("value", ["nohost", "host:port", ":443"])
def test_parse_target_rejects(value: str) -> None:
    with pytest.raises(Exception, match="host:port"):
        ec.parse_target(value)


def test_parse_target_and_proxy() -> None:
    assert ec.parse_target("[::1]:443") == ("::1", 443)
    assert ec.parse_proxy("http://egress-proxy:3128") == ("egress-proxy", 3128)
    assert ec.parse_proxy("egress-proxy") == ("egress-proxy", 3128)
    with pytest.raises(Exception, match="http://"):
        ec.parse_proxy("socks5://x:1")


# --- the Compose topology the CI job proves --------------------------------------------------


def test_compose_only_the_proxy_bridges_sandbox_to_a_routable_network() -> None:
    root = Path(__file__).resolve().parents[2]
    compose = yaml.safe_load((root / "docker-compose.yml").read_text())
    networks, services = compose["networks"], compose["services"]
    routable = {n for n, spec in networks.items() if not (spec or {}).get("internal")}
    assert networks["sandbox"]["internal"] is True
    bridges = {name for name, svc in services.items() if set(svc["networks"]) & routable}
    # postgres needs host-access for the published loopback port and runs no simulator code
    assert bridges == {"postgres", "egress-proxy"}
    assert services["postgres"]["networks"] == ["sandbox", "host-access"]
    assert services["egress-proxy"]["networks"] == ["sandbox", "egress"]
    assert "ports" not in services["egress-proxy"]
    simulator = set(services) - bridges
    assert {"app", "api", "egress-check"} <= simulator
    for name in simulator:
        svc = services[name]
        assert svc["networks"] == ["sandbox"], name
        assert "ports" not in svc and "network_mode" not in svc, name
        env = svc["environment"]
        assert env["HTTPS_PROXY"] == env["HTTP_PROXY"] == "http://egress-proxy:3128", name
        assert env["MAILSIM_ENV"] == "deployment", name
        assert "egress-proxy" in svc["depends_on"], name
    mount = services["egress-check"]["volumes"][0].split(":")
    assert (root / mount[0]).resolve() == Path(ec.__file__).resolve() and mount[-1] == "ro"
    proxy_src = root / services["egress-proxy"]["build"] / "proxy.py"
    assert proxy_src.resolve() == Path(px.__file__).resolve()
