"""App factory. Workstreams own their routers; this file is owned by the integrator."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI

from mailsim import __version__
from mailsim.api.errors import install_error_handlers
from mailsim.api.interface import InterfaceAttribution
from mailsim.api.limits import BodySizeLimit
from mailsim.api.local_mode import LoopbackOnly
from mailsim.api.routers import control, mailbox, simulator, ux

API_TITLE = "Mail API"
API_VERSION = "1.0.0-wave0"


def create_app(**state: Any) -> FastAPI:
    """`state` (engine, session factory, blob store, settings) is attached to app.state."""
    app = FastAPI(
        title=API_TITLE,
        version=API_VERSION,
        description="Mail API.",
    )
    for key, value in state.items():
        setattr(app.state, key, value)
    install_error_handlers(app)
    app.add_middleware(BodySizeLimit)  # WS-A: request body size cap (api/limits.py)
    app.add_middleware(LoopbackOnly)  # no-op unless local no-auth mode (api/local_mode.py)
    app.add_middleware(InterfaceAttribution)  # actions.interface (api/interface.py)

    @app.get("/v1/health", tags=["meta"])
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    for module in (control, simulator, mailbox, ux):
        app.include_router(module.router)
    return app
