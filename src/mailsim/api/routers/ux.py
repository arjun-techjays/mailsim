"""Dashboard setup and connection flows (UX story). Operator-side only: every route requires a
controller and/or evaluator key (or the local no-auth operator); none is reachable with an
agent key. Contract owner: integrator; implementation: UX-B.

Answer-key content leaves only through the evaluator-only document route. Connection tests
take an agent key in the request body, check that it is a live agent key of THIS run, and
make one real call through the chosen interface in-process (`api/inprocess.py`: no socket,
no HTTP client). Keys are never echoed back.
"""

from __future__ import annotations

import base64
import functools
import json
import logging
from collections.abc import Callable
from typing import Annotated, Any, Protocol

import anyio.to_thread
import yaml
from fastapi import APIRouter, FastAPI, Query, Request
from pydantic import ValidationError
from sqlalchemy import select
from starlette.routing import Match

from mailsim import __version__
from mailsim.api import authoring
from mailsim.api import schemas as s
from mailsim.api import ux_schemas as u
from mailsim.api.auth import InvalidCredential, verify_key
from mailsim.api.deps import Controller, Evaluator, Inspector
from mailsim.api.errors import ErrorEnvelope
from mailsim.api.inprocess import asgi_request
from mailsim.api.routers._common import (
    blobs,
    controller,
    inspector,
    scenario_version,
    scope,
    sessions,
)
from mailsim.auth.principal import Principal, Role
from mailsim.domain.clock import iso
from mailsim.domain.errors import InvalidRequest, SimError
from mailsim.domain.models import SYSTEM_FOLDERS, VALID_FLAGS
from mailsim.llm.config import LLMConfig
from mailsim.scenario import answer_key as ak
from mailsim.scenario import answer_keys as keys_service
from mailsim.scenario import schema as scenario_schema
from mailsim.scenario.loader import check_finite, parse_source
from mailsim.store import models as m
from mailsim.store import repos

log = logging.getLogger("mailsim.api.ux")
router = APIRouter(
    prefix="/v1", tags=["authoring"], responses={"default": {"model": ErrorEnvelope}}
)

TRIGGER_TYPES = ("at", "after_seconds", "after_agent_action")
EXTERNAL_DELIVERY_MODES = ("deny", "sink", "event")
BEHAVIOR_MODES = ("silent", "scripted", "llm")
SEVERITIES = ("mandatory", "advisory")
CATEGORIES = ("task", "safety", "integration", "efficiency", "communication")
LABELS = {"rest": "REST", "gmail": "Gmail API", "graph": "Microsoft Graph",
          "mcp_http": "MCP (HTTP)", "mcp_stdio": "MCP (stdio)", "imap": "IMAP",
          "smtp": "SMTP"}  # fmt: skip
PROBES = {"gmail": ("GET", "/gmail/v1/users/me/profile"),
          "graph": ("GET", "/graph/v1.0/me/mailFolders"), "mcp_http": ("POST", "/mcp")}  # fmt: skip
HTTP_PATHS = {"gmail": "/gmail/v1", "graph": "/graph/v1.0", "mcp_http": "/mcp"}
MCP_PROTOCOL_VERSION = "2025-11-25"
STDIO_COMMAND = ("python", "-m", "mailsim.serve", "mcp-stdio")
STDIO_NOTE = ("the agent launches this command itself: set MAILSIM_API_KEY to the agent key (never "
              "on the command line) and the server's MAILSIM_DATABASE_URL and MAILSIM_BLOB_DIR, "
              "and run it from the mailsim checkout / virtualenv (e.g. `uv run python -m "
              "mailsim.serve mcp-stdio`)")  # fmt: skip
LISTENER_DOWN = "LISTENER_DOWN"
AUTH_FAILED = {"imap": "AUTHENTICATIONFAILED", "smtp": "AUTHENTICATION_FAILED"}


class Listeners(Protocol):
    """What the API reads from `app.state.protocols` (`mailsim.protocols.ProtocolServers`,
    which this layer may not import)."""

    @property
    def host(self) -> str: ...
    @property
    def imap_port(self) -> int: ...
    @property
    def smtp_port(self) -> int: ...
    @property
    def running(self) -> bool: ...
    @property
    def tls(self) -> bool: ...
    def check_login(self, username: str, password: str, *,
                    interface: str = ...) -> tuple[bool, str | None, str]: ...  # fmt: skip


