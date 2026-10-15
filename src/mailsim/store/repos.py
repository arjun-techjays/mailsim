"""Scope-aware repositories.

Every method takes an explicit `Scope` (tenant + project); a resource outside that scope
is reported as NOT_FOUND so existence is not leaked across tenants (spec section 10).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from mailsim.domain.errors import InvalidRequest, NotFound
from mailsim.domain.ids import new_control_id
from mailsim.store.models import Project, Run, Tenant


@dataclass(frozen=True)
class Scope:
    tenant_id: str
    project_id: str


def create_tenant(session: Session, name: str) -> Tenant:
    if not name.strip():
        raise InvalidRequest("tenant name must not be empty")
    tenant = Tenant(id=new_control_id("ten"), name=name)
    session.add(tenant)
    session.flush()
    return tenant


def create_project(session: Session, tenant_id: str, name: str) -> Project:
    if not name.strip():
        raise InvalidRequest("project name must not be empty")
    if session.get(Tenant, tenant_id) is None:
        raise NotFound(f"tenant {tenant_id} not found")
    project = Project(id=new_control_id("prj"), tenant_id=tenant_id, name=name)
    session.add(project)
    session.flush()
    return project


def get_project(session: Session, scope: Scope) -> Project:
    project = session.get(Project, scope.project_id)
    if project is None or project.tenant_id != scope.tenant_id:
        raise NotFound(f"project {scope.project_id} not found")
    return project


def get_run(session: Session, scope: Scope, run_id: str, *, lock: bool = False) -> Run:
    stmt = select(Run).where(
        Run.id == run_id, Run.tenant_id == scope.tenant_id, Run.project_id == scope.project_id
    )
    if lock:
        stmt = stmt.with_for_update()
    run = session.execute(stmt).scalar_one_or_none()
    if run is None:
        raise NotFound(f"run {run_id} not found")
    return run


def list_runs(session: Session, scope: Scope) -> list[Run]:
    stmt = (
        select(Run)
        .where(Run.tenant_id == scope.tenant_id, Run.project_id == scope.project_id)
        .order_by(Run.created_at, Run.id)
    )
    return list(session.execute(stmt).scalars())


def as_dict(obj: Any, columns: list[str]) -> dict[str, Any]:
    return {c: getattr(obj, c) for c in columns}
