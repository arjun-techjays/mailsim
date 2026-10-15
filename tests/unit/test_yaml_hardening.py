import pytest

from mailsim.domain.errors import InvalidRequest
from mailsim.scenario import loader
from mailsim.scenario.loader import parse_source

BOMB = b"""a: &a ["x","x","x","x","x","x","x","x","x"]
b: &b [*a,*a,*a,*a,*a,*a,*a,*a,*a]
c: &c [*b,*b,*b,*b,*b,*b,*b,*b,*b]
"""


def test_aliases_rejected():
    with pytest.raises(InvalidRequest, match="not valid YAML"):
        parse_source(BOMB)


def test_alias_without_anchor_rejected():
    with pytest.raises(InvalidRequest, match="aliases"):
        parse_source(b"a: *x\n")


def test_anchor_alone_rejected():
    with pytest.raises(InvalidRequest, match="anchors"):
        parse_source(b"a: &x 1\n")


def test_node_cap(monkeypatch):
    monkeypatch.setattr(loader._NoAliasSafeLoader, "max_nodes", 5)
    with pytest.raises(InvalidRequest, match="too many nodes"):
        parse_source(b"[1, 2, 3, 4, 5, 6, 7]")


def test_plain_yaml_still_loads_and_tags_are_refused():
    assert parse_source(b"a: [1, 2]\n") == {"a": [1, 2]}
    with pytest.raises(InvalidRequest):
        parse_source(b"!!python/object/apply:os.system ['echo x']")