def _listeners(app: FastAPI) -> Listeners | None:
    return getattr(app.state, "protocols", None)


def _running(app: FastAPI) -> Listeners | None:
    listeners = _listeners(app)
    return listeners if listeners is not None and listeners.running else None


def _served(app: FastAPI, interface: str) -> bool:
    """Whether the app routes the interface's probe path (adapters are mounted optionally)."""
    if interface not in PROBES:
        return True  # REST /v1 is always served
    method, path = PROBES[interface]
    probe = {"type": "http", "method": method, "path": path, "root_path": "", "headers": []}
    return any(route.matches(probe)[0] != Match.NONE for route in app.routes)


def _port(listeners: Listeners, interface: str) -> int:
    return listeners.imap_port if interface == "imap" else listeners.smtp_port


def _not_running(interface: str) -> str:
    return f"{LABELS[interface]} listener is not running (start with serve, see README)"


# ---- meta --------------------------------------------------------------------------------
@router.get("/meta/authoring", response_model=u.AuthoringMeta)
def authoring_meta(principal: Inspector) -> u.AuthoringMeta:
    """Schemas and enumerations for the scenario builder and the answer-key editor."""
    return _authoring_meta()


@functools.cache
def _authoring_meta() -> u.AuthoringMeta:  # static per process: built once
    types = [
        u.AssertionTypeInfo(
            type=t, default_category=ak.DEFAULT_CATEGORY[t],
            check_schema=(ak.CHECK_MODELS[t].model_json_schema(by_alias=True)
                          if t in ak.CHECK_MODELS else None),
        )
        for t in ak.ASSERTION_TYPES
    ]  # fmt: skip
    return u.AuthoringMeta(
        scenario_schema=scenario_schema.json_schema(),
        answer_key_schema=ak.AnswerKeyDoc.model_json_schema(by_alias=True),
        schema_versions=list(scenario_schema.SUPPORTED_SCHEMA_VERSIONS),
        agent_operations=list(scenario_schema.AGENT_OPERATIONS),
        trigger_types=list(TRIGGER_TYPES),
        trigger_actions=list(scenario_schema.TRIGGER_ACTIONS),
        external_delivery_modes=list(EXTERNAL_DELIVERY_MODES),
        behavior_modes=list(BEHAVIOR_MODES),
        assertion_types=types,
        severities=list(SEVERITIES),
        categories=list(CATEGORIES),
        invariants=list(ak.INVARIANTS),
        folders=[kind for _name, kind in SYSTEM_FOLDERS],
        flags=sorted(VALID_FLAGS),
    )


def _llm(app: FastAPI) -> tuple[str, str | None]:
    provider = getattr(getattr(app.state, "llm_gateway", None), "provider", None)
    if provider is not None:
        return str(getattr(provider, "name", "unknown")), getattr(provider, "model", None) or None
    try:
        cfg = LLMConfig.from_env()
    except SimError:
        return "unconfigured", None
    return cfg.provider, cfg.model or None


@router.get("/system/status", response_model=u.SystemStatus)
def system_status(request: Request, principal: Inspector) -> u.SystemStatus:
    """Server mode, LLM provider and which interfaces/listeners are up."""
    app = request.app
    provider, model = _llm(app)
    listeners, up = _listeners(app), _running(app) is not None
    interfaces: list[u.Interface] = ["rest"]
    interfaces += [i for i in ("gmail", "graph", "mcp_http") if _served(app, i)]
    interfaces.append("mcp_stdio")
    if up:
        interfaces += ["imap", "smtp"]
    status: dict[Any, u.ListenerStatus] = {}
    for name in ("imap", "smtp"):
        status[name] = (
            u.ListenerStatus(running=False) if listeners is None
            else u.ListenerStatus(running=up, host=listeners.host, port=_port(listeners, name),
                                  tls="starttls" if listeners.tls else "none")
        )  # fmt: skip
    return u.SystemStatus(
        version=__version__, local_no_auth=getattr(app.state, "local_access", None) is not None,
        llm_provider=provider, llm_model=model, interfaces=interfaces, listeners=status,
    )  # fmt: skip


# ---- YAML tools ----------------------------------------------------------------------------
def _where(exc: InvalidRequest) -> str:
    cause = exc.__cause__
    mark = getattr(cause, "problem_mark", None) or getattr(cause, "context_mark", None)
    line = getattr(mark, "line", None)
    if line is not None:
        return f"line {line + 1}"
    lineno = getattr(cause, "lineno", None)  # json.JSONDecodeError
    return f"line {lineno}" if lineno is not None else "<source>"


