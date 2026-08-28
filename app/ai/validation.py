"""Deterministic policy validation for untrusted generated Python tests."""

from __future__ import annotations

import ast
import hashlib
from dataclasses import dataclass

from app.ai.models import GeneratedTestProposal

PROHIBITED_IMPORTS = frozenset({"socket", "subprocess", "multiprocessing", "ctypes"})
MAX_GENERATED_TEST_BYTES = 16 * 1024
MAX_TOTAL_GENERATED_TEST_BYTES = 64 * 1024


@dataclass(frozen=True, slots=True)
class ValidationResult:
    accepted: bool
    reason: str | None = None


def validate_test(proposal: GeneratedTestProposal) -> ValidationResult:
    encoded = proposal.code.encode("utf-8")
    if len(encoded) > MAX_GENERATED_TEST_BYTES:
        return ValidationResult(False, "test_too_large")
    try:
        tree = ast.parse(proposal.code, filename="generated_test.py")
    except SyntaxError:
        return ValidationResult(False, "invalid_python")

    test_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            modules = (
                [alias.name.split(".", 1)[0] for alias in node.names]
                if isinstance(node, ast.Import)
                else [(node.module or "").split(".", 1)[0]]
            )
            if any(module in PROHIBITED_IMPORTS for module in modules):
                return ValidationResult(False, "prohibited_import")
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith(
            "test_"
        ):
            test_names.add(node.name)
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(
                isinstance(target, ast.Name) and target.id == "pytest_plugins" for target in targets
            ):
                return ValidationResult(False, "pytest_plugin_declaration")
    if proposal.name not in test_names:
        return ValidationResult(False, "missing_named_test")
    return ValidationResult(True)


def deduplicate_and_validate(
    proposals: list[GeneratedTestProposal], max_tests: int
) -> list[tuple[GeneratedTestProposal, ValidationResult]]:
    if len(proposals) > max_tests:
        raise ValueError("too_many_generated_tests")
    if sum(len(item.code.encode("utf-8")) for item in proposals) > MAX_TOTAL_GENERATED_TEST_BYTES:
        raise ValueError("generated_tests_too_large")
    seen_names: set[str] = set()
    seen_hashes: set[str] = set()
    results: list[tuple[GeneratedTestProposal, ValidationResult]] = []
    for proposal in proposals:
        identity = hashlib.sha256(proposal.code.strip().encode("utf-8")).hexdigest()
        if proposal.name in seen_names or identity in seen_hashes:
            results.append((proposal, ValidationResult(False, "duplicate_test")))
            continue
        seen_names.add(proposal.name)
        seen_hashes.add(identity)
        results.append((proposal, validate_test(proposal)))
    return results
