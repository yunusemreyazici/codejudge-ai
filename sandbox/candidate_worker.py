"""Generic one-case candidate invocation worker.

This file contains no task tests, reference code, expected values, or evaluator paths.
"""

from __future__ import annotations

import asyncio
import builtins
import importlib.util
import inspect
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

_MAX_STEPS = 128
_SOLUTION_PATH = Path("/workspace/solution.py")
_PROTOCOL_STDOUT: object


def _send(payload: object) -> None:
    _PROTOCOL_STDOUT.write(json.dumps(payload, separators=(",", ":")) + "\n")  # type: ignore[attr-defined]
    _PROTOCOL_STDOUT.flush()  # type: ignore[attr-defined]


@dataclass
class Telemetry:
    calls: int = 0
    active: int = 0
    maximum_active: int = 0
    cancelled: int = 0
    observed: list[object] = field(default_factory=list)


class InvocationContext:
    def __init__(self) -> None:
        self.objects: dict[str, object] = {}
        self.opaque: dict[str, object] = {}

    def decode(self, value: object, telemetry: list[Telemetry]) -> object:
        if isinstance(value, list):
            return [self.decode(item, telemetry) for item in value]
        if not isinstance(value, dict):
            return value
        if set(value) == {"$ref"} and isinstance(value["$ref"], str):
            return self.objects[value["$ref"]]
        if set(value) == {"$opaque"} and isinstance(value["$opaque"], str):
            return self.opaque.setdefault(value["$opaque"], object())
        if set(value) == {"$tuple"} and isinstance(value["$tuple"], list):
            return tuple(self.decode(item, telemetry) for item in value["$tuple"])
        if set(value) == {"$dict"} and isinstance(value["$dict"], list):
            return {
                self.decode(pair[0], telemetry): self.decode(pair[1], telemetry)
                for pair in value["$dict"]
                if isinstance(pair, list) and len(pair) == 2
            }
        if set(value) == {"$callable"} and isinstance(value["$callable"], dict):
            tracker = Telemetry()
            telemetry.append(tracker)
            return self._callable(value["$callable"], tracker)
        if set(value) == {"$async_worker"} and isinstance(value["$async_worker"], dict):
            tracker = Telemetry()
            telemetry.append(tracker)
            return self._async_worker(value["$async_worker"], tracker)
        return {key: self.decode(item, telemetry) for key, item in value.items()}

    def encode(self, value: object) -> object:
        for token, item in self.opaque.items():
            if value is item:
                return {"$opaque": token}
        if value is None or isinstance(value, (bool, int, float, str)):
            return value
        if isinstance(value, list):
            return [self.encode(item) for item in value]
        if isinstance(value, tuple):
            return {"$tuple": [self.encode(item) for item in value]}
        if isinstance(value, dict):
            if all(isinstance(key, str) for key in value):
                return {str(key): self.encode(item) for key, item in value.items()}
            return {"$dict": [[self.encode(key), self.encode(item)] for key, item in value.items()]}
        raise TypeError(f"Result type is not protocol-safe: {type(value).__name__}")

    def _callable(self, spec: dict[str, object], tracker: Telemetry) -> object:
        kind = spec.get("kind")

        def operation() -> object:
            tracker.calls += 1
            if kind == "raise":
                candidate = getattr(builtins, str(spec.get("exception", "RuntimeError")), None)
                exception_type = (
                    candidate
                    if isinstance(candidate, type) and issubclass(candidate, Exception)
                    else RuntimeError
                )
                raise exception_type(str(spec.get("message", "")))
            if kind == "observe_attribute":
                target = self.objects[str(spec["object_id"])]
                tracker.observed.append(getattr(target, str(spec["attribute"])))
            return self.decode(spec.get("return"), [])

        return operation

    def _async_worker(self, spec: dict[str, object], tracker: Telemetry) -> object:
        mode = spec.get("mode", "identity")
        release = asyncio.Event()
        milestone = asyncio.Event()

        async def worker(value: object) -> object:
            tracker.calls += 1
            tracker.active += 1
            tracker.maximum_active = max(tracker.maximum_active, tracker.active)
            try:
                if mode == "ordered":
                    if value == spec.get("blocked_value"):
                        await milestone.wait()
                    else:
                        milestone.set()
                elif mode == "concurrency_probe":
                    if tracker.active >= int(spec.get("target_active", 2)):
                        release.set()
                    await release.wait()
                elif mode == "failure_cleanup":
                    if value == spec.get("failure_value"):
                        await milestone.wait()
                        raise RuntimeError(str(spec.get("message", "boom")))
                    milestone.set()
                    await asyncio.Event().wait()
                elif mode == "block":
                    milestone.set()
                    await asyncio.Event().wait()
                transform = spec.get("transform", "identity")
                if transform == "upper":
                    return str(value).upper()
                if transform == "double":
                    return value * 2  # type: ignore[operator]
                if transform == "increment":
                    return value + 1  # type: ignore[operator]
                return value
            except asyncio.CancelledError:
                tracker.cancelled += 1
                raise
            finally:
                tracker.active -= 1

        worker._codejudge_milestone = milestone  # type: ignore[attr-defined]
        return worker


