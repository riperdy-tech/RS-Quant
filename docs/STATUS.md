# QuantDesk implementation status

Implementation is in progress. See [BUILD_STATUS.md](BUILD_STATUS.md) for task-by-task commands and evidence and [DECISIONS.md](DECISIONS.md) for material rulings.

- Completed: Tasks 01–02 — foundation, exact boundaries, raw journal, atomic event store, migrations, and consistent encrypted backups
- Current task: Task 03 — serial engine, scheduling, checkpoint, and deterministic replay
- Default/live state: DEMO; LIVE disabled
- Preflight: complete; architecture and current Bitget documentation conflicts ruled conservatively
- External checks: Bitget credentials/sandbox, physical power-loss durability, Windows ACL/key-store integration, timed paper/shadow gates, and all live readiness remain pending
