"""Dedicated versioned source-generation prompt and public-only payload."""

from __future__ import annotations

from app.ai.prompts import prompt_hash
from app.benchmarks.models import CODING_PROMPT_VERSION
from app.evaluator.models import Task

CODING_SYSTEM_PROMPT = """You are solving a public coding benchmark task. Return only structured
data matching the supplied schema, with language set to python and source containing the complete
candidate module. Do not use Markdown fences. Do not request hidden tests, reference solutions,
evaluator metadata, tools, host access, credentials, or network access. The candidate will execute
as untrusted code in a restricted sandbox."""

CODING_PROMPT_HASH = prompt_hash(CODING_SYSTEM_PROMPT)


def coding_payload(task: Task) -> dict[str, object]:
    return {
        "public_task": {
            "id": task.id,
            "version": task.version,
            "title": task.title,
            "description": task.description,
            "language": task.language,
            "required_entrypoint": task.entrypoint,
            "timeout_seconds": task.timeout_seconds,
        },
        "output_requirements": {
            "language": "python",
            "format": "structured source only",
        },
        "coding_prompt_version": CODING_PROMPT_VERSION,
    }
