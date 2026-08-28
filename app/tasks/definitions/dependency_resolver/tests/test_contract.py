import pytest
from solution import DependencyCycleError, resolve_dependencies


def test_empty_single_and_simple_chain() -> None:
    assert resolve_dependencies({}) == []
    assert resolve_dependencies({"app": []}) == ["app"]
    assert resolve_dependencies({"app": ["service"], "service": ["db"], "db": []}) == [
        "db",
        "service",
        "app",
    ]


def test_unknown_dependencies_are_included_as_leaf_nodes() -> None:
    assert resolve_dependencies({"app": ["db"]}) == ["db", "app"]
    assert resolve_dependencies({"z": ["a", "m"]}) == ["a", "m", "z"]


def test_lexical_tie_breaking_is_global_and_deterministic() -> None:
    graph = {"web": ["core"], "worker": ["core"], "core": [], "audit": []}
    assert resolve_dependencies(graph) == ["audit", "core", "web", "worker"]
    assert resolve_dependencies(dict(reversed(list(graph.items())))) == [
        "audit",
        "core",
        "web",
        "worker",
    ]


def test_cycle_and_self_cycle_raise_documented_exception() -> None:
    with pytest.raises(DependencyCycleError):
        resolve_dependencies({"a": ["b"], "b": ["a"]})
    with pytest.raises(DependencyCycleError):
        resolve_dependencies({"self": ["self"]})
