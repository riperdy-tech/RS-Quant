# QuantDesk implementation status

Implementation is in progress. See [BUILD_STATUS.md](BUILD_STATUS.md) for task-by-task commands and evidence and [DECISIONS.md](DECISIONS.md) for material rulings.

- Completed: Tasks 01–04 — foundation, persistence/replay, instruments, validated books/bars, recording, imports, and immutable dataset catalog
- Current task: Task 05 — exact accounting ledger, positions, fees/funding, margin, and reconciliation
- Default/live state: DEMO; LIVE disabled
- Preflight: complete; architecture and current Bitget documentation conflicts ruled conservatively
- External checks: Bitget credentials/sandbox, physical power-loss durability, Windows ACL/key-store integration, timed paper/shadow gates, and all live readiness remain pending
