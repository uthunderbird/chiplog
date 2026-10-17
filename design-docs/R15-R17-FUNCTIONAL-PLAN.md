# R15–R17: parallel functional implementation

Status: preparation, 2026-10-17. User instruction: work on R15–R17 in parallel;
make the client scenarios work and defer unnecessary assurance and rare cases.
This replaces the old serial milestone order, not authorization, data ownership,
atomicity or no-repeat-SEND requirements. R14 functional baseline is `4576ae4`.
No R15/R16/R17 completion or deployment eligibility is claimed here.

## Plan and assumptions

1. Inspect each roadmap item and its actual native PG consumers.
2. Define the missing public boundaries and validate them from consumers before
   business logic. Existing usable contracts are reused, not re-created.
3. Implement independent components in parallel against one shared baseline;
   root alone changes common execution, Planning transaction and model guards.
4. Mount the ordinary and scheduled client scenarios below, including recovery.
   Root inspects changes and runs integration; reports are supporting evidence.

Assumptions, reread: one current native application worker per user; scheduler,
ingress and outbox components consume that runtime rather than independently
opening clients and fencing one another. PostgreSQL remains one DB per user on
one configured server. Hermetic provider/channel transports exercise real adapter
boundaries; live exposure and credentials are not required or granted. Known
unsupported authority bundles are retained without conversion.

Cold plan review required concrete client outputs, shared-file ownership and
explicit historical-order supersession. Those corrections are incorporated here.
Same-model cold review does not establish external verification.

## Client outcomes and acceptance

| Item | Required functional outcome and observation |
|---|---|
| R15 | Adopt a finite interval schedule; no due work allocates no Run; individual/coalesced/skipped due members retain stable occurrence identities. Disposition, boundary, lineage and native Run publish atomically. Overflow holds without losing its interval, then an explicit sufficient bound permits resolution. Reopen/replay allocates no duplicate Run. Lease expiry/takeover prevents stale model/effect work. |
| R16 | Authorized Plan change and its effect intent commit together; stale/denied change leaves neither half. A durable worker discovers committed sends and claimed unresolved work after reopen, emits unclaimed work once and reconciles claimed work without resend. Compensation is a distinct freshly adopted/authorized effect bound to current original evidence; original uncertainty is preserved. |
| R17 | Independently authenticated CLI/Telegram input retains exact raw bytes and source replay identity before ACK/cursor release. Duplicate input selects the same inbox/Run; changed bytes or forged witness do not admit. Both channels use the same native application driver and accepted rendering. Ambiguous delivery recovers without another model call or transfer. |

Root-owned horizontal witnesses:

- Authenticated input → durable inbox → native Run → actual accepted effect →
  lost response/reconciliation → CompleteAcceptance → selected-origin delivery →
  reopen with no repeated model/SEND.
- Adopted schedule → atomic occurrence/Run → the same execution/effect/completion
  path; a takeover before old submission prevents stale publication/transport.
- Plan/effect atomic adoption and separately authorized compensation, with exact
  replay, stale rejection and original evidence retained.

These are completion requirements, not claims that current code passes them.

## Existing implementation and missing boundaries

- R15 pure semantics: `scheduler_configuration.py`, `scheduler_domain.py`,
  `scheduler_materialization.py`, `scheduler_leases.py` under agent_loop.
  Existing `r15_*` runtime/publication is SQLite. Its legacy Run allocation is
  not the native PG Run and must not be called before a separate native create.
- R16 native custody/reducer: `composition/postgres_effects.py`; current exact
  tool adoption, send commitment, irreversible claim, receipts and original
  resolution are reusable. Native Plan/effect transaction, work discovery/pump
  and compensation are missing.
- R17 native delivery: `composition/postgres_completion.py` already owns durable
  acceptance/rendering/delivery recovery. Reuse it. `codex_cli.py` still uses
  the separate conversational AgentLoop. Existing R17 SQLite CLI custody and
  `platform/telegram_candidates.py` do not provide native authenticated ingress.

Public boundary preparation (implemented shapes; runtime evidence HOLD):

- R15 native configuration/cycle/occurrence/lease DTOs and small service port;
  use the existing `SchedulerRootReference` only for a real selected subject.
  The native V3 carrier already admits that reference. Root provides atomic
  scheduled Run initialization and current-lease checks at action boundaries.
- R16 Plan-origin and compensation-origin effect contracts with their own real
  identities, plus discovered-work and bounded worker-step results. Never invent
  an initialized tool call to satisfy a tool-only intent schema. Root owns the
  Planning preparation/transaction seam.
- R17 exact raw submission, independently issued transport witness, durable
  receipt/lookup and shared driver port. A proposed DTO never grants ACK; actual
  selected custody does. Origin comes from authenticated ingress, not body labels.

## Ownership and parallelism

| Owner | Exclusive implementation paths |
|---|---|
| Root | `postgres_execution.py`, native transitions/model start guards, shared client/driver, Planning transaction seam, schema/import registrations, integration docs/evidence |
| R15 delegate | New scheduler native component/contracts and focused scheduler tests |
| R16 delegate | Effects native component/contracts, discovery/worker/compensation tests |
| R17 delegate | Native ingress/channel adapters/contracts and focused parity/recovery tests |