@router.post("/tools/yaml-to-json", response_model=u.ParsedDocument)
def yaml_to_json(body: u.YamlToJson, principal: Inspector) -> u.ParsedDocument:
    """Parse YAML/JSON text (safe loader, same limits as publishing). Problems are reported in
    `error` (path "line N", 1-based, when the parser knows the line, else "<source>"; the
    message has the details), never as HTTP errors."""
    source = body.source
    try:
        data = parse_source(source.encode(), is_json=source.lstrip().startswith("{"))
    except InvalidRequest as exc:
        return u.ParsedDocument(error=s.ValidationIssue(path=_where(exc), message=exc.message))
    if not isinstance(data, dict):
        found = "nothing" if data is None else type(data).__name__
        return u.ParsedDocument(error=s.ValidationIssue(
            path="<source>", message=f"expected a mapping at the top level, found {found}"))  # fmt: skip
    # YAML timestamps and the like become strings: only JSON values are returned
    return u.ParsedDocument(document=json.loads(json.dumps(data, default=str)))


@router.post("/tools/json-to-yaml", response_model=u.RenderedYaml)
def json_to_yaml(body: u.JsonToYaml, principal: Inspector) -> u.RenderedYaml:
    """Render a document as YAML (stable key order: the form's order)."""
    check_finite(body.document)
    try:
        text = yaml.safe_dump(body.document, sort_keys=False, allow_unicode=True,
                              default_flow_style=False)  # fmt: skip
    except RecursionError:
        raise InvalidRequest("the document is nested too deeply") from None
    if len(text) > s.MAX_SOURCE_CHARS:
        raise InvalidRequest("the rendered YAML is larger than a scenario source may be")
    return u.RenderedYaml(source=text)


# ---- scenario drafts ----------------------------------------------------------------------
@router.post("/scenario-drafts/validate", response_model=u.DraftValidation)
def validate_draft(body: u.ScenarioDraft, principal: Controller) -> u.DraftValidation:
    """Validate an unsaved scenario (schema, references, fixtures). Stores nothing.

    Issues carry dotted paths (`scenario.personas.1.email`; `<source>` for text that does not
    parse). Malformed requests (both or neither of source/document, bad fixture base64 or
    paths) answer 400 INVALID_REQUEST."""
    report = authoring.validate_draft(body)
    return u.DraftValidation(ok=not report.errors, errors=report.errors,
                             warnings=report.warnings, document=report.document)  # fmt: skip


@router.post("/scenario-drafts/compose-eml", response_model=u.ComposedFixture)
def compose_eml(body: u.ComposeMessage, principal: Controller) -> u.ComposedFixture:
    """Build an .eml fixture from a composed message (threading headers, attachments).
    `date` defaults to the current UTC time, `message_id` to `<uuid@domain of from>`, the path
    to `fixtures/<subject slug>-<id prefix>.eml`. CR/LF in any header field: 400."""
    return authoring.compose_eml(body)


@router.post("/scenario-drafts/inspect-eml", response_model=u.EmlSummary)
def inspect_eml(body: u.EmlInspect, principal: Controller) -> u.EmlSummary:
    """Summarise an uploaded .eml for display in the form."""
    return authoring.summarize_eml(body.content_b64)


@router.get("/scenarios/{scenario_key}/versions/{version}/document",
            response_model=u.ScenarioVersionDocument)  # fmt: skip
def scenario_version_document(
    request: Request, scenario_key: str, version: int, principal: Controller
) -> u.ScenarioVersionDocument:
    """A published version's (agent-facing) document and fixtures, for editing or cloning."""
    store = blobs(request)
    with sessions(request)() as session:
        sv = scenario_version(session, principal, scenario_key, version)
        spec, manifest, digest = dict(sv.spec), dict(sv.fixtures), sv.spec_hash
    fixtures = {}
    for rel, ref in sorted(manifest.items()):
        if store.exists(ref):
            fixtures[rel] = base64.b64encode(store.get(ref)).decode()
        else:  # the blob store lost it: still show the document
            log.warning("fixture %s of %s v%s is missing from the blob store", rel,
                        scenario_key, version)  # fmt: skip
    return u.ScenarioVersionDocument(scenario_key=scenario_key, version=version,
                                     spec_hash=digest, document=spec, fixtures=fixtures)  # fmt: skip


