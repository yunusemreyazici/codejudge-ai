import copy

import pytest
from solution import resolve_dependencies


def test_diamond_graph_orders_each_dependency_before_dependent() -> None:
    graph = {
        "deploy": ["api", "worker"],
        "api": ["core"],
        "worker": ["core"],
        "core": ["config"],
    }
    result = resolve_dependencies(graph)
    assert result == ["config", "core", "api", "worker", "deploy"]
    positions = {node: index for index, node in enumerate(result)}
    assert positions["config"] < positions["core"] < positions["api"]
    assert positions["core"] < positions["worker"] < positions["deploy"]


def test_duplicate_edges_have_no_effect() -> None:
    assert resolve_dependencies({"app": ["db", "db"], "db": []}) == ["db", "app"]


def test_disconnected_components_and_transitive_unknown_nodes() -> None:
    graph = {"z": ["y"], "b": ["a"], "m": []}
    assert resolve_dependencies(graph) == ["a", "b", "m", "y", "z"]


def test_input_is_not_mutated() -> None:
    graph = {"app": ["db", "cache"], "db": []}
    original = copy.deepcopy(graph)
    resolve_dependencies(graph)
    assert graph == original


@pytest.mark.parametrize(
    "graph",
    [None, {1: []}, {"a": [1]}, {"a": ("b",)}],
)
def test_invalid_graph_shapes_are_rejected(graph: object) -> None:
    with pytest.raises(TypeError):
        resolve_dependencies(graph)
