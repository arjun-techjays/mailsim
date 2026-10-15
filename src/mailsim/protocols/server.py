"""Run the IMAP and SMTP listeners for a simulator engine.

`run(engine, ...)` blocks serving both listeners on one event loop (the integrator's
`serve protocols` subcommand builds the engine and calls it; this package may not import
`mailsim.engine.core`). `ProtocolServers` runs them on a private background thread, which is
what tests and embedding hosts use. Listeners bind to loopback by default; they are inbound
servers only and hand every message to the agent facade.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
from typing import Any

from mailsim.protocols.auth import Authenticator
from mailsim.protocols.imap import IDLE_TIMEOUT, LINE_LIMIT, ImapConnection, ImapSession
from mailsim.protocols.smtp import smtp_factory
from mailsim.protocols.tls import TLSContext, self_signed_context

log = logging.getLogger(__name__)


class ProtocolServers:
    """IMAP + SMTP listeners over one engine. Port 0 picks a free port (see the properties)."""

    def __init__(self, engine: Any, *, host: str = "127.0.0.1", imap_port: int = 1143,
                 smtp_port: int = 1587, tls: bool = False, tls_context: TLSContext | None = None,
                 hostname: str = "localhost", session_factory: Any = None,
                 blobs: Any = None, idle_timeout: float = IDLE_TIMEOUT) -> None:  # fmt: skip
        self.host = host
        self.hostname = hostname
        self.idle_timeout = idle_timeout
        self.authenticator = Authenticator(engine, session_factory)
        self.blobs = blobs if blobs is not None else getattr(engine, "blobs", None)
        self.tls_context = tls_context or (self_signed_context(hostname) if tls else None)
        self._ports = {"imap": imap_port, "smtp": smtp_port}
        self._servers: list[asyncio.Server] = []
        self._clients: set[asyncio.Task[None]] = set()
        self._transports: set[asyncio.BaseTransport] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None

    @property
    def imap_port(self) -> int:
        return self._ports["imap"]

    @property
    def smtp_port(self) -> int:
        return self._ports["smtp"]

    async def _imap_client(self, reader: asyncio.StreamReader,
                           writer: asyncio.StreamWriter) -> None:  # fmt: skip
        task = asyncio.current_task()
        assert task is not None
        self._clients.add(task)
        self._transports.add(writer.transport)
        try:
            session = ImapSession(self.authenticator, self.blobs,
                                  tls_available=self.tls_context is not None)  # fmt: skip
            await ImapConnection(session, reader, writer, tls_context=self.tls_context,
                                 idle_timeout=self.idle_timeout).run()  # fmt: skip
        finally:
            self._clients.discard(task)
            self._transports.discard(writer.transport)

    async def start_async(self) -> None:
        """Bind both listeners on the running loop."""
        loop = asyncio.get_running_loop()
        imap = await asyncio.start_server(self._imap_client, self.host, self._ports["imap"],
                                          limit=LINE_LIMIT)  # fmt: skip
        factory = smtp_factory(self.authenticator, tls_context=self.tls_context,
                               hostname=self.hostname, on_connect=self._transports.add)  # fmt: skip
        smtp = await loop.create_server(factory, self.host, self._ports["smtp"])
        self._servers = [imap, smtp]
        self._ports = {"imap": imap.sockets[0].getsockname()[1],
                       "smtp": smtp.sockets[0].getsockname()[1]}  # fmt: skip
        log.info("IMAP on %s:%d, SMTP on %s:%d (tls=%s)", self.host, self.imap_port, self.host,
                 self.smtp_port, self.tls_context is not None)  # fmt: skip

    async def stop_async(self) -> None:
        for server in self._servers:
            server.close()
        for transport in list(self._transports):  # open client connections end with the server
            transport.close()
        self._transports.clear()
        for task in list(self._clients):
            task.cancel()
        await asyncio.gather(*self._clients, return_exceptions=True)
        for server in self._servers:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(server.wait_closed(), 2)
        self._servers = []

    async def serve_forever(self) -> None:
        await self.start_async()
        try:
            await asyncio.Event().wait()
        finally:
            await self.stop_async()

    # ---------------------------------------------------------------- background thread
    def start(self) -> ProtocolServers:
        ready = threading.Event()
        errors: list[BaseException] = []

        def main() -> None:
            loop = asyncio.new_event_loop()
            self._loop = loop
            try:
                loop.run_until_complete(self.start_async())
            except BaseException as exc:  # noqa: BLE001 - reported to the starting thread
                errors.append(exc)
                ready.set()
                loop.close()
                return
            ready.set()
            loop.run_forever()
            loop.run_until_complete(self.stop_async())
            loop.close()

        self._thread = threading.Thread(target=main, name="mailsim-protocols", daemon=True)
        self._thread.start()
        ready.wait()
        if errors:
            raise errors[0]
        return self

    def stop(self) -> None:
        if self._loop is not None and self._thread is not None:
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._thread.join(timeout=10)
        self._loop, self._thread = None, None

    def __enter__(self) -> ProtocolServers:
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.stop()


def run(engine: Any, host: str = "127.0.0.1", imap_port: int = 1143, smtp_port: int = 1587,
        tls: bool = False, *, tls_context: TLSContext | None = None,
        hostname: str = "localhost") -> None:  # fmt: skip
    """Serve IMAP + SMTP for `engine` until interrupted (entry point for `serve protocols`)."""
    servers = ProtocolServers(engine, host=host, imap_port=imap_port, smtp_port=smtp_port,
                              tls=tls, tls_context=tls_context, hostname=hostname)  # fmt: skip
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(servers.serve_forever())


__all__ = ["ProtocolServers", "run"]
