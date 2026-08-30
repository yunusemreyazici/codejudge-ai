# Release process

[← Project README](../../README.md) · [Documentation index](../README.md)

CodeJudge releases should preserve semantic provenance, database safety, and stored benchmark
history. A release metadata change is not permission to redesign a completed feature.

## Change discipline

Classify the release before editing:

- **Patch** — compatible correctness, reliability, security, or documentation hardening.
- **Minor** — additive capability with explicit contracts and tests.
- **Major** — incompatible public API, persistence, scoring, or archive semantics.

Do not rewrite persisted benchmark rows, archives, configs, dataset identities, or evaluation
snapshots to match the new runtime. Historical evaluations retain their execution-time version.

## Authoritative version locations

The runtime/project version is synchronized in exactly three places:

1. `pyproject.toml` project version;
2. `app/core/version.py` source-tree fallback;
3. the root package metadata in `uv.lock`.

Ordinary fixtures should call the authoritative runtime version helper instead of hard-coding the
expected runtime identity. An explicit mismatch test should use a deliberately fake historical
value.

Verify installed runtime metadata after a bump:

```bash
uv run python -c 'from app.core.version import codejudge_version; print(codejudge_version())'
```

## Verification checklist

```bash
git diff --check
uv run ruff check .
uv run ruff format --check .
uv run mypy app
```

Then run focused tests for the change, the guarded non-sandbox suite, and the sandbox/privacy suite
when production sandbox code or the repository release gate requires it. The exact infrastructure
command is in [Testing](testing.md).

Before and after validation:

- verify the development database revision, schema fingerprint, and material row counts;
- verify benchmark config and archive hashes;
- check `git status --short` for temporary audit helpers or generated results;
- confirm no real provider call occurred;
- confirm no schema migration exists unless the change explicitly requires one.

## Documentation checks

For documentation releases, inspect all relative links, fenced code blocks, Mermaid fences, command
names, workflow/job names, and facts derived from source. Historical benchmark pages must report the
execution-time CodeJudge version stored on evaluation snapshots, not merely the current package or
archive-creation version.

## Commit and tag

After review, the operator—not an automated validation task—may create a signed-off release commit
and annotated tag. Example placeholders:

```bash
git add <reviewed-files>
git commit -m "docs: refresh project documentation"
git tag -a vX.Y.Z -m "CodeJudge AI vX.Y.Z"
```

Inspect the staged diff before committing. Push the commit and tag only after local and CI evidence
is green. Do not combine untracked local benchmark configs or generated archives with a release
commit.