# ---- answer keys --------------------------------------------------------------------------
@router.post("/scenarios/{scenario_key}/answer-keys/validate",
             response_model=u.AnswerKeyValidation)  # fmt: skip
def validate_answer_key(
    request: Request, scenario_key: str, body: u.AnswerKeyDraft, principal: Inspector
) -> u.AnswerKeyValidation:
    """Schema + leak guard for an unsaved answer key. Stores nothing.

    Errors: dotted schema paths (`answer_key.assertions.0.check`), `answer_key.scenario` for a
    key of another scenario, `answer_key.scenario_versions` for unknown versions, and
    `leak:<scenario version>` when grading prose appears in content the agent can read (the
    leaked text is cut to 60 characters). 404 when the scenario does not exist."""
    if (body.source is None) == (body.document is None):
        raise InvalidRequest("provide exactly one of `source` or `document`")
    try:
        data = authoring.answer_key_data(body.source, body.document)
    except InvalidRequest as exc:
        issue = s.ValidationIssue(path="<source>", message=exc.message)
        return u.AnswerKeyValidation(ok=False, errors=[issue], warnings=[])
    document = json.loads(json.dumps(data, default=str)) if isinstance(data, dict) else None
    try:
        doc = ak.AnswerKeyDoc.model_validate(data)
    except ValidationError as exc:
        errors = [s.ValidationIssue(path=e.path, message=e.message)
                  for e in authoring.answer_key_errors(exc, data)]  # fmt: skip
        return u.AnswerKeyValidation(ok=False, errors=errors, warnings=[], document=document)
    key = doc.answer_key
    errors, warnings = [], []
    if key.scenario != scenario_key:
        errors.append(s.ValidationIssue(
            path="answer_key.scenario",
            message=f"answer key is for {key.scenario!r}, not {scenario_key!r}"))  # fmt: skip
    if not key.assertions:
        warnings.append(s.ValidationIssue(path="answer_key.assertions",
                                          message="the answer key has no assertions"))  # fmt: skip
    with sessions(request)() as session:
        unknown, leaks = keys_service.check_answer_key(session, scope(principal), scenario_key,
                                                       key, blobs(request))  # fmt: skip
    if unknown:
        errors.append(s.ValidationIssue(path="answer_key.scenario_versions",
                                        message=f"unknown scenario versions {unknown}"))  # fmt: skip
    errors += [
        s.ValidationIssue(path=f"leak:{version}",
                          message="answer-key text appears in agent-visible content of scenario "
                                  f"version {version}: {text[:60]!r}")
        for version, texts in leaks for text in texts
    ]  # fmt: skip
    return u.AnswerKeyValidation(ok=not errors, errors=errors, warnings=warnings,
                                 document=document)  # fmt: skip


@router.get("/scenarios/{scenario_key}/answer-keys/{version}/document",
            response_model=u.AnswerKeyDocument)  # fmt: skip
def answer_key_document(
    request: Request, scenario_key: str, version: int, principal: Evaluator
) -> u.AnswerKeyDocument:
    """Evaluator-only: the full answer key, for editing."""
    with sessions(request)() as session:
        row = keys_service.get_answer_key(session, scope(principal), scenario_key, version)
        return u.AnswerKeyDocument(scenario_key=scenario_key, version=row.version,
                                   spec_hash=row.spec_hash, document=dict(row.spec))  # fmt: skip


# ---- connecting an agent ------------------------------------------------------------------
def _agent_mailbox(request: Request, principal: Principal,
                   run_id: str) -> tuple[str, str, str, str]:  # fmt: skip
    """(run status, mailbox id, address, persona id) of the run's agent mailbox (scoped)."""
    ctl = controller(request, principal)
    with sessions(request)() as session:
        run = repos.get_run(session, scope(principal), run_id)
        sv = session.get(m.ScenarioVersion, run.scenario_version_id)
        assert sv is not None  # every run is created from a scenario version
        body, status = sv.spec["scenario"], run.status
    persona = body["agent"]["mailbox"]
    address = next(p["email"] for p in body["personas"] if p["id"] == persona).lower()
    return status, ctl.mailbox_id_for(run_id, persona), address, persona


