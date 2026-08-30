# Static analysis

[← Project README](../../README.md) · [Documentation index](../README.md)

CodeJudge runs deterministic analyzers over the exact submitted Python source after official-test
execution. The analysis engine never imports the candidate module.

## Analyzer set

| Tool | Dimension | Purpose |
| --- | --- | --- |
| Ruff | Code quality | Isolated `E`, `F`, `B`, `UP`, and `SIM` diagnostics |
| mypy | Type safety | Packaged config; skips followed imports/site packages and does not require annotations |
| Bandit | Security | Severity/confidence findings; candidate `# nosec` does not suppress scoring |
| Radon | Complexity | Cyclomatic complexity evidence and complexity scoring |

Each analyzer receives one temporary `solution.py` containing the exact source text. Tools run
sequentially with bounded execution time and output. Normalized findings include tool identity,
rule or category, severity, message, and source position where available.

## Determinism and provenance

The evaluation snapshot records analyzer versions and normalized findings. Analyzer identity is
part of queued-job integrity validation and reproducibility metadata, so a worker will not silently
evaluate persisted work after the expected analyzer environment changes.

Static results are converted into the versioned code-quality, type-safety, security, and complexity
dimensions described in [Scoring](scoring.md). Routes and benchmark aggregation do not reinterpret
raw analyzer output.

## Failure policy

Analyzer findings are candidate evidence. Analyzer infrastructure failure is not. A timeout,
missing executable, malformed analyzer output, or incomplete dimension set fails the evaluation
closed instead of silently treating the missing tool as a perfect score or dropping its weight.

When `STATIC_ANALYSIS_ENABLED=false`, every static dimension is intentionally absent and the final
score becomes correctness-only. This explicit mode is distinct from partial analyzer failure.

## Configuration

| Variable | Default | Meaning |
| --- | ---: | --- |
| `STATIC_ANALYSIS_ENABLED` | `true` | Enable all deterministic analyzers |
| `STATIC_ANALYSIS_TIMEOUT_SECONDS` | `5.0` | Per-analyzer process timeout |
| `STATIC_ANALYSIS_OUTPUT_LIMIT_BYTES` | `262144` | Per-analyzer combined output bound |

Candidate source may be malicious. Static analyzers are a quality and evidence layer, not the
sandbox boundary. Official execution still belongs in the restricted Docker runner, and analyzer
dependencies remain trusted infrastructure.

Static analysis cannot prove correctness or security. Bandit is heuristic, mypy is not a runtime
safety proof, and cyclomatic complexity is one maintainability signal.
