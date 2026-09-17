# QuantDesk AI contributor guide

The authoritative product and implementation specification is [docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md). Read it completely before changing code, and implement its numbered tasks in dependency order.

Essential rules:

- STRICT COMPLIANCE: I must strictly follow the implementation plan requirements on each task TO THE WORD. I will not deviate from it, cut corners, mock core functionality to speed up the process, or invent new unapproved architectures on my own.
- Preserve deterministic single-writer event processing, append-only audit history, exact Decimal financial arithmetic, idempotent economic effects, atomic event/ledger/outbox commits, and strict venue/core separation.
- Default to DEMO and fail closed. Never enable LIVE, place mainnet orders, alter exchange settings, request production credentials, or treat unavailable external checks as passed.
- Use boundary fakes only for external systems. Strategies, reducers, OMS, ledger, risk, replay, simulator, API, and browser workflows must exercise real local components.
- Follow test-first red/green/refactor work. Run every task gate before advancing; diagnose failures instead of weakening or bypassing checks.
- Keep docs/DECISIONS.md, docs/STATUS.md, and docs/BUILD_STATUS.md accurate with assumptions, commands, evidence, and externally blocked checks.
- Prevent concurrent edits to the same files. Inspect delegated changes and independently verify them before integration.
- Do not commit secrets, mutable runtime data, generated credentials, or private venue payloads.
