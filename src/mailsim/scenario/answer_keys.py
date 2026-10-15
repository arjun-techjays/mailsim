"""Answer-key storage: separately versioned, immutable, evaluator-only.

An answer key belongs to a scenario (by key) and declares which scenario versions it is
compatible with. New key versions can be published at any time and applied to past runs;
the scenario the agent saw never changes.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from mailsim.domain.errors import Conflict, InvalidRequest, NotFound
from mailsim.domain.ids import new_control_id
from mailsim.scenario.answer_key import (
    AnswerAssertion,
    AnswerKeyBody,
    AnswerKeyDoc,
    answer_key_hash,
    find_leaks,
    secret_strings,
)
from mailsim.scenario.loader import parse_source
from mailsim.scenario.schema import ScenarioDoc
from mailsim.scenario.validator import agent_visible_texts
from mailsim.store import models as m
from mailsim.store.blob import BlobStore
from mailsim.store.repos import Scope


def load_answer_key(path: Path) -> AnswerKeyDoc:
    path = Path(path)
    if not path.is_file():
        raise InvalidRequest(f"answer key file not found: {path}")
    data = parse_source(path.read_bytes(), is_json=path.suffix.lower() == ".json")
    try:
        return AnswerKeyDoc.model_validate(data)
    except ValueError as exc:
        raise InvalidRequest("answer key validation failed", details={"errors": str(exc)}) from exc


def _scenario(session: Session, scope: Scope, key: str, *, lock: bool = False) -> m.Scenario:
    stmt = select(m.Scenario).where(
        m.Scenario.tenant_id == scope.tenant_id,
        m.Scenario.project_id == scope.project_id,
        m.Scenario.key == key,
    )
    row = session.execute(stmt.with_for_update() if lock else stmt).scalar_one_or_none()
    if row is None:
        raise NotFound(f"scenario {key} not found")
    return row


def _store(session: Session, scenario: m.Scenario, doc: AnswerKeyDoc, source: str) -> m.AnswerKey:
    body = doc.answer_key
    digest = answer_key_hash(doc)
    existing = session.execute(
        select(m.AnswerKey).where(
            m.AnswerKey.scenario_id == scenario.id, m.AnswerKey.version == body.version
        )
    ).scalar_one_or_none()
    if existing is not None:
        if existing.spec_hash == digest:
            return existing
        raise Conflict(
            f"answer key {body.scenario} v{body.version} already exists with different content",
            details={"version": body.version},
        )
    latest = session.scalar(
        select(func.max(m.AnswerKey.version)).where(m.AnswerKey.scenario_id == scenario.id)
    )
    if latest is not None and body.version < latest:
        raise Conflict(f"answer key version {body.version} is older than latest {latest}")
    row = m.AnswerKey(
        id=new_control_id("akv"),
        scenario_id=scenario.id,
        version=body.version,
        spec=doc.model_dump(mode="json", by_alias=True),
        spec_hash=digest,
        scenario_versions=body.scenario_versions,
        source=source,
    )
    session.add(row)
    session.flush()
    return row


def _check_leaks(
    session: Session, scenario: m.Scenario, body: AnswerKeyBody, blobs: BlobStore
) -> None:
    secrets = secret_strings(body)
    if not secrets:
        return
    stmt = select(m.ScenarioVersion).where(m.ScenarioVersion.scenario_id == scenario.id)
    if body.scenario_versions is not None:
        stmt = stmt.where(m.ScenarioVersion.version.in_(body.scenario_versions))
    for sv in session.execute(stmt).scalars():
        scen = ScenarioDoc.model_validate(sv.spec).scenario
        raws = [blobs.get(ref) for ref in sv.fixtures.values() if blobs.exists(ref)]
        leaks = find_leaks(secrets, agent_visible_texts(scen, raws))
        if leaks:
            raise InvalidRequest(
                "answer-key text appears in agent-visible scenario content",
                details={"scenario_version": sv.version, "leaks": [x[:60] for x in leaks]},
            )


def create_answer_key_version(
    session: Session, scope: Scope, doc: AnswerKeyDoc, blobs: BlobStore
) -> m.AnswerKey:
    body = doc.answer_key
    scenario = _scenario(session, scope, body.scenario, lock=True)
    if body.scenario_versions:
        known = set(
            session.scalars(
                select(m.ScenarioVersion.version).where(
                    m.ScenarioVersion.scenario_id == scenario.id
                )
            )
        )
        missing = sorted(set(body.scenario_versions) - known)
        if missing:
            raise InvalidRequest(f"unknown scenario versions {missing}")
    _check_leaks(session, scenario, body, blobs)
    return _store(session, scenario, doc, "file")


def sync_inline_answer_key(
    session: Session, scenario: m.Scenario, scenario_version: int, inline: list[AnswerAssertion]
) -> m.AnswerKey:
    """Turn inline scenario assertions into an answer-key version (auto-numbered)."""
    for row in session.execute(
        select(m.AnswerKey)
        .where(m.AnswerKey.scenario_id == scenario.id, m.AnswerKey.source == "inline")
        .order_by(m.AnswerKey.version.desc())
    ).scalars():
        doc = AnswerKeyDoc.model_validate(row.spec)
        if doc.answer_key.scenario_versions == [scenario_version]:
            if doc.answer_key.assertions == inline:
                return row
            break
    latest = session.scalar(
        select(func.max(m.AnswerKey.version)).where(m.AnswerKey.scenario_id == scenario.id)
    )
    doc = AnswerKeyDoc(
        answer_key_version="1.0",
        answer_key=AnswerKeyBody(
            scenario=scenario.key,
            version=(latest or 0) + 1,
            scenario_versions=[scenario_version],
            assertions=inline,
        ),
    )
    return _store(session, scenario, doc, "inline")


def list_answer_keys(session: Session, scope: Scope, scenario_key: str) -> list[m.AnswerKey]:
    scenario = _scenario(session, scope, scenario_key)
    return list(
        session.execute(
            select(m.AnswerKey)
            .where(m.AnswerKey.scenario_id == scenario.id)
            .order_by(m.AnswerKey.version)
        ).scalars()
    )


def answer_key_for(
    session: Session, scope: Scope, scenario_key: str, scenario_version: int, version: int | None
) -> m.AnswerKey:
    """The requested key version, or the latest key compatible with `scenario_version`."""
    candidates = [
        k
        for k in list_answer_keys(session, scope, scenario_key)
        if k.scenario_versions is None or scenario_version in k.scenario_versions
    ]
    if version is not None:
        candidates = [k for k in candidates if k.version == version]
    if not candidates:
        raise NotFound(
            f"no answer key for {scenario_key} v{scenario_version}"
            + (f" (version {version})" if version is not None else "")
        )
    return candidates[-1]
