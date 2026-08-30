"""Named, reviewable mutations for every task in codejudge-core@3."""

# Exact source fragments are intentionally kept inline and may exceed normal line length.
# ruff: noqa: E501

from __future__ import annotations

from tests.tasks.mutation_audit import MutationDefinition, SourceReplacement


def _mutation(
    task_id: str,
    name: str,
    old: str,
    new: str,
    *additional: tuple[str, str],
    equivalent_reason: str | None = None,
    survivor_reason: str | None = None,
) -> MutationDefinition:
    return MutationDefinition(
        task_id=task_id,
        name=name,
        replacements=(
            SourceReplacement(old, new),
            *(SourceReplacement(before, after) for before, after in additional),
        ),
        equivalent_reason=equivalent_reason,
        survivor_reason=survivor_reason,
    )


MUTATIONS = (
    _mutation(
        "async-batch-processor",
        "skips_concurrency_validation_for_empty_input",
        "if isinstance(concurrency, bool) or not isinstance(concurrency, int) or concurrency <= 0:",
        "if items and (isinstance(concurrency, bool) or not isinstance(concurrency, int) or concurrency <= 0):",
    ),
    _mutation(
        "async-batch-processor",
        "ignores_concurrency_bound",
        "    async def invoke(item: InputT) -> ResultT:\n        async with semaphore:\n            return await worker(item)",
        "    async def invoke(item: InputT) -> ResultT:\n        return await worker(item)",
    ),
    _mutation(
        "async-batch-processor",
        "returns_completion_order",
        "return list(await asyncio.gather(*tasks))",
        "return [await task for task in asyncio.as_completed(tasks)]",
    ),
    _mutation(
        "async-batch-processor",
        "does_not_cancel_siblings_after_worker_failure",
        "    except BaseException:\n        for task in tasks:\n            task.cancel()\n        await asyncio.gather(*tasks, return_exceptions=True)\n        raise",
        "    except BaseException:\n        raise",
    ),
    _mutation(
        "async-batch-processor",
        "relies_on_gather_for_batch_cancellation_cleanup",
        "except BaseException:",
        "except Exception:",
        equivalent_reason=(
            "Cancelling the awaited gather propagates cancellation to its unfinished children; "
            "the documented cleanup remains observable without the broader catch."
        ),
    ),
    _mutation(
        "async-batch-processor",
        "invokes_each_worker_twice",
        "        async with semaphore:\n            return await worker(item)",
        "        async with semaphore:\n            await worker(item)\n            return await worker(item)",
    ),
    _mutation(
        "circuit-breaker",
        "opens_one_failure_late",
        "if self._failure_count >= self._threshold:",
        "if self._failure_count > self._threshold:",
    ),
    _mutation(
        "circuit-breaker",
        "keeps_open_at_recovery_boundary",
        "if now < self._opened_at + self._recovery_timeout:",
        "if now <= self._opened_at + self._recovery_timeout:",
    ),
    _mutation(
        "circuit-breaker",
        "probe_observes_closed_state",
        '            self._state = "half_open"',
        '            self._state = "closed"',
    ),
    _mutation(
        "circuit-breaker",
        "success_does_not_reset_failure_count",
        '        self._state = "closed"\n        self._failure_count = 0\n        self._opened_at = None\n        return result',
        '        self._state = "closed"\n        self._opened_at = None\n        return result',
    ),
    _mutation(
        "circuit-breaker",
        "failed_probe_does_not_restart_timeout",
        '            if self._state == "half_open":\n                self._open(now)',
        '            if self._state == "half_open":\n                self._state = "open"',
    ),
    _mutation(
        "circuit-breaker",
        "reset_keeps_timestamp_history",
        "        self._opened_at = None\n        self._last_now = None\n\n    def _open",
        "        self._opened_at = None\n\n    def _open",
    ),
    _mutation(
        "config-layer-merge",
        "uses_shallow_layer_update",
        "    for layer in layers:\n        _merge(result, layer)",
        "    for layer in layers:\n        result.update(layer)",
    ),
    _mutation(
        "config-layer-merge",
        "stores_deletion_marker",
        "        if value is None:\n            target.pop(key, None)",
        "        if value is None:\n            target[key] = None",
    ),
    _mutation(
        "config-layer-merge",
        "replaces_nested_mapping_instead_of_merging",
        "            nested = deepcopy(existing) if isinstance(existing, dict) else {}",
        "            nested = {}",
    ),
    _mutation(
        "config-layer-merge",
        "shares_nested_mutable_values",
        "            target[key] = deepcopy(value)",
        "            target[key] = value",
    ),
    _mutation(
        "config-layer-merge",
        "skips_key_validation_inside_lists",
        "    elif isinstance(value, list):\n        for nested in value:\n            _validate_keys(nested)",
        "    elif isinstance(value, list):\n        pass",
    ),
    _mutation(
        "config-layer-merge",
        "accepts_tuple_of_layers",
        "if not isinstance(layers, list):",
        "if not isinstance(layers, (list, tuple)):",
    ),
    _mutation(
        "dependency-resolver",
        "uses_lifo_tie_breaking",
        "        node = heapq.heappop(available)",
        "        node = available.pop()",
    ),
    _mutation(
        "dependency-resolver",
        "omits_unknown_leaf_nodes",
        "        all_nodes.update(unique)",
        "        pass",
    ),
    _mutation(
        "dependency-resolver",
        "counts_duplicate_edges_twice",
        "        dependencies[node] = unique",
        "        dependencies[node] = list(raw_dependencies)",
    ),
    _mutation(
        "dependency-resolver",
        "returns_partial_order_for_cycles",
        '    if len(resolved) != len(all_nodes):\n        raise DependencyCycleError("dependency graph contains a cycle")',
        "    if len(resolved) != len(all_nodes):\n        return resolved",
    ),
    _mutation(
        "dependency-resolver",
        "mutates_dependency_lists_by_sorting",
        "        unique: set[str] = set()",
        "        raw_dependencies.sort()\n        unique: set[str] = set()",
    ),
    _mutation(
        "dependency-resolver",
        "reverses_valid_topological_order",
        "    return resolved",
        "    return list(reversed(resolved))",
    ),
    _mutation(
        "frame-decoder",
        "counts_utf8_bytes_instead_of_characters",
        "                if len(self._buffer) < self._expected:",
        '                if len(self._buffer.encode("utf-8")) < self._expected:',
        survivor_reason=(
            "The contract defines character counts across arbitrary chunks, but the released "
            "cases do not split a Unicode payload where byte length reaches the prefix first."
        ),
    ),
    _mutation(
        "frame-decoder",
        "accepts_leading_zero_prefix",
        '        if self._buffer.startswith("0") and len(self._buffer) > 1:\n            raise ValueError("malformed frame length")',
        '        if False:\n            raise ValueError("malformed frame length")',
        (
            '        and (prefix == "0" or not prefix.startswith("0"))',
            "        and True",
        ),
    ),
    _mutation(
        "frame-decoder",
        "allows_oversized_configuration",
        "            or not 0 < max_frame_size <= 1_000_000",
        "            or max_frame_size <= 0",
    ),
    _mutation(
        "frame-decoder",
        "does_not_reset_after_malformed_input",
        "        except ValueError:\n            self._reset()\n            raise",
        "        except ValueError:\n            raise",
    ),
    _mutation(
        "frame-decoder",
        "nonstring_feed_corrupts_buffer",
        '        if not isinstance(chunk, str):\n            raise TypeError("chunk must be a string")\n        self._buffer += chunk',
        '        if not isinstance(chunk, str):\n            self._buffer += str(chunk)\n            raise TypeError("chunk must be a string")\n        self._buffer += chunk',
    ),
    _mutation(
        "frame-decoder",
        "finish_silently_discards_truncated_frame",
        '            self._reset()\n            raise ValueError("truncated frame")',
        "            self._reset()\n            return None",
    ),
    _mutation(
        "interval-reservation",
        "treats_adjacent_intervals_as_overlapping",
        "start < existing_end and existing_start < end",
        "start <= existing_end and existing_start <= end",
    ),
    _mutation(
        "interval-reservation",
        "misses_containing_overlap",
        "start < existing_end and existing_start < end",
        "existing_start <= start < existing_end or existing_start < end <= existing_end",
    ),
    _mutation(
        "interval-reservation",
        "allows_duplicate_id_on_different_resource",
        "        if reservation_id in self._by_id:",
        "        if reservation_id in self._by_id and self._by_id[reservation_id][0] == resource:",
    ),
    _mutation(
        "interval-reservation",
        "does_not_sort_reservations",
        '        return sorted(result, key=lambda item: (item["start"], item["end"], item["id"]))',
        "        return result",
    ),
    _mutation(
        "interval-reservation",
        "cancel_does_not_release_interval",
        "        return self._by_id.pop(reservation_id, None) is not None",
        "        return reservation_id in self._by_id",
    ),
    _mutation(
        "interval-reservation",
        "treats_resources_as_one_global_calendar",
        "            if existing_resource == resource and start < existing_end and existing_start < end:",
        "            if start < existing_end and existing_start < end:",
    ),
    _mutation(
        "logical-path",
        "ignores_parent_components",
        '        if component == "..":\n            if not result:\n                raise ValueError("path traverses above root")\n            result.pop()',
        '        if component == "..":\n            continue',
    ),
    _mutation(
        "logical-path",
        "clamps_traversal_above_root",
        '            if not result:\n                raise ValueError("path traverses above root")',
        "            if not result:\n                continue",
    ),
    _mutation(
        "logical-path",
        "treats_backslash_as_separator",
        '    result = initial.copy()\n    for component in value.split("/"):',
        '    result = initial.copy()\n    for component in value.replace("\\\\", "/").split("/"):',
    ),
    _mutation(
        "logical-path",
        "relative_path_ignores_cwd",
        '    base = [] if path.startswith("/") else _components(cwd, [])',
        "    base = []",
    ),
    _mutation(
        "logical-path",
        "absolute_path_keeps_cwd",
        '    base = [] if path.startswith("/") else _components(cwd, [])',
        "    base = _components(cwd, [])",
    ),
    _mutation(
        "logical-path",
        "preserves_trailing_slash",
        '    return "/" + "/".join(resolved)',
        '    return "/" + "/".join(resolved) + ("/" if path.endswith("/") and resolved else "")',
    ),
    _mutation(
        "lru-cache",
        "does_not_refresh_recency_on_get",
        "        self._items.move_to_end(key)\n        return self._items[key]",
        "        return self._items[key]",
    ),
    _mutation(
        "lru-cache",
        "does_not_refresh_recency_on_update",
        "        if key in self._items:\n            self._items.move_to_end(key)",
        "        if key in self._items:\n            pass",
    ),
    _mutation(
        "lru-cache",
        "evicts_most_recently_used_key",
        "            self._items.popitem(last=False)",
        "            self._items.popitem(last=True)",
    ),
    _mutation(
        "lru-cache",
        "evicts_when_cache_reaches_capacity",
        "        if len(self._items) > self._capacity:",
        "        if len(self._items) >= self._capacity:",
    ),
    _mutation(
        "lru-cache",
        "missing_key_returns_none",
        "            return -1",
        "            return None",
    ),
    _mutation(
        "lru-cache",
        "existing_key_value_is_not_updated",
        "        if key in self._items:\n            self._items.move_to_end(key)\n        self._items[key] = value",
        "        if key in self._items:\n            self._items.move_to_end(key)\n            return\n        self._items[key] = value",
    ),
    _mutation(
        "rate-limiter",
        "retains_event_at_exact_window_boundary",
        "        while events and events[0] <= boundary:",
        "        while events and events[0] < boundary:",
    ),
    _mutation(
        "rate-limiter",
        "records_rejected_attempts",
        "        if len(events) >= self._limit:\n            return False\n        events.append(now)",
        "        events.append(now)\n        if len(events) > self._limit:\n            return False",
    ),
    _mutation(
        "rate-limiter",
        "uses_one_window_for_all_keys",
        "        events = self._events[key]",
        '        events = self._events["global"]',
    ),
    _mutation(
        "rate-limiter",
        "uses_global_timestamp_monotonicity",
        "        previous = self._last_call.get(key)",
        "        previous = max(self._last_call.values(), default=None)",
    ),
    _mutation(
        "rate-limiter",
        "allows_one_event_above_limit",
        "        if len(events) >= self._limit:",
        "        if len(events) > self._limit:",
    ),
    _mutation(
        "rate-limiter",
        "prunes_at_most_one_expired_event_per_call",
        "        while events and events[0] <= boundary:",
        "        if events and events[0] <= boundary:",
        equivalent_reason=(
            "The queue never exceeds the configured limit, so removing one expired event is "
            "sufficient to make the current allow decision; later calls continue pruning."
        ),
    ),
    _mutation(
        "retry-backoff",
        "uses_zero_based_attempt_exponent",
        "    for _ in range(attempt - 1):",
        "    for _ in range(attempt):",
    ),
    _mutation(
        "retry-backoff",
        "does_not_cap_delay",
        "        delay = min(cap, delay * factor)",
        "        delay = delay * factor",
        ("    return min(delay, cap)", "    return delay"),
    ),
    _mutation(
        "retry-backoff",
        "ignores_custom_multiplier",
        "    factor = float(multiplier)",
        "    factor = 2.0",
    ),
    _mutation(
        "retry-backoff",
        "accepts_boolean_attempt",
        "if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 1:",
        "if not isinstance(attempt, int) or attempt < 1:",
    ),
    _mutation(
        "retry-backoff",
        "uses_overflow_prone_closed_form",
        "    delay = float(base_delay)\n    cap = float(max_delay)\n    factor = float(multiplier)\n    for _ in range(attempt - 1):\n        if delay >= cap or factor == 1:\n            break\n        delay = min(cap, delay * factor)\n    return min(delay, cap)",
        "    delay = float(base_delay) * float(multiplier) ** (attempt - 1)\n    return min(delay, float(max_delay))",
    ),
    _mutation(
        "retry-backoff",
        "rejects_equal_base_and_cap",
        "if isinstance(max_delay, bool) or max_delay < base_delay:",
        "if isinstance(max_delay, bool) or max_delay <= base_delay:",
        survivor_reason=(
            "The contract allows max_delay equal to base_delay, but the released cases do not "
            "exercise equality."
        ),
    ),
    _mutation(
        "structured-event-parser",
        "allows_unknown_fields",
        '        if set(raw) - {"id", "timestamp", "kind", "payload"}:\n            raise ValueError("unknown event field")',
        '        if False:\n            raise ValueError("unknown event field")',
    ),
    _mutation(
        "structured-event-parser",
        "allows_duplicate_ids",
        '        if event_id in seen:\n            raise ValueError("duplicate event id")',
        '        if False:\n            raise ValueError("duplicate event id")',
    ),
    _mutation(
        "structured-event-parser",
        "allows_decreasing_timestamps",
        '        if previous_timestamp is not None and timestamp < previous_timestamp:\n            raise ValueError("timestamps must be nondecreasing")',
        '        if False:\n            raise ValueError("timestamps must be nondecreasing")',
    ),
    _mutation(
        "structured-event-parser",
        "accepts_boolean_timestamp",
        "if isinstance(timestamp, bool) or not isinstance(timestamp, int) or timestamp < 0:",
        "if not isinstance(timestamp, int) or timestamp < 0:",
    ),
    _mutation(
        "structured-event-parser",
        "accepts_arbitrary_string_kind",
        'if not isinstance(kind, str) or kind not in {"created", "updated", "deleted"}:',
        "if not isinstance(kind, str):",
    ),
    _mutation(
        "structured-event-parser",
        "stable_sort_by_timestamp",
        "    return result",
        '    return sorted(result, key=lambda event: event["timestamp"])',
        equivalent_reason=(
            "Valid inputs already require nondecreasing timestamps, so a stable timestamp sort "
            "cannot change the documented result order."
        ),
    ),
    _mutation(
        "ttl-cache",
        "expires_only_after_boundary",
        "if expires_at <= now",
        "if expires_at < now",
    ),
    _mutation(
        "ttl-cache",
        "does_not_refresh_recency_on_get",
        "        self._items.move_to_end(key)\n        return item[0]",
        "        return item[0]",
    ),
    _mutation(
        "ttl-cache",
        "overwrite_does_not_refresh_recency",
        "        if key in self._items:\n            del self._items[key]",
        "        if key in self._items:\n            pass",
    ),
    _mutation(
        "ttl-cache",
        "expired_entries_consume_capacity_on_put",
        "        self._purge(now)\n        if key in self._items:",
        "        if key in self._items:",
        survivor_reason=(
            "The released capacity case makes the expired entry least-recently-used, so eviction "
            "masks the missing pre-put purge."
        ),
    ),
    _mutation(
        "ttl-cache",
        "delete_does_not_purge_expired_entries",
        "    def delete(self, key: str, now: float) -> bool:\n        self._purge(now)",
        "    def delete(self, key: str, now: float) -> bool:",
        survivor_reason=(
            "The released expiration case performs get before delete, so direct deletion of an "
            "expired entry is not observed."
        ),
    ),
    _mutation(
        "ttl-cache",
        "accepts_boolean_capacity",
        "if isinstance(capacity, bool) or not isinstance(capacity, int) or capacity <= 0:",
        "if not isinstance(capacity, int) or capacity <= 0:",
    ),
)


MUTATIONS_BY_TASK = {
    task_id: tuple(mutation for mutation in MUTATIONS if mutation.task_id == task_id)
    for task_id in sorted({mutation.task_id for mutation in MUTATIONS})
}
