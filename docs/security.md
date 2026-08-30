# Security documentation

This path is retained for compatibility with older links. The security documentation is now split
by concern:

- [Sandbox security model](sandbox/security-model.md) — threat model, trust boundaries,
  implemented controls, provider/AI boundaries, and residual risk.
- [Sandbox resource limits](sandbox/resource-limits.md) — CPU, memory/swap, PIDs, timeout, output,
  OOM evidence, metadata ordering, and cleanup.
- [Database operations](operations/database.md) — immutable evidence and the destructive-test
  database safety boundary.

Docker is the recommended execution backend, but it is not a perfect boundary against kernel or
container-runtime escape. The `local` backend must never execute untrusted submissions.
