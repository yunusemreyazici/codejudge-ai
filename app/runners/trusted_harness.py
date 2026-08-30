"""Trusted host-side official test plans.

Only invocation operations are sent to the candidate sandbox. Assertions, expected
values, case names, and private evaluator logic remain in this host process.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol


class CandidateTransport(Protocol):
    async def request(self, payload: Mapping[str, object]) -> Mapping[str, object]: ...


class HarnessProtocolError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class HarnessReport:
    passed: int
    failed: int
    total: int
    syntax_error: bool = False
    import_error: bool = False


_MISSING = object()


@dataclass(frozen=True, slots=True)
class ExpectedStep:
    command: Mapping[str, object]
    result: object = _MISSING
    exception: str | None = None
    message: str | None = None
    telemetry: Mapping[str, object] | None = None


@dataclass(frozen=True, slots=True)
class OfficialCase:
    name: str
    steps: tuple[ExpectedStep, ...]


def _expect(
    command: Mapping[str, object],
    result: object = _MISSING,
    *,
    exception: str | None = None,
    message: str | None = None,
    telemetry: Mapping[str, object] | None = None,
) -> ExpectedStep:
    return ExpectedStep(command, result, exception, message, telemetry)


def _construct(symbol: str, object_id: str, *args: object) -> Mapping[str, object]:
    return {"op": "construct", "symbol": symbol, "object_id": object_id, "args": list(args)}


def _function(symbol: str, *args: object, **options: object) -> Mapping[str, object]:
    return {"op": "call_function", "symbol": symbol, "args": list(args), **options}


def _method(object_id: str, method: str, *args: object, **kwargs: object) -> Mapping[str, object]:
    command: dict[str, object] = {
        "op": "call_method",
        "object_id": object_id,
        "method": method,
        "args": list(args),
    }
    if kwargs:
        command["kwargs"] = kwargs
    return command


def _attr(object_id: str, attribute: str) -> Mapping[str, object]:
    return {"op": "get_attr", "object_id": object_id, "attribute": attribute}


def _store(object_id: str, value: object) -> Mapping[str, object]:
    return {"op": "store", "object_id": object_id, "value": value}


def _stored(object_id: str) -> Mapping[str, object]:
    return {"op": "get_object", "object_id": object_id}


def _ref(object_id: str) -> Mapping[str, object]:
    return {"$ref": object_id}


def _callback(
    kind: str = "return",
    *,
    value: object = None,
    exception: str = "RuntimeError",
    message: str = "",
    object_id: str | None = None,
    attribute: str | None = None,
) -> Mapping[str, object]:
    spec: dict[str, object] = {"kind": kind, "return": value}
    if kind == "raise":
        spec.update(exception=exception, message=message)
    if kind == "observe_attribute":
        spec.update(object_id=object_id, attribute=attribute)
    return {"$callable": spec}


def _worker(mode: str = "identity", **options: object) -> Mapping[str, object]:
    return {"$async_worker": {"mode": mode, **options}}


def _case(name: str, *steps: ExpectedStep) -> OfficialCase:
    return OfficialCase(name, steps)


def _lru_cases() -> tuple[OfficialCase, ...]:
    return (
        _case(
            "basic insertion and retrieval",
            _expect(_construct("LRUCache", "c", 2), None),
            _expect(_method("c", "put", 1, 10), None),
            _expect(_method("c", "get", 1), 10),
        ),
        _case(
            "missing key",
            _expect(_construct("LRUCache", "c", 2), None),
            _expect(_method("c", "get", 99), -1),
        ),
        _case(
            "update existing key",
            _expect(_construct("LRUCache", "c", 2), None),
            _expect(_method("c", "put", 1, 10), None),
            _expect(_method("c", "put", 1, 20), None),
            _expect(_method("c", "get", 1), 20),
        ),
        _case(
            "least recently used eviction",
            _expect(_construct("LRUCache", "c", 2), None),
            *(_expect(_method("c", "put", key, key * 10), None) for key in (1, 2, 3)),
            _expect(_method("c", "get", 1), -1),
            _expect(_method("c", "get", 2), 20),
            _expect(_method("c", "get", 3), 30),
        ),
        _case(
            "access changes recency",
            _expect(_construct("LRUCache", "c", 2), None),
            _expect(_method("c", "put", 1, 10), None),
            _expect(_method("c", "put", 2, 20), None),
            _expect(_method("c", "get", 1), 10),
            _expect(_method("c", "put", 3, 30), None),
            _expect(_method("c", "get", 2), -1),
            _expect(_method("c", "get", 1), 10),
        ),
        _case(
            "capacity one",
            _expect(_construct("LRUCache", "c", 1), None),
            _expect(_method("c", "put", 1, 10), None),
            _expect(_method("c", "put", 2, 20), None),
            _expect(_method("c", "get", 1), -1),
            _expect(_method("c", "get", 2), 20),
        ),
        _case(
            "multiple evictions",
            _expect(_construct("LRUCache", "c", 2), None),
            *(_expect(_method("c", "put", key, key * 10), None) for key in (1, 2, 3, 4)),
            *(
                _expect(_method("c", "get", key), value)
                for key, value in ((1, -1), (2, -1), (3, 30), (4, 40))
            ),
        ),
        _case(
            "update refreshes recency",
            _expect(_construct("LRUCache", "c", 2), None),
            _expect(_method("c", "put", 1, 10), None),
            _expect(_method("c", "put", 2, 20), None),
            _expect(_method("c", "put", 1, 11), None),
            _expect(_method("c", "put", 3, 30), None),
            _expect(_method("c", "get", 1), 11),
            _expect(_method("c", "get", 2), -1),
        ),
    )


def _ttl_cases() -> tuple[OfficialCase, ...]:
    invalid = tuple(
        _expect(_construct("TTLCache", f"bad{index}", capacity), exception="ValueError")
        for index, capacity in enumerate((0, -1, True, 1.5))
    )
    invalid_ttl = tuple(
        _expect(_method("valid", "put", "a", 1, ttl=ttl, now=0), exception="ValueError")
        for ttl in (0, -0.1, True)
    )
    return (
        _case(
            "put get overwrite and missing",
            _expect(_construct("TTLCache", "c", 2), None),
            _expect(_method("c", "get", "missing", 0), None),
            _expect(_method("c", "put", "a", {"$opaque": "marker"}, ttl=5, now=0), None),
            _expect(_method("c", "get", "a", 1), {"$opaque": "marker"}),
            _expect(_method("c", "put", "a", "new", ttl=10, now=2), None),
            _expect(_method("c", "get", "a", 11.9), "new"),
            _expect(_method("c", "size", 11.9), 1),
        ),
        _case(
            "inclusive expiration boundary",
            _expect(_construct("TTLCache", "c", 2), None),
            _expect(_method("c", "put", "a", 1, ttl=5, now=10), None),
            _expect(_method("c", "get", "a", 14.999), 1),
            _expect(_method("c", "get", "a", 15), None),
            _expect(_method("c", "size", 15), 0),
            _expect(_method("c", "delete", "a", 15), False),
        ),
        _case(
            "invalid capacity and ttl",
            *invalid,
            _expect(_construct("TTLCache", "valid", 1), None),
            *invalid_ttl,
        ),
        _case(
            "capacity evicts live LRU",
            _expect(_construct("TTLCache", "c", 2), None),
            _expect(_method("c", "put", "a", 1, ttl=100, now=0), None),
            _expect(_method("c", "put", "b", 2, ttl=100, now=1), None),
            _expect(_method("c", "get", "a", 2), 1),
            _expect(_method("c", "put", "c", 3, ttl=100, now=3), None),
            *(
                _expect(_method("c", "get", key, 4), value)
                for key, value in (("b", None), ("a", 1), ("c", 3))
            ),
        ),
        _case(
            "expired entries do not consume capacity",
            _expect(_construct("TTLCache", "c", 2), None),
            _expect(_method("c", "put", "expired", 1, ttl=1, now=0), None),
            _expect(_method("c", "put", "live", 2, ttl=10, now=0), None),
            _expect(_method("c", "put", "new", 3, ttl=10, now=2), None),
            *(
                _expect(_method("c", "get", key, 2), value)
                for key, value in (("expired", None), ("live", 2), ("new", 3))
            ),
            _expect(_method("c", "size", 2), 2),
        ),
        _case(
            "overwrite refreshes expiration and recency",
            _expect(_construct("TTLCache", "c", 2), None),
            _expect(_method("c", "put", "a", 1, ttl=2, now=0), None),
            _expect(_method("c", "put", "b", 2, ttl=20, now=0), None),
            _expect(_method("c", "put", "a", 10, ttl=20, now=1), None),
            _expect(_method("c", "put", "c", 3, ttl=20, now=2), None),
            *(
                _expect(_method("c", "get", key, 3), value)
                for key, value in (("a", 10), ("b", None), ("c", 3))
            ),
        ),
        _case(
            "delete and independent instances",
            _expect(_construct("TTLCache", "first", 1), None),
            _expect(_construct("TTLCache", "second", 1), None),
            _expect(_method("first", "put", "a", 1, ttl=10, now=0), None),
            _expect(_method("second", "get", "a", 0), None),
            _expect(_method("first", "delete", "a", 1), True),
            _expect(_method("first", "delete", "a", 1), False),
            _expect(_method("first", "size", 1), 0),
        ),
    )


def _rate_cases() -> tuple[OfficialCase, ...]:
    return (
        _case(
            "limit and rejected attempts",
            _expect(_construct("SlidingWindowRateLimiter", "r", 2, 10), None),
            *(
                _expect(_method("r", "allow", "user", now), allowed)
                for now, allowed in (
                    (0, True),
                    (1, True),
                    (2, False),
                    (10, True),
                    (10.5, False),
                    (11, True),
                )
            ),
        ),
        _case(
            "exact boundary",
            _expect(_construct("SlidingWindowRateLimiter", "r", 1, 5), None),
            *(
                _expect(_method("r", "allow", "key", now), allowed)
                for now, allowed in ((10, True), (14.999, False), (15, True))
            ),
        ),
        _case(
            "invalid configuration",
            *(
                _expect(
                    _construct("SlidingWindowRateLimiter", f"l{index}", limit, 1),
                    exception="ValueError",
                )
                for index, limit in enumerate((0, -1, True, 1.5))
            ),
            *(
                _expect(
                    _construct("SlidingWindowRateLimiter", f"w{index}", 1, window),
                    exception="ValueError",
                )
                for index, window in enumerate((0, -1, True))
            ),
        ),
        _case(
            "independent keys",
            _expect(_construct("SlidingWindowRateLimiter", "r", 1, 10), None),
            *(
                _expect(_method("r", "allow", key, now), allowed)
                for key, now, allowed in (
                    ("a", 0, True),
                    ("a", 1, False),
                    ("b", 1, True),
                    ("b", 2, False),
                )
            ),
        ),
        _case(
            "prune old but retain live",
            _expect(_construct("SlidingWindowRateLimiter", "r", 3, 10), None),
            *(
                _expect(_method("r", "allow", "key", now), allowed)
                for now, allowed in (
                    (0, True),
                    (1, True),
                    (9, True),
                    (10, True),
                    (10.5, False),
                    (11, True),
                    (19, True),
                )
            ),
        ),
        _case(
            "nonmonotonic per key",
            _expect(_construct("SlidingWindowRateLimiter", "r", 2, 10), None),
            _expect(_method("r", "allow", "a", 5), True),
            _expect(_method("r", "allow", "a", 4.9), exception="ValueError"),
            _expect(_method("r", "allow", "b", -100), True),
            _expect(_method("r", "allow", "a", 5), True),
        ),
        _case(
            "independent instances",
            _expect(_construct("SlidingWindowRateLimiter", "first", 1, 10), None),
            _expect(_construct("SlidingWindowRateLimiter", "second", 1, 10), None),
            _expect(_method("first", "allow", "same", 0), True),
            _expect(_method("first", "allow", "same", 1), False),
            _expect(_method("second", "allow", "same", 1), True),
        ),
    )


def _retry_cases() -> tuple[OfficialCase, ...]:
    cases = [
        _case(
            "growth and default multiplier",
            *(
                _expect(_function("retry_delay", attempt, 0.5, 100), expected)
                for attempt, expected in ((1, 0.5), (2, 1), (3, 2), (6, 16))
            ),
        ),
        _case(
            "inclusive stable cap",
            *(
                _expect(_function("retry_delay", *args), expected)
                for args, expected in (
                    ((3, 2, 8), 8),
                    ((4, 2, 8), 8),
                    ((100, 2, 8), 8),
                    ((10_000, 0.1, 5), 5),
                )
            ),
        ),
        _case(
            "custom multiplier",
            *(
                _expect(_function("retry_delay", *args), expected)
                for args, expected in (
                    ((1, 3, 100, 3), 3),
                    ((2, 3, 100, 3), 9),
                    ((4, 3, 100, 3), 81),
                    ((50, 3, 100, 1), 3),
                )
            ),
        ),
    ]
    cases.extend(
        _case(
            f"invalid attempt {index}",
            _expect(_function("retry_delay", attempt, 1, 10), exception="ValueError"),
        )
        for index, attempt in enumerate((0, -1, True, 1.5, "2"))
    )
    cases.extend(
        _case(
            f"invalid configuration {index}",
            _expect(_function("retry_delay", 1, base, cap, multiplier), exception="ValueError"),
        )
        for index, (base, cap, multiplier) in enumerate(
            ((0, 10, 2), (-1, 10, 2), (2, 1, 2), (1, 10, 0.5), (True, 10, 2))
        )
    )
    cases.append(
        _case(
            "pure repeatable calls",
            _expect(_function("retry_delay", 7, 0.25, 20, 2), 16),
            _expect(_function("retry_delay", 7, 0.25, 20, 2), 16),
            _expect(_function("retry_delay", 8, 0.25, 20, 2), 20),
        )
    )
    return tuple(cases)


def _dependency_cases() -> tuple[OfficialCase, ...]:
    cases = [
        _case(
            "empty single and chain",
            _expect(_function("resolve_dependencies", {}), []),
            _expect(_function("resolve_dependencies", {"app": []}), ["app"]),
            _expect(
                _function(
                    "resolve_dependencies", {"app": ["service"], "service": ["db"], "db": []}
                ),
                ["db", "service", "app"],
            ),
        ),
        _case(
            "unknown leaf dependencies",
            _expect(_function("resolve_dependencies", {"app": ["db"]}), ["db", "app"]),
            _expect(_function("resolve_dependencies", {"z": ["a", "m"]}), ["a", "m", "z"]),
        ),
        _case(
            "global lexical tie breaking",
            _expect(
                _function(
                    "resolve_dependencies",
                    {"web": ["core"], "worker": ["core"], "core": [], "audit": []},
                ),
                ["audit", "core", "web", "worker"],
            ),
            _expect(
                _function(
                    "resolve_dependencies",
                    {"audit": [], "core": [], "worker": ["core"], "web": ["core"]},
                ),
                ["audit", "core", "web", "worker"],
            ),
        ),
        _case(
            "cycles",
            _expect(
                _function("resolve_dependencies", {"a": ["b"], "b": ["a"]}),
                exception="DependencyCycleError",
            ),
            _expect(
                _function("resolve_dependencies", {"self": ["self"]}),
                exception="DependencyCycleError",
            ),
        ),
        _case(
            "diamond graph",
            _expect(
                _function(
                    "resolve_dependencies",
                    {
                        "deploy": ["api", "worker"],
                        "api": ["core"],
                        "worker": ["core"],
                        "core": ["config"],
                    },
                ),
                ["config", "core", "api", "worker", "deploy"],
            ),
        ),
        _case(
            "duplicate edges",
            _expect(
                _function("resolve_dependencies", {"app": ["db", "db"], "db": []}), ["db", "app"]
            ),
        ),
        _case(
            "disconnected components",
            _expect(
                _function("resolve_dependencies", {"z": ["y"], "b": ["a"], "m": []}),
                ["a", "b", "m", "y", "z"],
            ),
        ),
        _case(
            "input not mutated",
            _expect(_store("graph", {"app": ["db", "cache"], "db": []}), None),
            _expect(_function("resolve_dependencies", _ref("graph")), ["cache", "db", "app"]),
            _expect(_stored("graph"), {"app": ["db", "cache"], "db": []}),
        ),
    ]
    invalid_graphs: Sequence[object] = (
        None,
        {"$dict": [[1, []]]},
        {"a": [1]},
        {"a": {"$tuple": ["b"]}},
    )
    cases.extend(
        _case(
            f"invalid graph shape {index}",
            _expect(_function("resolve_dependencies", graph), exception="TypeError"),
        )
        for index, graph in enumerate(invalid_graphs)
    )
    return tuple(cases)


def _async_cases() -> tuple[OfficialCase, ...]:
    return (
        _case(
            "empty and invalid concurrency",
            _expect(_function("process_batch", [], _worker(), 1), [], telemetry={"calls": 0}),
            *(
                _expect(
                    _function("process_batch", [], _worker(), concurrency), exception="ValueError"
                )
                for concurrency in (0, -1, True, 1.5)
            ),
        ),
        _case(
            "once per item and input order",
            _expect(
                _function(
                    "process_batch",
                    ["first", "second"],
                    _worker("ordered", blocked_value="first", transform="upper"),
                    2,
                ),
                ["FIRST", "SECOND"],
                telemetry={"calls": 2, "maximum_active": 2},
            ),
        ),
        _case(
            "bounded concurrency with parallelism",
            _expect(
                _function(
                    "process_batch",
                    [1, 2, 3, 4],
                    _worker("concurrency_probe", target_active=2, transform="double"),
                    2,
                ),
                [2, 4, 6, 8],
                telemetry={"calls": 4, "maximum_active": 2},
            ),
        ),
        _case(
            "failure cancels unfinished tasks",
            _expect(
                _function(
                    "process_batch",
                    [1, 2],
                    _worker("failure_cleanup", failure_value=1, message="boom"),
                    2,
                ),
                exception="RuntimeError",
                message="boom",
                telemetry={"calls": 2, "cancelled": 1},
            ),
        ),
        _case(
            "batch cancellation cleans workers",
            _expect(
                _function(
                    "process_batch", [1, 2, 3], _worker("block"), 2, cancel_after_worker_start=True
                ),
                exception="CancelledError",
                telemetry={"calls": 2, "cancelled": 2},
            ),
        ),
        _case(
            "concurrency larger than batch",
            _expect(
                _function("process_batch", [1, 2], _worker(transform="increment"), 10),
                [2, 3],
                telemetry={"calls": 2},
            ),
        ),
    )


def _circuit_cases() -> tuple[OfficialCase, ...]:
    failure = _callback("raise", exception="RuntimeError", message="operation failed")
    return (
        _case(
            "success resets closed failure count",
            _expect(_construct("CircuitBreaker", "b", 2, 10), None),
            _expect(_method("b", "call", failure, now=0), exception="RuntimeError"),
            _expect(_attr("b", "state"), "closed"),
            _expect(_method("b", "call", _callback(value="ok"), now=1), "ok"),
            _expect(_method("b", "call", failure, now=2), exception="RuntimeError"),
            _expect(_attr("b", "state"), "closed"),
        ),
        _case(
            "threshold opens and blocks",
            _expect(_construct("CircuitBreaker", "b", 2, 10), None),
            _expect(_method("b", "call", failure, now=0), exception="RuntimeError"),
            _expect(_method("b", "call", failure, now=1), exception="RuntimeError"),
            _expect(_attr("b", "state"), "open"),
            _expect(
                _method("b", "call", _callback(), now=10.999),
                exception="CircuitOpenError",
                telemetry={"calls": 0},
            ),
            _expect(_attr("b", "state"), "open"),
        ),
        _case(
            "invalid configuration",
            *(
                _expect(
                    _construct("CircuitBreaker", f"t{index}", threshold, 1), exception="ValueError"
                )
                for index, threshold in enumerate((0, -1, True, 1.5))
            ),
            *(
                _expect(
                    _construct("CircuitBreaker", f"r{index}", 1, timeout), exception="ValueError"
                )
                for index, timeout in enumerate((0, -1, True))
            ),
        ),
        _case(
            "half-open success",
            _expect(_construct("CircuitBreaker", "b", 1, 5), None),
            _expect(_method("b", "call", failure, now=10), exception="RuntimeError"),
            _expect(_method("b", "call", _callback(), now=14.999), exception="CircuitOpenError"),
            _expect(
                _method(
                    "b",
                    "call",
                    _callback("observe_attribute", value=42, object_id="b", attribute="state"),
                    now=15,
                ),
                42,
                telemetry={"calls": 1, "observed": ["half_open"]},
            ),
            _expect(_attr("b", "state"), "closed"),
        ),
        _case(
            "failed half-open probe reopens",
            _expect(_construct("CircuitBreaker", "b", 1, 5), None),
            _expect(_method("b", "call", failure, now=0), exception="RuntimeError"),
            _expect(_method("b", "call", failure, now=5), exception="RuntimeError"),
            _expect(_attr("b", "state"), "open"),
            _expect(
                _method("b", "call", _callback(value="too early"), now=9.999),
                exception="CircuitOpenError",
            ),
            _expect(_method("b", "call", _callback(value="recovered"), now=10), "recovered"),
            _expect(_attr("b", "state"), "closed"),
        ),
        _case(
            "nonmonotonic and independent",
            _expect(_construct("CircuitBreaker", "first", 2, 5), None),
            _expect(_construct("CircuitBreaker", "second", 2, 5), None),
            _expect(_method("first", "call", _callback(value=1), now=10), 1),
            _expect(_method("first", "call", _callback(value=2), now=9), exception="ValueError"),
            _expect(_method("second", "call", _callback(value=3), now=-100), 3),
            _expect(_attr("second", "state"), "closed"),
        ),
        _case(
            "reset clears time history",
            _expect(_construct("CircuitBreaker", "b", 1, 100), None),
            _expect(_method("b", "call", failure, now=50), exception="RuntimeError"),
            _expect(_attr("b", "state"), "open"),
            _expect(_method("b", "reset"), None),
            _expect(_attr("b", "state"), "closed"),
            _expect(_method("b", "call", _callback(value="ok"), now=0), "ok"),
        ),
    )


def _structured_event_cases() -> tuple[OfficialCase, ...]:
    normal = [
        '{"id":"a","timestamp":1,"kind":"created","payload":{"x":2}}',
        "   ",
        '{"id":"b","timestamp":1,"kind":"updated"}',
        '{"id":"c","timestamp":3,"kind":"deleted","payload":{}}',
    ]
    invalid_lines = (
        ("malformed json", "{"),
        ("missing required field", '{"timestamp":1,"kind":"created"}'),
        ("empty id", '{"id":"","timestamp":1,"kind":"created"}'),
        ("boolean timestamp", '{"id":"a","timestamp":true,"kind":"created"}'),
        ("negative timestamp", '{"id":"a","timestamp":-1,"kind":"created"}'),
        ("invalid kind", '{"id":"a","timestamp":1,"kind":"other"}'),
        ("nonstring kind", '{"id":"a","timestamp":1,"kind":[]}'),
        ("nonobject payload", '{"id":"a","timestamp":1,"kind":"created","payload":[]}'),
        ("unknown field", '{"id":"a","timestamp":1,"kind":"created","extra":1}'),
    )
    invalid_cases = tuple(
        _case(
            name,
            _expect(_function("parse_events", [line]), exception="ValueError"),
        )
        for name, line in invalid_lines
    )
    return (
        _case(
            "parse normalize and preserve order",
            _expect(
                _function("parse_events", normal),
                [
                    {"id": "a", "timestamp": 1, "kind": "created", "payload": {"x": 2}},
                    {"id": "b", "timestamp": 1, "kind": "updated", "payload": {}},
                    {"id": "c", "timestamp": 3, "kind": "deleted", "payload": {}},
                ],
            ),
        ),
        _case(
            "empty and blank input",
            _expect(_function("parse_events", []), []),
            _expect(_function("parse_events", ["", "\t", "  "]), []),
        ),
        _case("none outer input", _expect(_function("parse_events", None), exception="TypeError")),
        _case(
            "tuple outer input",
            _expect(_function("parse_events", {"$tuple": []}), exception="TypeError"),
        ),
        _case(
            "string outer input",
            _expect(_function("parse_events", "one line"), exception="TypeError"),
        ),
        _case(
            "item and decoded types",
            _expect(_function("parse_events", [1]), exception="TypeError"),
            _expect(_function("parse_events", ["[]"]), exception="TypeError"),
        ),
        *invalid_cases,
        _case(
            "duplicate ids and decreasing timestamps",
            _expect(
                _function(
                    "parse_events",
                    [
                        '{"id":"a","timestamp":1,"kind":"created"}',
                        '{"id":"a","timestamp":2,"kind":"updated"}',
                    ],
                ),
                exception="ValueError",
                message="duplicate",
            ),
            _expect(
                _function(
                    "parse_events",
                    [
                        '{"id":"a","timestamp":2,"kind":"created"}',
                        '{"id":"b","timestamp":1,"kind":"updated"}',
                    ],
                ),
                exception="ValueError",
                message="nondecreasing",
            ),
        ),
        _case(
            "fresh payload mappings",
            _expect(
                _function(
                    "parse_events",
                    ['{"id":"a","timestamp":0,"kind":"created","payload":{"x":1}}'],
                ),
                [{"id": "a", "timestamp": 0, "kind": "created", "payload": {"x": 1}}],
            ),
        ),
    )


def _interval_reservation_cases() -> tuple[OfficialCase, ...]:
    invalid = (
        ("empty reservation id", "", "room", 0, 1),
        ("empty resource", "id", "", 0, 1),
        ("negative start", "id", "room", -1, 1),
        ("empty interval", "id", "room", 1, 1),
        ("reversed interval", "id", "room", 2, 1),
        ("boolean start", "id", "room", True, 2),
        ("boolean end", "id", "room", 0, False),
    )
    invalid_cases = tuple(
        _case(
            name,
            _expect(_construct("ReservationBook", "book"), None),
            _expect(
                _method("book", "reserve", reservation_id, resource, start, end),
                exception="ValueError",
            ),
        )
        for name, reservation_id, resource, start, end in invalid
    )
    return (
        _case(
            "overlap adjacency and ordering",
            _expect(_construct("ReservationBook", "book"), None),
            _expect(_method("book", "reserve", "late", "room", 10, 20), True),
            _expect(_method("book", "reserve", "early", "room", 0, 10), True),
            _expect(_method("book", "reserve", "overlap", "room", 9, 11), False),
            _expect(
                _method("book", "reservations", "room"),
                [
                    {"id": "early", "start": 0, "end": 10},
                    {"id": "late", "start": 10, "end": 20},
                ],
            ),
        ),
        _case(
            "resources independent and rejected id reusable",
            _expect(_construct("ReservationBook", "book"), None),
            _expect(_method("book", "reserve", "a", "one", 0, 5), True),
            _expect(_method("book", "reserve", "retry", "one", 1, 2), False),
            _expect(_method("book", "reserve", "retry", "two", 1, 2), True),
        ),
        _case(
            "cancel idempotently releases interval",
            _expect(_construct("ReservationBook", "book"), None),
            _expect(_method("book", "reserve", "a", "room", 1, 4), True),
            _expect(_method("book", "cancel", "a"), True),
            _expect(_method("book", "cancel", "a"), False),
            _expect(_method("book", "reserve", "b", "room", 1, 4), True),
        ),
        _case(
            "accepted ids globally unique",
            _expect(_construct("ReservationBook", "book"), None),
            _expect(_method("book", "reserve", "same", "one", 0, 1), True),
            _expect(
                _method("book", "reserve", "same", "two", 10, 11),
                exception="ValueError",
                message="already exists",
            ),
        ),
        *invalid_cases,
        _case(
            "containment and equal ranges overlap",
            _expect(_construct("ReservationBook", "book"), None),
            _expect(_method("book", "reserve", "outer", "room", 5, 20), True),
            _expect(_method("book", "reserve", "inner", "room", 10, 12), False),
            _expect(_method("book", "reserve", "same-range", "room", 5, 20), False),
        ),
        _case(
            "reservation views are stable copies",
            _expect(_construct("ReservationBook", "book"), None),
            _expect(_method("book", "reserve", "a", "room", 0, 1), True),
            _expect(
                _method("book", "reservations", "room"),
                [{"id": "a", "start": 0, "end": 1}],
            ),
            _expect(
                _method("book", "reservations", "room"),
                [{"id": "a", "start": 0, "end": 1}],
            ),
        ),
    )


def _config_layer_cases() -> tuple[OfficialCase, ...]:
    bad_keys = (
        ("top-level nonstring key", [{"$dict": [[1, "bad"]]}]),
        ("nested nonstring key", [{"nested": {"$dict": [[2, "bad"]]}}]),
        ("list-contained nonstring key", [{"items": [{"$dict": [[3, "bad"]]}]}]),
    )
    bad_key_cases = tuple(
        _case(name, _expect(_function("merge_config_layers", value), exception="TypeError"))
        for name, value in bad_keys
    )
    return (
        _case(
            "recursive merge replacement and deletion",
            _expect(
                _function(
                    "merge_config_layers",
                    [
                        {
                            "service": {"host": "a", "port": 80},
                            "debug": False,
                            "obsolete": 1,
                        },
                        {
                            "service": {"port": 443, "tls": True},
                            "debug": True,
                            "obsolete": None,
                        },
                    ],
                ),
                {"service": {"host": "a", "port": 443, "tls": True}, "debug": True},
            ),
        ),
        _case(
            "empty layers and missing deletion",
            _expect(_function("merge_config_layers", []), {}),
            _expect(_function("merge_config_layers", [{"missing": None}]), {}),
        ),
        _case(
            "mappings and scalars replace each other",
            _expect(_function("merge_config_layers", [{"a": 1}, {"a": {"b": 2}}]), {"a": {"b": 2}}),
            _expect(_function("merge_config_layers", [{"a": {"b": 2}}, {"a": [3]}]), {"a": [3]}),
        ),
        _case(
            "input layers are not mutated",
            _expect(_store("layers", [{"a": {"items": [1, {"x": 2}]}}]), None),
            _expect(
                _function("merge_config_layers", _ref("layers")),
                {"a": {"items": [1, {"x": 2}]}},
            ),
            _expect(_stored("layers"), [{"a": {"items": [1, {"x": 2}]}}]),
        ),
        _case(
            "none outer value",
            _expect(_function("merge_config_layers", None), exception="TypeError"),
        ),
        _case(
            "mapping outer value",
            _expect(_function("merge_config_layers", {}), exception="TypeError"),
        ),
        _case(
            "tuple outer value",
            _expect(_function("merge_config_layers", {"$tuple": []}), exception="TypeError"),
        ),
        _case(
            "nonmapping layer",
            _expect(_function("merge_config_layers", [{"valid": 1}, []]), exception="TypeError"),
        ),
        *bad_key_cases,
        _case(
            "nested deletion retains parent",
            _expect(
                _function(
                    "merge_config_layers",
                    [{"a": {"x": 1, "y": 2}}, {"a": {"x": None}}],
                ),
                {"a": {"y": 2}},
            ),
        ),
    )


def _logical_path_cases() -> tuple[OfficialCase, ...]:
    return (
        _case(
            "absolute normalization",
            _expect(_function("normalize_path", "//api/./v1/../v2//"), "/api/v2"),
            _expect(_function("normalize_path", "/"), "/"),
        ),
        _case(
            "relative path uses normalized cwd",
            _expect(_function("normalize_path", "../logs/./today", "/srv/app/"), "/srv/logs/today"),
            _expect(_function("normalize_path", "child", "/a//b/../c"), "/a/c/child"),
        ),
        _case(
            "absolute path ignores cwd components",
            _expect(_function("normalize_path", "/safe", "/ignored/../cwd"), "/safe"),
        ),
        _case("empty path", _expect(_function("normalize_path", "", "/"), exception="ValueError")),
        _case("empty cwd", _expect(_function("normalize_path", "a", ""), exception="ValueError")),
        _case(
            "relative cwd",
            _expect(_function("normalize_path", "a", "relative"), exception="ValueError"),
        ),
        _case(
            "exact root boundary",
            _expect(_function("normalize_path", "..", "/a"), "/"),
            _expect(_function("normalize_path", "a/..", "/"), "/"),
        ),
        _case(
            "above-root traversal",
            _expect(
                _function("normalize_path", "../x", "/"),
                exception="ValueError",
                message="above root",
            ),
            _expect(
                _function("normalize_path", "x", "/../bad"),
                exception="ValueError",
                message="above root",
            ),
        ),
        _case(
            "backslash is ordinary",
            _expect(_function("normalize_path", r"a\b/../c", "/root"), "/root/c"),
            _expect(_function("normalize_path", r"a\b", "/root"), r"/root/a\b"),
        ),
        _case(
            "absolute path replaces cwd",
            _expect(_function("normalize_path", "/x/../y", "/base"), "/y"),
        ),
    )


def _frame_decoder_cases() -> tuple[OfficialCase, ...]:
    invalid_maximums = (0, -1, True, 1.5, 1_000_001)
    maximum_cases = tuple(
        _case(
            f"invalid maximum {index}",
            _expect(
                _construct("LengthPrefixedDecoder", "decoder", maximum),
                exception="ValueError",
            ),
        )
        for index, maximum in enumerate(invalid_maximums)
    )
    malformed = (":", "01:a", "x:a", "\uff11\uff12:a")
    malformed_cases = tuple(
        _case(
            f"malformed prefix {index}",
            _expect(_construct("LengthPrefixedDecoder", "decoder", 10), None),
            _expect(_method("decoder", "feed", encoded), exception="ValueError"),
            _expect(_method("decoder", "feed", "2:ok"), ["ok"]),
        )
        for index, encoded in enumerate(malformed)
    )
    return (
        _case(
            "multiple and empty frames",
            _expect(_construct("LengthPrefixedDecoder", "decoder", 20), None),
            _expect(_method("decoder", "feed", "3:abc0:5:hello"), ["abc", "", "hello"]),
            _expect(_method("decoder", "finish"), None),
        ),
        _case(
            "prefix and payload cross chunks",
            _expect(_construct("LengthPrefixedDecoder", "decoder", 20), None),
            _expect(_method("decoder", "feed", "1"), []),
            _expect(_method("decoder", "feed", "1:hello"), []),
            _expect(_method("decoder", "feed", " world"), ["hello world"]),
        ),
        _case(
            "complete frames before incomplete tail",
            _expect(_construct("LengthPrefixedDecoder", "decoder", 10), None),
            _expect(_method("decoder", "feed", "1:a3:x"), ["a"]),
            _expect(_method("decoder", "feed", "yz"), ["xyz"]),
        ),
        _case(
            "empty feed",
            _expect(_construct("LengthPrefixedDecoder", "decoder", 4), None),
            _expect(_method("decoder", "feed", ""), []),
            _expect(_method("decoder", "feed", "2:ok"), ["ok"]),
        ),
        *maximum_cases,
        *malformed_cases,
        _case(
            "oversized partial prefix resets",
            _expect(_construct("LengthPrefixedDecoder", "decoder", 3), None),
            _expect(
                _method("decoder", "feed", "4"),
                exception="ValueError",
                message="maximum",
            ),
            _expect(_method("decoder", "feed", "3:yes"), ["yes"]),
        ),
        _case(
            "nonstring chunk preserves buffer",
            _expect(_construct("LengthPrefixedDecoder", "decoder", 10), None),
            _expect(_method("decoder", "feed", "3:a"), []),
            _expect(_method("decoder", "feed", 1), exception="TypeError"),
            _expect(_method("decoder", "feed", "bc"), ["abc"]),
        ),
        _case(
            "finish rejects truncation and resets",
            _expect(_construct("LengthPrefixedDecoder", "decoder", 10), None),
            _expect(_method("decoder", "feed", "3:ab"), []),
            _expect(
                _method("decoder", "finish"),
                exception="ValueError",
                message="truncated",
            ),
            _expect(_method("decoder", "feed", "1:x"), ["x"]),
        ),
        _case(
            "payload contains prefix characters and unicode",
            _expect(_construct("LengthPrefixedDecoder", "decoder", 10), None),
            _expect(_method("decoder", "feed", "4:a:🙂b"), ["a:🙂b"]),
        ),
    )


OFFICIAL_CASES: Mapping[str, tuple[OfficialCase, ...]] = {
    "lru-cache": _lru_cases(),
    "ttl-cache": _ttl_cases(),
    "rate-limiter": _rate_cases(),
    "retry-backoff": _retry_cases(),
    "dependency-resolver": _dependency_cases(),
    "async-batch-processor": _async_cases(),
    "circuit-breaker": _circuit_cases(),
    "structured-event-parser": _structured_event_cases(),
    "interval-reservation": _interval_reservation_cases(),
    "config-layer-merge": _config_layer_cases(),
    "logical-path": _logical_path_cases(),
    "frame-decoder": _frame_decoder_cases(),
}

OFFICIAL_TEST_CASE_COUNTS: Mapping[str, int] = {
    task_id: len(cases) for task_id, cases in OFFICIAL_CASES.items()
}


class TrustedOfficialHarness:
    async def evaluate(self, task_id: str, transport: CandidateTransport) -> HarnessReport:
        try:
            cases = OFFICIAL_CASES[task_id]
        except KeyError as error:
            raise HarnessProtocolError(
                f"No trusted official harness for task: {task_id}"
            ) from error

        passed = 0
        for case in cases:
            payload = {"op": "run_case", "steps": [dict(step.command) for step in case.steps]}
            response = await transport.request(payload)
            candidate = response.get("candidate")
            if not response.get("ok") or not isinstance(candidate, Mapping):
                raise HarnessProtocolError("Candidate supervisor returned an invalid response")
            startup = candidate.get("startup_error")
            if isinstance(startup, Mapping):
                return HarnessReport(
                    passed=0,
                    failed=0,
                    total=0,
                    syntax_error=startup.get("syntax_error") is True,
                    import_error=startup.get("import_error") is True,
                )
            outcomes = candidate.get("outcomes")
            if not isinstance(outcomes, list) or len(outcomes) != len(case.steps):
                raise HarnessProtocolError("Candidate worker returned invalid step outcomes")
            if all(
                _matches(step, outcome) for step, outcome in zip(case.steps, outcomes, strict=True)
            ):
                passed += 1
        return HarnessReport(passed=passed, failed=len(cases) - passed, total=len(cases))


def _matches(expected: ExpectedStep, raw: object) -> bool:
    if not isinstance(raw, Mapping):
        return False
    if expected.exception is not None:
        exception = raw.get("exception")
        if raw.get("ok") is not False or not isinstance(exception, Mapping):
            return False
        if exception.get("type") != expected.exception:
            return False
        if expected.message is not None and expected.message not in str(
            exception.get("message", "")
        ):
            return False
    else:
        if raw.get("ok") is not True:
            return False
        if expected.result is not _MISSING and raw.get("result") != expected.result:
            return False
    if expected.telemetry is None:
        return True
    telemetry = raw.get("telemetry")
    if not isinstance(telemetry, list) or not telemetry or not isinstance(telemetry[0], Mapping):
        return False
    return all(telemetry[0].get(key) == value for key, value in expected.telemetry.items())


assert OFFICIAL_TEST_CASE_COUNTS == {
    "lru-cache": 8,
    "ttl-cache": 7,
    "rate-limiter": 7,
    "retry-backoff": 14,
    "dependency-resolver": 12,
    "async-batch-processor": 6,
    "circuit-breaker": 7,
    "structured-event-parser": 17,
    "interval-reservation": 13,
    "config-layer-merge": 12,
    "logical-path": 10,
    "frame-decoder": 17,
}