No concurrent edits to root paths. A shared contract change is reviewed and
committed once; delegates start from the same source revision. No separate
Telegram delivery lifecycle, scheduler inside R16 or independent canonical loop.

## Meaningful guarantees and evidence plan

| Claim | Owned decision/data → independent observable → forbidden substitute → boundary fixture / planned evidence |
|---|---|
| Atomic scheduled creation | Agent-loop occurrence/root plus Run → one PG commit and selected records after reopen → tick then independent create → injected precommit failure; `test_postgres_scheduler.py` |
| Stable occurrence/replay | Revision-bound member identity and retained interval → same IDs/head on duplicate delivery → new clock window on replay → reopen and changed replay input; scheduler tests |
| Current lease | Selected root/lease/generation and runtime clock → stale actor produces no publication/model/provider call → global worker token alone → expiry and takeover-before-submit; scheduler execution tests |
| Atomic Plan/effect | Planning-owned proposed change plus effects-owned intent → both durable or neither → fabricated tool acceptance or independent commits → stale/denied/lost-ACK transaction; Plan/effect tests |
| No repeated SEND | Existing committed claim and provider custody → transfer journal count after worker reopen → claim replay as permission → lost response with retained unknown; outbox tests |
| Fresh compensation | Distinct intent, original evidence head and new adoption/gate → separate action while original uncertainty remains → relabel original retry as compensation → evidence changes before send; compensation tests |
| Authenticated exact ingress | Registered transport issuer/binding plus exact raw bytes → selected receipt and retained bytes before ACK → principal/source claims from message body → forged witness, changed replay bytes, lost commit ACK; ingress tests |
| Same application/delivery | Shared native driver and accepted rendered bytes → CLI/Telegram fixtures traverse it; recovery model fails if invoked → parallel chat loop/direct accepted_text printing → parity and delivery reopen; channel tests |

Mutation families retained: omission (half-publication), addition/unknown input
(unsupported submission), substitution (peer/source/recipient), duplicate/replay,
stale/race (lease/gate/head), N/N+1 (ordinary interval overflow), and identity
mismatch (occurrence/root/Run). Exhaustive cross-products are deferred; none of
these families is declared N/A merely because the initial shape test is green.

## Backlog and process boundary

Root owns deferred work; revisit on the named pressure, not automatically as a
prerequisite for the first functional completion:

- B-R15-01: uint64 exhaustion/physical epoch rollover and full competing-order
  matrices, plus host clock rollback protection across restarts; revisit for a
  real deployment requirement or observed collision/rollback. The supported
  deployment supplies a nondecreasing scheduler clock; ordinary expiry/takeover
  remains part of current acceptance.
- B-R16-01: all-child no-exposure/idempotency retry proofs and broad multi-child
  compensation permutations; revisit for a client requiring duplicate-risk retry.
  Ordinary explicit compensation remains in current acceptance.
- B-R17-01: universal seven-source custody closure, destructive-read loss slots,
  admission reserves/FIFO/rebase, full drain/quarantine lineage; revisit when an
  actual mounted source needs that behavior. Exact custody/ACK for the two current
  channels remains required.
- B-VERIFY-01: broad stress/load and global source/catalog/registry certification;
  revisit for main/master integration or demonstrated pressure. Keep fast real
  negative and recovery checks throughout.

Inherited untracked drafts, `.r14-recovery-oct7`, `.worktrees`, `.codex/config.toml`,
SQLite sidecars, old startup RED and `.DS_Store` files are prior-session/user work;
leave them untouched. New preparation owns this document, current-order notes,
contract leaves/tests and three identified legacy harness fixtures. The actual
guard thresholds are `HC_FILES_SOFT=10`, `HC_FILES_HARD=40` in thresholds.sh;
split preparation, contracts and implementation semantically before the hard
limit, counting inherited dirty paths and final evidence. No production catalog
repin is planned. Final checks read one frozen source revision.

Initial `test.sh --preflight` failed: layout and checkpoint fixtures omitted the
new PG CLI dependency; corruption reproducer used removed SQLite CLI arguments.
Root repaired the three fixtures without changing the production catalog.
Independent checks: the three public contract test modules pass (17 tests),
targeted Ruff and mypy pass; checkpoint tooling passes (25 tests); the actual PG
corruption witness passes (1 test). Commands are `uv run pytest -q -p no:timeout`
with the three new contract test paths, `uv run mypy` with their three source
paths, `sh .harness/scripts/clean-git-env.sh uv run pytest -q
tests/tooling/test_checkpoint_gate.py`, and the corruption `run.sh` with explicit
isolated `CHIPLOG_TEST_POSTGRES_DSN`. Full preflight rerun with that dedicated
DSN (`sh .harness/scripts/test.sh --preflight`) completed with exit 0.
These observations establish contract transport and fixture behavior only.
