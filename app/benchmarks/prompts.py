"""Dedicated versioned source-generation prompt and public-only payload."""

from __future__ import annotations

from app.ai.prompts import canonical_json, prompt_hash
from app.benchmarks.models import CODING_PROMPT_VERSION, GenerationOutputMode
from app.evaluator.models import Task

CODING_SYSTEM_PROMPT = """You are solving a public coding benchmark task. Return only structured
data matching the supplied schema, with language set to python and source containing the complete
candidate module. Do not use Markdown fences. Do not request hidden tests, reference solutions,
evaluator metadata, tools, host access, credentials, or network access. The candidate will execute
as untrusted code in a restricted sandbox."""

RAW_SOURCE_CODING_SYSTEM_PROMPT = """You are solving a public coding benchmark task. Return only
valid Python source code as the exact assistant message content. Do not include Markdown fences.
Do not include explanation. Do not include JSON. Do not include prose before or after the program.
Do not request hidden tests, reference solutions, evaluator metadata, tools, host access,
credentials, or network access. The candidate will execute as untrusted code in a restricted
sandbox."""

_PROMPTS = {
    GenerationOutputMode.STRUCTURED_JSON: CODING_SYSTEM_PROMPT,
    GenerationOutputMode.RAW_SOURCE: RAW_SOURCE_CODING_SYSTEM_PROMPT,
}
CODING_PROMPT_HASH = prompt_hash(
    canonical_json({mode.value: value for mode, value in _PROMPTS.items()})
)


def coding_system_prompt(output_mode: GenerationOutputMode) -> str:
    return _PROMPTS[output_mode]


def model_coding_prompt_hash(output_mode: GenerationOutputMode) -> str:
    return prompt_hash(coding_system_prompt(output_mode))


def coding_payload(
    task: Task,
    output_mode: GenerationOutputMode = GenerationOutputMode.STRUCTURED_JSON,
) -> dict[str, object]:
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
            "format": (
                "structured JSON with language and source fields"
                if output_mode is GenerationOutputMode.STRUCTURED_JSON
                else "exact raw Python source in assistant message content"
            ),
        },
        "coding_prompt_version": CODING_PROMPT_VERSION,
    }
