"""Trusted dependency-resolution oracle for generated-test validation."""

import heapq


class DependencyCycleError(ValueError):
    pass


def resolve_dependencies(graph: dict[str, list[str]]) -> list[str]:
    if not isinstance(graph, dict):
        raise TypeError("graph must be a dictionary")
    dependencies: dict[str, set[str]] = {}
    all_nodes: set[str] = set()
    for node, raw_dependencies in graph.items():
        if not isinstance(node, str):
            raise TypeError("node names must be strings")
        if not isinstance(raw_dependencies, list):
            raise TypeError("dependencies must be lists")
        unique: set[str] = set()
        for dependency in raw_dependencies:
            if not isinstance(dependency, str):
                raise TypeError("node names must be strings")
            unique.add(dependency)
        dependencies[node] = unique
        all_nodes.add(node)
        all_nodes.update(unique)

    dependents: dict[str, set[str]] = {node: set() for node in all_nodes}
    indegree = {node: 0 for node in all_nodes}
    for node, required in dependencies.items():
        indegree[node] = len(required)
        for dependency in required:
            dependents[dependency].add(node)

    available = [node for node, degree in indegree.items() if degree == 0]
    heapq.heapify(available)
    resolved: list[str] = []
    while available:
        node = heapq.heappop(available)
        resolved.append(node)
        for dependent in sorted(dependents[node]):
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                heapq.heappush(available, dependent)
    if len(resolved) != len(all_nodes):
        raise DependencyCycleError("dependency graph contains a cycle")
    return resolved