def _endpoints(request: Request, address: str) -> list[u.InterfaceEndpoint]:
    app = request.app
    base = str(request.base_url).rstrip("/")
    listeners, up = _listeners(app), _running(app) is not None
    out = [u.InterfaceEndpoint(interface="rest", available=True, base_url=f"{base}/v1")]
    for name, path in HTTP_PATHS.items():
        served = _served(app, name)
        out.append(u.InterfaceEndpoint(
            interface=name, available=served, base_url=f"{base}{path}",
            note=None if served else f"{LABELS[name]} is not served by this server"))  # fmt: skip
    out.append(u.InterfaceEndpoint(interface="mcp_stdio", available=True,
                                   command=list(STDIO_COMMAND), note=STDIO_NOTE))  # fmt: skip
    for name in ("imap", "smtp"):
        out.append(u.InterfaceEndpoint(
            interface=name, available=up, username=address,
            host=listeners.host if listeners is not None else None,
            port=_port(listeners, name) if listeners is not None else None,
            tls="starttls" if listeners is not None and listeners.tls else "none",
            note=None if up else _not_running(name)))  # fmt: skip
    return out


@router.get("/runs/{run_id}/connection", response_model=u.RunConnection)
def run_connection(request: Request, run_id: str, principal: Controller) -> u.RunConnection:
    """How an agent connects to this run (endpoints, mailbox, key ids; never key secrets)."""
    status, mailbox_id, address, persona = _agent_mailbox(request, principal, run_id)
    with sessions(request)() as session:
        rows = session.execute(
            select(m.ApiKey)
            .where(m.ApiKey.tenant_id == principal.tenant_id,
                   m.ApiKey.project_id == principal.project_id, m.ApiKey.run_id == run_id,
                   m.ApiKey.role == str(Role.AGENT))
            .order_by(m.ApiKey.created_at, m.ApiKey.id)
        ).scalars()  # fmt: skip
        keys = [u.KeyInfo(key_id=k.id, prefix=k.prefix, created_at=iso(k.created_at),
                          expires_at=iso(k.expires_at) if k.expires_at else None,
                          revoked=k.revoked_at is not None) for k in rows]  # fmt: skip
    return u.RunConnection(run_id=run_id, run_status=status, mailbox_id=mailbox_id,
                           mailbox_address=address, persona_id=persona,
                           interfaces=_endpoints(request, address),
                           agent_keys=keys)  # fmt: skip


class KeyRefused(Exception):
    """The tested key is not a live agent key of the run (reported as ok=false, not an error)."""

    def __init__(self, code: str, reason: str, message: str) -> None:
        super().__init__(message)
        self.code, self.reason, self.message = code, reason, message


def _check_agent_key(request: Request, principal: Principal, run_id: str, key: str) -> Principal:
    """The key's principal when it is a live agent key of `run_id` in the caller's project;
    else KeyRefused (invalid_key: UNAUTHENTICATED; not_agent_key / other_run:
    PERMISSION_DENIED). Never says which run another key belongs to."""
    with sessions(request)() as session:
        repos.get_run(session, scope(principal), run_id)  # 404 outside the caller's project
        try:
            agent = verify_key(session, key.strip())
        except InvalidCredential:
            agent = None
    if agent is None or (agent.tenant_id, agent.project_id) != (
        principal.tenant_id,
        principal.project_id,
    ):
        raise KeyRefused("UNAUTHENTICATED", "invalid_key",
                         "the key is unknown, revoked or expired")  # fmt: skip
    if agent.role != Role.AGENT:
        raise KeyRefused("PERMISSION_DENIED", "not_agent_key",
                         "the key is not an agent key (operator keys cannot connect an agent)")  # fmt: skip
    if agent.run_id != run_id:
        raise KeyRefused("PERMISSION_DENIED", "other_run", "the key belongs to another run")
    return agent


def _error_fields(body: Any) -> tuple[str | None, str | None]:
    """(code, message) of a REST / Gmail / Graph error envelope or a JSON-RPC error."""
    error = body.get("error") if isinstance(body, dict) else None
    if not isinstance(error, dict):
        return None, None
    code = error.get("code")
    if isinstance(code, int) and not isinstance(code, bool):  # Gmail / JSON-RPC numeric codes
        details = error.get("details")
        info = details[0] if isinstance(details, list) and details else None
        code = (
            (info.get("reason") if isinstance(info, dict) else None) or error.get("status") or code
        )
    message = error.get("message")
    return (None if code is None else str(code),
            None if message is None else str(message)[:500])  # fmt: skip


