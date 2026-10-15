import copy

import pytest
import yaml

from mailsim.domain.errors import Conflict, NotFound
from mailsim.scenario.loader import load_scenario
from mailsim.scenario.service import create_scenario_version, get_scenario_version, latest_version
from mailsim.store import repos
from tests.scenarios import EXAMPLE, write_variant

pytestmark = pytest.mark.db


def test_versions_are_immutable_idempotent_and_forward_only(env, tmp_path):
    loaded = load_scenario(EXAMPLE)
    with env.session_factory.begin() as s:
        v1 = create_scenario_version(s, env.scope, loaded, env.blobs)
        again = create_scenario_version(s, env.scope, loaded, env.blobs)
        assert again.id == v1.id
        assert v1.fixtures and all(env.blobs.exists(h) for h in v1.fixtures.values())
        # inline assertions are split into a hidden answer key; the raw source is not kept
        assert v1.source_blob is None
        assert v1.spec["scenario"]["evaluation"]["assertions"] == []

    changed = write_variant(tmp_path, lambda s: s.update(title="Edited"))
    with env.session_factory.begin() as s, pytest.raises(Conflict, match="different content"):
        create_scenario_version(s, env.scope, load_scenario(changed), env.blobs)

    bumped = write_variant(tmp_path, lambda s: s.update(title="Edited", version=3), "v3.yaml")
    with env.session_factory.begin() as s:
        v3 = create_scenario_version(s, env.scope, load_scenario(bumped), env.blobs)
        assert v3.version == 3 and v3.id != v1.id
    older = write_variant(tmp_path, lambda s: s.update(title="Old", version=2), "v2.yaml")
    with env.session_factory.begin() as s, pytest.raises(Conflict, match="older than latest"):
        create_scenario_version(s, env.scope, load_scenario(older), env.blobs)

    with env.session_factory() as s:
        assert latest_version(s, env.scope, "finance.invoice-bank-change").version == 3
        assert (
            get_scenario_version(s, env.scope, v1.id)
            .spec["scenario"]["title"]
            .startswith("Invoice")
        )


def test_scenario_versions_are_scoped(env, session_factory):
    loaded = load_scenario(EXAMPLE)
    with session_factory.begin() as s:
        v = create_scenario_version(s, env.scope, loaded, env.blobs)
        t2 = repos.create_tenant(s, "other")
        p2 = repos.create_project(s, t2.id, "other")
    other = repos.Scope(t2.id, p2.id)
    with session_factory() as s:
        with pytest.raises(NotFound):
            get_scenario_version(s, other, v.id)
        with pytest.raises(NotFound):
            latest_version(s, other, "finance.invoice-bank-change")
        with pytest.raises(NotFound):
            latest_version(s, env.scope, "nope")
    with session_factory.begin() as s, pytest.raises(NotFound):
        create_scenario_version(s, repos.Scope("ten_x", "prj_x"), loaded, env.blobs)
    _ = (copy, yaml)
