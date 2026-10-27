"""App factory. Workstreams own their routers; this file is owned by the integrator."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import HTMLResponse, JSONResponse

from mailsim import __version__
from mailsim.api.deps import DocsReader
from mailsim.api.errors import install_error_handlers
from mailsim.api.interface import InterfaceAttribution
from mailsim.api.limits import BodySizeLimit
from mailsim.api.local_mode import LoopbackOnly
from mailsim.api.routers import control, mailbox, simulator, ux
from mailsim.api.schemas import HealthOut

API_TITLE = "Mail API"
API_VERSION = "1.0.0-wave0"


def create_app(**state: Any) -> FastAPI:
    """`state` (engine, session factory, blob store, settings) is attached to app.state."""
    app = FastAPI(
        title=API_TITLE,
        version=API_VERSION,
        description="Mail API.",
        # served below, to operators only (the description names operator and grading routes)
        openapi_url=None,
        docs_url=None,
        redoc_url=None,
    )
    for key, value in state.items():
        setattr(app.state, key, value)
    install_error_handlers(app)
    app.add_middleware(BodySizeLimit)  # WS-A: request body size cap (api/limits.py)
    app.add_middleware(LoopbackOnly)  # no-op unless local no-auth mode (api/local_mode.py)
    app.add_middleware(InterfaceAttribution)  # actions.interface (api/interface.py)

    @app.get("/v1/health", tags=["meta"])
    def health(request: Request) -> HealthOut:
        """Public liveness check. `local_no_auth` lets the dashboard learn, without a failing
        request, whether to ask for the server's launch link (local no-auth mode)."""
        local = getattr(request.app.state, "local_access", None) is not None
        return HealthOut(status="ok", version=__version__, local_no_auth=local)

    @app.get("/openapi.json", include_in_schema=False)
    def openapi_document(request: Request, _: DocsReader) -> JSONResponse:
        return JSONResponse(request.app.openapi())

    @app.get("/docs", include_in_schema=False)
    def openapi_docs(request: Request, _: DocsReader) -> HTMLResponse:
        # the document is embedded: the page's own fetch of /openapi.json could not send a key
        return get_swagger_ui_html(openapi_url="", title=API_TITLE,
                                   swagger_ui_parameters={"spec": request.app.openapi()})  # fmt: skip

    for module in (control, simulator, mailbox, ux):
        app.include_router(module.router)
    return app
