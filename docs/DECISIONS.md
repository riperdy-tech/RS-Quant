# Engineering decisions

Material assumptions, substitutions, and deviations from the implementation plan are recorded here.

## 2026-09-12

- Initialized the empty user-selected workspace as a new Git repository on branch `quantdesk-implementation`. There was no existing checkout or main branch to isolate in a linked worktree.
- The host has CPython 3.12.10 and Node 24.12.0. Python matches the required compatibility baseline; Node is newer than the plan's Node 22 build-tool baseline and will be recorded as the tested platform unless a Node 22-specific incompatibility appears.
- The user requested `docs/STATUS.md`; the plan requires `docs/BUILD_STATUS.md`. Both are maintained: BUILD_STATUS is the detailed evidence ledger and STATUS is the concise operator-facing summary.
- Installed `uv 0.12.13` into the current user's Python 3.12 environment because the required build tool was absent.
- Interpreted Task 02's “negative/corrupted ledger postings rejection” as rejection of malformed or unbalanced postings, not valid negative credit postings required by the plan's debit-positive double-entry examples.
- The simulator will emit unsequenced incoming execution/report facts for the single account engine to assign envelope IDs and `engine_seq`; Task 10's `Envelope` return annotation is treated as shorthand because §6.1 reserves sequencing to the engine writer.
