"""Immutable scenario versions (spec 4.1): editing creates a new version, never mutates."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from mailsim.domain.errors import Conflict, NotFound
from mailsim.domain.ids import new_control_id
from mailsim.scenario.loader import LoadedScenario, fixture_manifest
from mailsim.store import models as m
from mailsim.store.blob import BlobStore
from mailsim.store.repos import Scope, get_project


def create_scenario_version(
    session: Session, scope: Scope, loaded: LoadedScenario, blobs: BlobStore
) -> m.ScenarioVersion:
    """Store a validated scenario as a new immutable version.

    - Same key + same version + identical content: idempotent, returns the existing row.
    - Same key + same version + different content: CONFLICT (versions are immutable).
    - Version lower than the latest: CONFLICT (versions only move forward).
    """
    get_project(session, scope)
    scenario = session.execute(
        select(m.Scenario)
        .where(m.Scenario.project_id == scope.project_id, m.Scenario.key == loaded.key)
        .with_for_update()
    ).scalar_one_or_none()
    if scenario is None:
        scenario = m.Scenario(
            id=new_control_id("scn"),
            tenant_id=scope.tenant_id,
            project_id=scope.project_id,
            key=loaded.key,
        )
        session.add(scenario)
        session.flush()

    existing = session.execute(
        select(m.ScenarioVersion).where(
            m.ScenarioVersion.scenario_id == scenario.id,
            m.ScenarioVersion.version == loaded.version,
        )
    ).scalar_one_or_none()
    if existing is not None:
        if existing.spec_hash == loaded.spec_hash:
            return existing
        raise Conflict(
            f"scenario {loaded.key} version {loaded.version} already exists with different "
            "content; bump `version` to publish a change",
            details={"key": loaded.key, "version": loaded.version},
        )
    latest = session.scalar(
        select(func.max(m.ScenarioVersion.version)).where(
            m.ScenarioVersion.scenario_id == scenario.id
        )
    )
    if latest is not None and loaded.version < latest:
        raise Conflict(
            f"scenario {loaded.key} version {loaded.version} is older than latest {latest}",
            details={"latest": latest},
        )

    manifest = fixture_manifest(loaded.fixtures)
    for content in loaded.fixtures.values():
        blobs.put(content)  # fixtures are snapshotted so runs never depend on disk state
    row = m.ScenarioVersion(
        id=new_control_id("scv"),
        scenario_id=scenario.id,
        version=loaded.version,
        spec=loaded.spec,
        spec_hash=loaded.spec_hash,
        fixtures=manifest,
        seed=loaded.doc.scenario.environment.seed,
        source_blob=blobs.put(loaded.source),
    )
    session.add(row)
    session.flush()
    return row


def get_scenario_version(session: Session, scope: Scope, version_id: str) -> m.ScenarioVersion:
    row = session.execute(
        select(m.ScenarioVersion)
        .join(m.Scenario, m.Scenario.id == m.ScenarioVersion.scenario_id)
        .where(
            m.ScenarioVersion.id == version_id,
            m.Scenario.tenant_id == scope.tenant_id,
            m.Scenario.project_id == scope.project_id,
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFound(f"scenario version {version_id} not found")
    return row


def latest_version(session: Session, scope: Scope, key: str) -> m.ScenarioVersion:
    row = session.execute(
        select(m.ScenarioVersion)
        .join(m.Scenario, m.Scenario.id == m.ScenarioVersion.scenario_id)
        .where(
            m.Scenario.tenant_id == scope.tenant_id,
            m.Scenario.project_id == scope.project_id,
            m.Scenario.key == key,
        )
        .order_by(m.ScenarioVersion.version.desc())
        .limit(1)
    ).scalar_one_or_none()
    if row is None:
        raise NotFound(f"scenario {key} not found")
    return row