Describe = Callable[[Any], tuple[str, dict[str, Any]]]


def _field(body: Any, name: str) -> Any:
    return body.get(name) if isinstance(body, dict) else None


def _count(body: Any, name: str | None) -> int:
    items = body if name is None else _field(body, name)
    return len(items) if isinstance(items, list) else 0


def _probe(interface: str, mailbox_id: str,
           key: str) -> tuple[str, str, str, dict[str, str], bytes, Describe]:  # fmt: skip
    """(method, path, query, headers, body, describe-success) of one HTTP interface probe."""
    auth = {"authorization": f"Bearer {key}"}
    if interface == "rest":
        return ("GET", f"/v1/mailboxes/{mailbox_id}/folders", "", auth, b"",
                lambda b: (f"{_count(b, None)} folders visible", {"folders": _count(b, None)}))  # fmt: skip
    if interface == "gmail":
        return ("GET", "/gmail/v1/users/me/profile", "", auth, b"",
                lambda b: (f"profile of {_field(b, 'emailAddress')}",
                           {"email_address": _field(b, "emailAddress")}))  # fmt: skip
    if interface == "graph":
        return ("GET", "/graph/v1.0/me/mailFolders", "$top=1", auth, b"",
                lambda b: ("mail folders readable", {"folders_returned": _count(b, "value")}))  # fmt: skip
    rpc = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}).encode()
    headers = {**auth, "content-type": "application/json",
               "accept": "application/json, text/event-stream",
               "mcp-protocol-version": MCP_PROTOCOL_VERSION}  # fmt: skip
    return ("POST", "/mcp", "", headers, rpc,
            lambda b: (f"{_count(_field(b, 'result'), 'tools')} tools listed",
                       {"tools": _count(_field(b, "result"), "tools")}))  # fmt: skip


async def _http_test(request: Request, interface: str, mailbox_id: str,
                     key: str) -> u.ConnectionTestResult:  # fmt: skip
    label = LABELS[interface]
    if not _served(request.app, interface):
        return u.ConnectionTestResult.model_validate(
            {"interface": interface, "ok": False,
             "summary": f"{label} is not served by this server"})  # fmt: skip
    method, path, query, headers, payload, describe = _probe(interface, mailbox_id, key)
    try:
        res = await asgi_request(request.app, method, path, query=query, headers=headers,
                                 body=payload, host=request.headers.get("host", "127.0.0.1"),
                                 scheme=request.url.scheme)  # fmt: skip
    except Exception:  # never leak internals (or the key) to the caller
        log.exception("connection test through %s failed", interface)
        return u.ConnectionTestResult.model_validate(
            {"interface": interface, "ok": False, "summary": f"{label}: internal error",
             "error_code": "INTERNAL"})  # fmt: skip
    try:
        body = json.loads(res.body) if res.body else None
    except ValueError:
        body = None
    code, message = _error_fields(body)
    ok = 200 <= res.status < 300 and code is None and isinstance(body, dict | list)
    details: dict[str, Any] = {}
    if ok:
        text, details = describe(body)
        summary = f"{label}: agent key accepted, {text} (HTTP {res.status})"
    else:
        summary = f"{label}: HTTP {res.status}" + (f" {code}" if code else "")
        summary += f": {message}" if message else ""
    return u.ConnectionTestResult.model_validate(
        {"interface": interface, "ok": ok, "summary": summary, "status_code": res.status,
         "error_code": code, "error_message": message, "details": details})  # fmt: skip


def _protocol_test(request: Request, interface: str, address: str,
                   key: str) -> u.ConnectionTestResult:  # fmt: skip
    listeners = _running(request.app)
    if listeners is None:
        return u.ConnectionTestResult.model_validate(
            {"interface": interface, "ok": False, "summary": _not_running(interface),
             "error_code": LISTENER_DOWN})  # fmt: skip
    ok, code, message = listeners.check_login(address, key, interface=interface)
    if not ok and code is None:  # credentials refused: the protocol's own wording
        code = AUTH_FAILED[interface]
    label = LABELS[interface]
    details = {"host": listeners.host, "port": _port(listeners, interface), "username": address}
    if ok:
        verb = "LOGIN" if interface == "imap" else "AUTH"
        summary = f"{label}: {verb} as {address} accepted"
    else:
        summary = f"{label}: authentication refused: {message}"
    return u.ConnectionTestResult.model_validate(
        {"interface": interface, "ok": ok, "summary": summary, "error_code": code,
         "error_message": None if ok else message, "details": details})  # fmt: skip