def _load_solution() -> tuple[object | None, dict[str, object] | None]:
    try:
        spec = importlib.util.spec_from_file_location("solution", _SOLUTION_PATH)
        if spec is None or spec.loader is None:
            raise ImportError("Candidate module cannot be loaded")
        module = importlib.util.module_from_spec(spec)
        sys.modules["solution"] = module
        spec.loader.exec_module(module)
        return module, None
    except BaseException as error:
        return None, {
            "type": type(error).__name__,
            "message": str(error),
            "syntax_error": isinstance(error, SyntaxError),
            "import_error": isinstance(error, (ImportError, ModuleNotFoundError)),
        }


async def _invoke(callable_object: object, args: list[object], kwargs: dict[str, object]) -> object:
    result = callable_object(*args, **kwargs)  # type: ignore[operator]
    if inspect.isawaitable(result):
        return await result
    return result


async def _step(module: object, context: InvocationContext, raw: object) -> dict[str, object]:
    if not isinstance(raw, dict) or not isinstance(raw.get("op"), str):
        return {"ok": False, "protocol_error": "invalid_step"}
    telemetry: list[Telemetry] = []
    try:
        operation = raw["op"]
        if operation == "store":
            context.objects[str(raw["object_id"])] = context.decode(raw.get("value"), telemetry)
            result: object = None
        elif operation == "construct":
            symbol = getattr(module, str(raw["symbol"]))
            args = context.decode(raw.get("args", []), telemetry)
            kwargs = context.decode(raw.get("kwargs", {}), telemetry)
            instance = symbol(*args, **kwargs)  # type: ignore[operator]
            context.objects[str(raw["object_id"])] = instance
            result = None
        elif operation == "call_function":
            symbol = getattr(module, str(raw["symbol"]))
            args = context.decode(raw.get("args", []), telemetry)
            kwargs = context.decode(raw.get("kwargs", {}), telemetry)
            if raw.get("cancel_after_worker_start") is True:
                worker = next(item for item in args if hasattr(item, "_codejudge_milestone"))
                task = asyncio.create_task(_invoke(symbol, args, kwargs))
                await worker._codejudge_milestone.wait()  # type: ignore[attr-defined]
                task.cancel()
                result = await task
            else:
                result = await _invoke(symbol, args, kwargs)
        elif operation == "call_method":
            target = context.objects[str(raw["object_id"])]
            method = getattr(target, str(raw["method"]))
            args = context.decode(raw.get("args", []), telemetry)
            kwargs = context.decode(raw.get("kwargs", {}), telemetry)
            result = await _invoke(method, args, kwargs)
        elif operation == "get_attr":
            result = getattr(context.objects[str(raw["object_id"])], str(raw["attribute"]))
        elif operation == "get_object":
            result = context.objects[str(raw["object_id"])]
        else:
            return {"ok": False, "protocol_error": "unsupported_step"}
        return {
            "ok": True,
            "result": context.encode(result),
            "telemetry": [tracker.__dict__ for tracker in telemetry],
        }
    except BaseException as error:
        return {
            "ok": False,
            "exception": {"type": type(error).__name__, "message": str(error)},
            "telemetry": [tracker.__dict__ for tracker in telemetry],
        }


async def main() -> int:
    module, startup_error = _load_solution()
    context = InvocationContext()
    step_count = 0
    for encoded in sys.stdin.buffer:
        try:
            request: object = json.loads(encoded)
        except json.JSONDecodeError:
            _send({"protocol_error": "invalid_request"})
            continue
        if not isinstance(request, dict):
            _send({"protocol_error": "invalid_request"})
            continue
        if request.get("op") == "shutdown":
            return 0
        if request.get("op") != "step":
            _send({"protocol_error": "invalid_request"})
            continue
        step_count += 1
        if step_count > _MAX_STEPS:
            _send({"protocol_error": "too_many_steps"})
            continue
        if startup_error is not None or module is None:
            _send({"startup_error": startup_error})
            continue
        outcome = await _step(module, context, request.get("step"))
        _send(outcome)
    return 0


if __name__ == "__main__":
    _PROTOCOL_STDOUT = os.fdopen(os.dup(sys.stdout.fileno()), "w", encoding="utf-8")
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    raise SystemExit(asyncio.run(main()))
