"""Allowlist egress proxy: the ONLY route from the Compose `sandbox` network to the outside.

Simulator containers sit on the internal `sandbox` network (no default route, no external
DNS). This proxy is the single container attached to both `sandbox` and the routable `egress`
network, and it forwards nothing but HTTPS ``CONNECT`` tunnels to an explicit host:port
allowlist (the configured LLM endpoint). Everything else is refused:

- any method other than ``CONNECT`` (plain HTTP forwarding, absolute-URI GETs): 405;
- a ``CONNECT`` to a host not on the allowlist, or to a port not allowed for it (SMTP 25/465/587
  included): 403;
- malformed or oversized requests: 400 / 431.

The allowlist is exact host match (case-insensitive, one trailing dot ignored, no wildcards,
no suffix matching), so ``evil.api.anthropic.com`` or an IP literal is not allowed unless
listed verbatim. Standard library only, so the image needs nothing beyond ``python:3.12-slim``.

Environment:

- ``EGRESS_ALLOWLIST``: comma-separated ``host[:port]`` entries; port defaults to 443
  (default ``api.anthropic.com:443``).
- ``EGRESS_LISTEN_HOST`` / ``EGRESS_LISTEN_PORT``: bind address (default ``127.0.0.1:3128``;
  Compose sets ``0.0.0.0`` because only the internal network can reach it).
- ``EGRESS_CONNECT_TIMEOUT`` (seconds, default 10), ``EGRESS_MAX_CONNECTIONS`` (default 256).

This file lives outside ``src/`` on purpose: the simulator package bans network clients.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import sys
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

LOG = logging.getLogger("egress-proxy")
DEFAULT_ALLOWLIST = "api.anthropic.com:443"
MAX_HEADER_BYTES = 16 * 1024
HEADER_TIMEOUT = 10.0
CHUNK = 64 * 1024


@dataclass(frozen=True)
class Config:
    allow: frozenset[tuple[str, int]]
    listen_host: str = "127.0.0.1"
    listen_port: int = 3128
    connect_timeout: float = 10.0
    max_connections: int = 256

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Config:
        e = os.environ if env is None else env
        return cls(
            allow=parse_allowlist(e.get("EGRESS_ALLOWLIST", DEFAULT_ALLOWLIST).split(",")),
            listen_host=e.get("EGRESS_LISTEN_HOST", "127.0.0.1"),
            listen_port=int(e.get("EGRESS_LISTEN_PORT", "3128")),
            connect_timeout=float(e.get("EGRESS_CONNECT_TIMEOUT", "10")),
            max_connections=int(e.get("EGRESS_MAX_CONNECTIONS", "256")),
        )


def normalise_host(host: str) -> str:
    host = host.strip().lower()
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    return host.removesuffix(".")


def split_hostport(value: str, default_port: int | None) -> tuple[str, int]:
    """``host:port`` / ``[v6]:port`` / ``host`` (only with a default port) -> (host, port)."""
    value = value.strip()
    if value.startswith("["):
        end = value.find("]")
        if end < 0:
            raise ValueError(f"bad host: {value!r}")
        host, rest = value[1:end], value[end + 1 :]
        if rest and not rest.startswith(":"):
            raise ValueError(f"bad host: {value!r}")
        port_text = rest[1:] if rest else ""
    elif value.count(":") == 1:
        host, port_text = value.split(":")
    elif ":" in value:
        raise ValueError(f"IPv6 literal must be bracketed: {value!r}")
    else:
        host, port_text = value, ""
    if not port_text:
        if default_port is None:
            raise ValueError(f"missing port: {value!r}")
        port = default_port
    elif port_text.isdigit():
        port = int(port_text)
    else:
        raise ValueError(f"bad port: {value!r}")
    host = normalise_host(host)
    if not host or not 0 < port < 65536:
        raise ValueError(f"bad host/port: {value!r}")
    return host, port


def parse_allowlist(entries: Iterable[str]) -> frozenset[tuple[str, int]]:
    return frozenset(split_hostport(e, 443) for e in entries if e.strip())


def decide(config: Config, request_line: str) -> tuple[int, str, tuple[str, int] | None]:
    """(status, reason, target): status 200 means open a tunnel to target."""
    parts = request_line.split()
    if len(parts) != 3 or not parts[2].startswith("HTTP/1."):
        return 400, "Bad Request", None
    method, authority, _version = parts
    if method.upper() != "CONNECT":
        return 405, "Method Not Allowed (CONNECT to allowlisted hosts only)", None
    try:
        target = split_hostport(authority, None)
    except ValueError:
        return 400, "Bad Request", None
    if target not in config.allow:
        return 403, "Forbidden (destination not on the egress allowlist)", None
    return 200, "Connection Established", target


async def _read_head(reader: asyncio.StreamReader) -> bytes | None:
    """The request line + headers, or None when oversized/closed."""
    try:
        head = await reader.readuntil(b"\r\n\r\n")
    except asyncio.LimitOverrunError:
        return None
    except asyncio.IncompleteReadError as exc:
        return exc.partial or None
    return head


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while data := await reader.read(CHUNK):
            writer.write(data)
            await writer.drain()
    except (ConnectionError, OSError):
        pass
    finally:
        with contextlib.suppress(ConnectionError, OSError, RuntimeError):
            if writer.can_write_eof():
                writer.write_eof()


async def _respond(writer: asyncio.StreamWriter, status: int, reason: str) -> None:
    body = f"{status} {reason}\n".encode()
    writer.write(
        f"HTTP/1.1 {status} {reason}\r\nContent-Type: text/plain\r\n"
        f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()
        + body
    )
    with contextlib.suppress(ConnectionError, OSError):
        await writer.drain()


class EgressProxy:
    def __init__(self, config: Config) -> None:
        self.config = config
        self._slots = asyncio.Semaphore(config.max_connections)
        self.server: asyncio.Server | None = None

    async def start(self) -> asyncio.Server:
        self.server = await asyncio.start_server(
            self.handle, self.config.listen_host, self.config.listen_port, limit=MAX_HEADER_BYTES
        )
        return self.server

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")
        try:
            if self._slots.locked():
                await _respond(writer, 503, "Service Unavailable")
                return
            async with self._slots:
                await self._handle(reader, writer, peer)
        finally:
            writer.close()
            with contextlib.suppress(ConnectionError, OSError):
                await writer.wait_closed()

    async def _handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, peer: object
    ) -> None:
        try:
            head = await asyncio.wait_for(_read_head(reader), HEADER_TIMEOUT)
        except TimeoutError:
            await _respond(writer, 408, "Request Timeout")
            return
        if head is None:
            await _respond(writer, 431, "Request Header Fields Too Large")
            return
        line = head.split(b"\r\n", 1)[0].decode("latin-1")
        status, reason, target = decide(self.config, line)
        LOG.info("%s %r -> %d", peer, line[:200], status)
        if target is None:
            await _respond(writer, status, reason)
            return
        try:
            up_reader, up_writer = await asyncio.wait_for(
                asyncio.open_connection(*target), self.config.connect_timeout
            )
        except (OSError, TimeoutError):
            await _respond(writer, 502, "Bad Gateway")
            return
        writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        await writer.drain()
        try:
            await asyncio.gather(_pipe(reader, up_writer), _pipe(up_reader, writer))
        finally:
            up_writer.close()
            with contextlib.suppress(ConnectionError, OSError):
                await up_writer.wait_closed()


async def serve(config: Config) -> None:
    proxy = EgressProxy(config)
    server = await proxy.start()
    allowed = ", ".join(f"{h}:{p}" for h, p in sorted(config.allow)) or "(nothing)"
    LOG.info(
        "egress proxy on %s:%d; allowlist: %s", config.listen_host, config.listen_port, allowed
    )
    async with server:
        await server.serve_forever()


def main() -> int:  # pragma: no cover - process entry point
    logging.basicConfig(level=logging.INFO, stream=sys.stdout, format="%(asctime)s %(message)s")
    asyncio.run(serve(Config.from_env()))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
