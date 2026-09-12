# Engineering decisions

Material assumptions, substitutions, and deviations from the implementation plan are recorded here.

## 2026-09-12

- Initialized the empty user-selected workspace as a new Git repository on branch `quantdesk-implementation`. There was no existing checkout or main branch to isolate in a linked worktree.
- The host has CPython 3.12.10 and Node 24.12.0. Python matches the required compatibility baseline; Node is newer than the plan's Node 22 build-tool baseline and will be recorded as the tested platform unless a Node 22-specific incompatibility appears.
- The user requested `docs/STATUS.md`; the plan requires `docs/BUILD_STATUS.md`. Both are maintained: BUILD_STATUS is the detailed evidence ledger and STATUS is the concise operator-facing summary.
- Installed `uv 0.12.13` into the current user's Python 3.12 environment because the required build tool was absent.
- Interpreted Task 02's “negative/corrupted ledger postings rejection” as rejection of malformed or unbalanced postings, not valid negative credit postings required by the plan's debit-positive double-entry examples.
- The simulator will emit unsequenced incoming execution/report facts for the single account engine to assign envelope IDs and `engine_seq`; Task 10's `Envelope` return annotation is treated as shorthand because §6.1 reserves sequencing to the engine writer.
- Cross-task prerequisites are pulled forward where an earlier gate otherwise depends on a later component: persistence owns reusable backup consistency, execution owns OS-lock/dispatch-permit primitives, API commands expose real replay/backup services, and integrated fault gates are rerun after packaging/supervisor work.
- Recovery reducers have separate fact-application and decision-generation paths. State recovery consumes recorded facts without generating fresh decisions; forensic replay regenerates derived history from input facts.
- Only the account writer grants a single-use gateway dispatch permit bound to instruction, expiry, risk/latch version, and ownership epoch, with revalidation immediately before transport handoff.
- Native contingent protection coverage and immediately executable close reservations are separate. A venue profile that cannot safely coexist or replace them is not live-eligible.
- Kill latches risk, disarms LIVE, and pauses entries. Reset does not rearm or resume. Invalid market data never disables already-owned protection or a separately authorized exposure-reducing recovery policy.
- Command confirmation uses typed target revisions, explicit confirmation text, canonical payload hashes, actor/environment/account-bound single-use tokens, and duplicate-ID/body resolution before token-reuse rejection.
- Research workers publish immutable artifacts only; the API owns jobs/catalog observations; the account engine alone activates strategies, configs, models, recorders, and sessions at event boundaries.
- Workflow capability checks are independent: OHLCV supports conservative bar research, not L2 or depth labels. Rich shipped fixtures drive offline ML. Missing margin inputs yield `INCOMPLETE_MARGIN_MODEL`.
- Performance evidence always retains the plan's reference load result. A reduced profile is separate and cannot rename a reference-target failure as PASS.
- Current official Bitget UTA documentation (checked 2026-09-12) uses `GET /api/v3/market/instruments`; this supersedes the older path in the plan for the versioned adapter.
- Bitget client order IDs use the safe documented intersection `[A-Za-z0-9_-]{1,32}`. The current place-order endpoint schema sends `marginMode=isolated`, while live readiness also verifies the account and symbol settings. Conflicting migration prose keeps credentialed acceptance pending until an authorized sandbox check.
- Bitget recovery is topic-specific: account and position subscriptions provide initial observations; the order subscription does not, so open orders and history are REST-bootstrapped and all sources are economically deduplicated.
- Bitget sandbox fencing binds demo credentials, the `paptrading: 1` REST header, demo WebSocket endpoints, and environment/client-ID namespaces; a shared REST hostname is never sufficient proof of sandbox routing.
- Every SQLite backup is rebuilt in memory with `VACUUM` before serialization so deleted private page contents cannot leak into otherwise public backup artifacts. This makes backup memory use proportional to database size; later UI/storage checks must enforce capacity rather than silently falling back to an unsafe physical copy.
- Plaintext persistence backups are permitted only for an exact known schema containing accountless allowlisted public-market facts. Any account/control state, private raw data, unknown event, populated non-public table, or schema drift requires the injected encryption boundary.
- The deterministic callback pipeline is stage-major: each stage applies its fact reducers before its decision producers; emitted children catch up through fact reducers through their birth stage and can influence only later decision stages. Recovery replays recorded child slots through the same schedule without invoking decision functions. Composition must change the code fingerprint whenever this schedule changes.
- Hypothesis wall-clock deadlines are disabled only on the ordering property that performs real `synchronous=FULL` SQLite/journal I/O on Windows. Its 100 generated examples and all correctness assertions remain intact; performance is measured separately by Task 16.
- Dataset capabilities are evidence-derived from recognized, fully validated Parquet schemas and row content on publication and every catalog read. Manifest or Parquet metadata labels alone never grant L2, trade, BBO, mark, funding, or OHLCV eligibility.
- Task 04 ships only explicitly named generic recorded/synthetic book-sequence contracts and a declared generic raw-trade import schema. Unknown checksum/topic semantics fail closed until the versioned Bitget adapter in Task 07 supplies official protocol rules.
- Late venue identifiers are appended through an atomic `EconomicAliasUpdate` effect tied to a current canonical observation and the original transaction scope. Supplemental alias/adjustment evidence is itself recorded as `FinancialEventObserved`; an in-memory wrapper alone is never replay evidence.
- Margin preserves the position subledger's stored base quantity and treats compatible current metadata only as the unit converter for possible future fills. Future, unverifiable, or incompatible instrument revisions yield `INCOMPLETE_MARGIN_MODEL`; pending extrema include valid reduce-only-before-ordinary execution orderings.

## 2026-09-13

- Outbox status changes are first-class atomic transition effects with causal-event validation and compare-and-set rules. The additive schema migration retains prior rows while allowing explicit UNKNOWN/BLOCKED/CANCELED/REJECTED states.
- Reported-but-unaccounted executions remain a separate unresolved financial-reserve dimension. They retain quantity, cash, and fee exposure even after local position changes; only executable close lots compete for known closing capacity.
- The account writer owns dispatch authorization and revalidates committed OMS state after every preparation await. A cancellation committed before transport handoff revokes a known-unsent submission and resolves its submit/cancel/reservation/outbox effects atomically. Task 07 must make and test the concrete adapter's synchronous byte-handoff boundary.
- No production transport is enabled by the core router. Bitget credentials, sandbox writes, and account-setting changes remain external and unauthorized.