@router.post("/runs/{run_id}/connection-tests", response_model=u.ConnectionTestResult)
async def connection_test(
    request: Request, run_id: str, body: u.ConnectionTestRequest, principal: Controller
) -> u.ConnectionTestResult:
    """Make a real call through the chosen interface with the given agent key, server-side.

    Always 200 with `ok` and a summary (400 only for a malformed request, 404 for a run outside
    the caller's project). A key that is not a live agent key of this run is not tried at all:
    ok=false with `details.reason` invalid_key (error_code UNAUTHENTICATED: unknown, revoked,
    expired), not_agent_key or other_run (PERMISSION_DENIED; which run is never said). A probe
    that is made is recorded in the run's activity like any agent call of that interface. The request body
    (and so the key) is never logged, and the key never appears in the result.

    `error_code` when `ok` is false:
    - REST, MCP (HTTP): the /v1 envelope code (UNAUTHENTICATED, RUN_NOT_ACTIVE,
      PERMISSION_DENIED, ...)
    - Gmail: the neutral code in `error.details[0].reason` (UNAUTHENTICATED, RUN_NOT_ACTIVE,
      ...), else `error.status`
    - Graph: Graph's own code (InvalidAuthenticationToken; ErrorPreconditionFailed for a run
      that is not running; ErrorAccessDenied ...)
    - IMAP: AUTHENTICATIONFAILED; SMTP: AUTHENTICATION_FAILED (credentials refused), or the
      engine's code when it refused a valid key (RUN_NOT_ACTIVE, PERMISSION_DENIED)
    - IMAP/SMTP listener not running: LISTENER_DOWN
    - gmail / graph / mcp_http not mounted: null (summary "... is not served by this server")
    - unexpected failure: INTERNAL"""
    result = await _connection_test(request, run_id, body, principal)
    key = body.api_key.strip()
    for name in ("summary", "error_message"):  # defence in depth: never echo the key
        value = getattr(result, name)
        if key and value and key in value:
            setattr(result, name, value.replace(key, "[redacted]"))
    return result


async def _connection_test(request: Request, run_id: str, body: u.ConnectionTestRequest,
                           principal: Principal) -> u.ConnectionTestResult:  # fmt: skip
    try:
        agent = await anyio.to_thread.run_sync(_check_agent_key, request, principal, run_id,
                                               body.api_key)  # fmt: skip
    except KeyRefused as exc:
        return u.ConnectionTestResult(
            interface=body.interface, ok=False, summary=f"{LABELS[body.interface]}: {exc.message}",
            error_code=exc.code, error_message=exc.message,
            details={"reason": exc.reason})  # fmt: skip
    if body.interface == "mcp_stdio":  # what `serve mcp-stdio` verifies at startup
        return u.ConnectionTestResult(
            interface="mcp_stdio", ok=True,
            summary="MCP (stdio): the key is valid; start the stdio server with "
                    "MAILSIM_API_KEY set to it",
            details={"command": list(STDIO_COMMAND)})  # fmt: skip
    if body.interface in ("imap", "smtp"):
        address = (await anyio.to_thread.run_sync(_agent_mailbox, request, principal, run_id))[2]
        return await anyio.to_thread.run_sync(_protocol_test, request, body.interface, address,
                                              body.api_key)  # fmt: skip
    assert agent.mailbox_id is not None  # agent keys are always bound to a mailbox
    return await _http_test(request, body.interface, agent.mailbox_id, body.api_key)


# ---- activity -----------------------------------------------------------------------------
@router.get("/runs/{run_id}/activity", response_model=u.ActivityPage)
def run_activity(
    request: Request, run_id: str, principal: Inspector,
    after_id: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> u.ActivityPage:  # fmt: skip
    """Recorded actions (newest last) with the interface each came through, for polling."""
    rows = inspector(request, principal).actions_page(run_id, after_id=after_id, limit=limit)
    items = [u.ActivityItem(id=r["id"], actor=r["actor"], operation=r["operation"],
                            outcome=r["outcome"], interface=r["interface"],
                            virtual_time=r["virtual_time"], input=r["input"],
                            result=r["result"]) for r in rows]  # fmt: skip
    return u.ActivityPage(items=items, next_after_id=items[-1].id if items else after_id)
