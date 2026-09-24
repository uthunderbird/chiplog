# R14–R17: one integrated mechanism

Status: PLANNED, implementation and final evidence OPEN. Replanned 2026-09-24
at the user's request: **all remaining contracts → tests → implementation of
one joint mechanism → detailed verification and extensions**. No retrospective.

This is the current execution order. It supersedes the incremental implementation
order in R14-R17-COMPLETION.md and R14-R17-COORDINATION.md, not the requirements in
IMPLEMENTATION-ROADMAP.md or project-architecture/NORMATIVE.md. The completion
ledger retains the requirement-to-evidence map. Its old baseline/progress entries
are historical, not a description of the current tree.

## Objective and completion boundary

Build one executable path through authenticated ingress, admission, ordinary or
scheduled Run, sealed calls, acceptance/cancellation, effects, recovery,
continuation/completion and exact delivery. Reuse the existing broker journal,
sole writer, isolated owners and registered versioned histories. A common
composition does not merge owners or make DTOs into authority.

The first integrated result must implement every mechanism family in the map below,
with a positive witness and a reached failure/recovery witness through the same
assembly. It is not a happy-path demo. Full cross-products, additional payloads and
all mandated counterhistories follow in the detail phase. That phase remains part
of R14–R17 completion: a green common mechanism is not permission to close them.
Any missing mandatory operation, permanently unsupported branch or placeholder
HOLD prevents completion of the common mechanism itself. A legitimate state-based
HOLD is valid only with its rejection witness and the permitted progress route.

R18's complete calendar/workflow orchestration, R19's wider scenario programme,
R20 release entitlement and live provider traffic are outside this replan. Existing
R14–R17 T01/T03 and canonical-loop obligations remain inside; R18 is not a reason
to defer their owner/broker/driver joins. Providers and channels use hermetic
transports at the actual adapter boundaries.

## Inspected starting point and ownership

Baseline: master `5c46409fc00a35dfed759bc7872d546da7cec81b`, also origin/master at
inspection. Commands: `git status --short --branch`, `git log -3 --oneline`,
`git worktree list --porcelain`. Main was clean before these planning edits.
Root owns this plan, common contracts, assembly, integration and final evidence.
Other worktrees are preserved; their branches or reports are not automatically
admitted as verified implementation. Re-inventory dirty paths and assign ownership
before importing anything from them.

Reusable bounded implementation, not full milestone evidence:

- `R14ExecutionRuntime` and `ExecutionDispatchRuntime` implement executable
  initialization/fanout, original ingress provenance, authenticated call acceptance,
  atomic accepted/execution/effect records and existing dispatch/outcome history.
  See R14-CALL-ACCEPTANCE-RUNTIME.md and the corresponding composition modules.
  The actor and worker path is still bounded to registered hermetic nonscheduler work.
- R16's durable original-stream outcomes/reconciliation are reusable; those effect
  outcomes do not yet establish loop reduction, terminal accounting or continuation.
  See R16-OUTCOME-COMPLETION.md and R16-OUTCOME-INTEGRATION.md.
- R15 has configuration and bounded interval publication; scheduled execution,
  takeover and recovery are not established by those publications. R17 has retained
  CLI custody and actual peer authentication, not universal admission or delivery.
  See R15-GENESIS-RUNTIME.md, R15-TICK-CONTRACT.md, R17-AUTHENTICATED-CUSTODY.md.
- R15SchedulerRuntime and R17IngressRuntime are separate R14PlanningRuntime
  descendants. Their existence, or a combined owner manifest, does not prove a
  joint runtime. Existing CLI/chat entrypoints must be inspected and explicitly
  connected; historical fixture profiles keep their old interpretation.
  In particular, execution currently calls `R13Workspace.ingest()` rather than
  consuming R17's selected admitted inbox. Join that exact inbox/origin instead of
  independently recreating prompt ingress. Scheduler exclusions in the runtime,
  execution fanout, transition verifier and owner fanout preparation all need real
  common lease checks, not removal of rejection clauses. `cli.py` and `codex_chat.py`
  retain R8/R13 entrypoints; Telegram normalization is not an application driver.
  `verification/r8_surface.py` pins old CLI routing, so change its current-surface
  expectations deliberately while retaining historical compatibility witnesses.
- The in-flight cancellation-contract commit completed as
  `41e188691488e9cb7c807a69d00485f45218fdcf` in the clean
  `chiplog-r14-execution-cancellation` worktree. Its agent-owned changes are
  R14-EXECUTION-CANCELLATION.md, the execution cancellation DTO/port, consumer tests,
  shared fixture and reviewed source-catalog entry. Its already-running full hook
  passed; inspect exact commit/tree before import. It contributes a
  contract, not runtime cancellation or a separate completion milestone.

## Phase C — freeze the whole remaining contract graph

The concrete symbol inventory and remaining gaps are tracked in
[Integrated contracts](R14-R17-INTEGRATED-CONTRACTS.md); that graph remains OPEN
until every required boundary has a callable public contract and consumer evidence.

Before new business logic, reconcile existing contracts against every ledger row.
Retain valid contracts; version only genuine wire/semantic changes. Freeze together:

| Boundary | Decision owner and required contract content |
|---|---|
| Ingress → admitted input | Broker/trust: actual source witness, receipt/loss slot, immutable raw custody, admission/FIFO/reserves/deadline, parser lineage, page disposition/cursor and exact origin |
| Schedule → worker/Run | Agent loop: schedule/policy/bound heads, complete interval/resolution members, three dispositions, stable lineage versus physical epoch, lease/takeover/rollover and current worker proof |
| Sealed call → accepted/cancelled | Agent loop + effects: complete ordered inventory, exact initialized/current Run heads, competing branch CAS, complete owner batches, original mandate and cancellation receipt |
| Effect/evidence → recovery result | Effects owns intent, dispatch binding, attempts, outcome/reconciliation/compensation; original resolver owns closure and versioned semantic reduction. Consumers bind exact witness/closure/reduction heads |
| Frontier → continued/successor Run | Agent loop: independent complete enumeration, immutable baseline, total classification, original obligations, terminal versus read-only pending, stable budget and successor bindings |
| Worker/model attempt → publication | Agent loop/broker: authoritative writer applicability, all lease/epoch variants, five model-attempt states, durable emission, no-exposure proof, fresh selector, post-terminal work |
| Completion → delivery | Agent loop owns accepted deterministic render and exact origin/selected recipient; effects owns delivery attempt/outcome/reconciliation; broker revalidates endpoint/disclosure at the last boundary |
| Shared assembly → observers | Composition: registered public entrypoints and operation/version routing; observers expose selected journal, materialized bytes, lineage and external emission without injecting successful owner results |

The required ingress universe is Telegram push, Telegram poll, CLI, provider
callback, provider poll, reconciliation and tool-result ingress. Compare actual
discovery with this mandatory set as well as the registry; a missing adapter is
unfinished work, not an N/A row. Dashboard ingress remains empty.

For each boundary record canonical input/output bytes, authority source, versions,
identity/idempotency scope, current versus historical validation, all failure
variants and who may fill which fields. Use the existing typed rejection/integrity/
uncertainty distinctions. Never regenerate selected owner output during replay.

Exit C: every remaining required transition is expressible through public surfaces;
independent consumers roundtrip/compile, reject wrong variants and preserve old
schema bytes; the complete graph and source-catalog delta receive cold review.
Small consumer shape checks belong to contract validation. They do not replace
the behavioral test phase. If a later test exposes an insufficient contract,
return to C for that change before implementing it.

## Phase T — executable acceptance suite before implementation

Write the shared scenario driver, independent durable/transport observers, fault
controls and cases before completing their runtime behavior. Use public owner and
composition ports, real isolated owner exchange and real journal/SQL publication;
only external transport/model/time inputs are controlled. Race witnesses use
independent workers/processes where a local asyncio lock could hide contention.

Keep test placement under existing categories (`composition`, `platform`, `cli`,
`capabilities`, `verification`); shared builders go in `tests/support`, not test
module imports. Preserve historical nodeids and verifier selectors or record an
explicit mapping to replacements. Do not move existing tests merely for this plan.

Every new case has a stable case ID, ledger row, initial state, public commands,
fault/race cut, expected durable state and forbidden external observation. Retain
the first RED result and its specific missing behavior. Collection/import errors,
environment failure or a fabricated success response are not a valid RED witness.
Existing supported behavior must remain green.

If a semantic test checkpoint must be committed before implementation, an explicit
per-case `xfail(strict=True, raises=...)` may describe only a newly introduced,
identified unimplemented boundary. The stub must have a narrow typed failure;
generic AssertionError/Exception or blanket module marks are not acceptable.
Run the new cases with `--runxfail` as well and retain actual RED diagnostics.
XPASS forces removal of the mark. This is an OPEN test specification, never a
passing implementation result. Do not change hooks, hide cases by deselection,
mark old regressions xfail or infer readiness from the ordinary gate's exit code.

Exit T: contracts frozen, every mechanism row has runnable positive and adversarial
histories, observers demonstrably reject planted violations, REDs attributed to
missing behavior rather than broken test infrastructure. Root reviews the test
oracle separately from the implementation. Phase T can contain baseline-green
cases for mechanisms already implemented.

## Phase I — implement the common mechanism in one assembly

Extend the existing executable/dispatch assembly with scheduler, ingress/admission,
recovery/continuation and delivery routes. Do not select a succession of milestone
profiles in one test to simulate a common runtime. Register the final owner topology,
resources, schemas, worker paths, source closure and canonical CLI/Telegram driver
bindings together. Authentication remains channel-specific; the application loop
and committed projection are shared.

Implementation follows internal dependencies, not independently released R numbers:
source admission and lineage/fences → call/effect lifecycle and resolver joins →
frontier/continuation/successor and scheduled execution → completion/delivery.
These are work packages inside one result, not permission to postpone any mechanism
in the table. Keep original identity and authority across every join and restart.
Preparation occurs outside the writer transaction; final admission rechecks the
complete source cut inside it, with no provider or owner IPC under the writer lock.

Use a root-owned integration branch from the inspected baseline plus the verified
in-flight contract. Other branches are sources for reviewed reuse, not separate
milestone completion lanes. Any delegated work has disjoint owned paths, frozen
interfaces and root inspection before integration. Root serializes assembly,
source catalogs, registries and evidence selector changes.

Exit I (COMMON-MECHANISM READY): all rows below execute through that assembly;
all Phase-T mechanism cases pass without skip/xfail/deselection; crash/reopen
preserves original identities and outcomes; common-loop parity is observed; writer
and ingress inventories match actual paths bidirectionally. Frozen-tree preflight,
normal tests, relevant verifiers, lint/types and cold critical review pass. This
does not assert the expanded Phase-D matrix or full milestone completion.

## Coverage map: common mechanism first, detail expansion second

Every row requires representative positive and reached negative/recovery evidence
in I. D completes the full normative case matrix, including additional orderings,
boundary values and channel/provider variants. A mechanism listed here cannot be
implemented for the first time only in D.

| ID / completion-ledger rows | Required mechanism and first joint witnesses | Detail-phase expansion |
|---|---|---|
| J1 — R17.1, R17.2 admission/quarantine | Every actual source class enters token/custody/loss + admission; real page/quarantine route; crash before/after release; FIFO/rebase/drain makes progress | Every source's authentication/replay mutants, malformed payload classes, reserve and page N/N+1 combinations |
| J2 — R15.1/2/3 | Automatic interval service, all disposition operations, overflow/resolution, leased scheduled Run, takeover and physical rollover; stale writer rejected | All competing CAS orders, empty/one/N manifests, amendment/resolution races, expiry and exhaustion variants |
| J3 — R14.1, R16 atomic intent | Sealed executable calls; acceptance and pre-accept cancellation in both orders; no partial owner batch or pre-accept send; accepted unknown never becomes NOT_EXECUTED | Full fanout cardinality/byte mutations, mixed call sets, historical replay/corruption combinations |
| J4 — R16 dispatch/ambiguity/compensation + R14.2 | Every registered transition implemented; original-stream outcome and resolver reduction close exact evidence; lost response/reopen produces no blind retry; separately authorized compensation and proof-gated permitted retry | Every dispatch crash edge/version mutation, compatible/rival late evidence orders, all-child proof omissions; full T01/T03 required observations |
| J5 — R14.3 frontier/resume/read-only | Complete frontier/baseline, same/hold/fault/successor, both continuation consumers, read-only pending with stable budget across restart/takeover/successor | All membership/branch mutants, N/N+1 budget, current-head races and successor companion omissions |
| J6 — R14.4/5 | All writer applicability families, post-terminal original-stream work and independent lease/rollover; five-state model attempt and proof-gated replacement with fresh selector | Full bidirectional A99 discovery after all writers exist, all stale clock/epoch/lease cuts, late old-generation response and no-exposure proof mutants |
| J7 — R17.3/4 + cross-stage | Atomic completion/delivery, both exact recipient modes, final disclosure check, deterministic committed rendering, ambiguity without model rerun; CLI/Telegram same loop with distinct authentication | All endpoint/narrowing/recipient dedup mutations, delivery/recovery interleavings and final complete ingress inventory |

Connect these witnesses into shared histories: J1→J2/J3→J4→J5→J7, and crash/takeover
with J6 across the same original lineage. Do not force mutually exclusive outcomes
into one fictional execution: accepted/cancelled, confirmed/unknown and
same/successor are separate histories on the same assembly. Existing P01–P08 in
R14-R17-TRANSCRIPT-PLAN.md remain scenario vocabulary, not sequential implementation
releases or evidence by themselves.

## Guarantee-to-observer map

Before C materializes new wires, expand these rows into exact symbols and fixture
IDs in its contract artifact. Evidence below is PLANNED, not a runtime result.
Apply omission, addition/unknown, substitution/alias, duplicate/reorder, stale/race,
N/N+1 and logical/physical identity mutations to each applicable boundary; justify
any N/A per boundary rather than exempting a whole family.

| Claim | Owned data/decision | Independent observable | Forbidden substitute | Boundary fixture / planned evidence |
|---|---|---|---|---|
| Authenticated exact ingress | Trust source identity; broker token/raw custody | Real source handoff/release observer plus retained raw bytes | caller source label, body digest without bytes | J1 forged witness, destructive-read crash, partial page; V4/V6/V7 |
| Complete atomic publication | Exact owner-produced batch; broker selection/materialization | Journal selection versus independently read SQL members | reconstructed owner output, count-only comparison | J2/J3/J5 omit/add/reorder, selected-before-SQL crash; V5/V6 |
| Same lineage and current fence | Original call/mandate/obligation; worker lease and epoch | Selected original bytes before/after takeover and fresh-process reopen | renewed mandate, new call ID, local-lock-only race | J2/J5/J6 stale submit, successor and rollover; V2/V5/V6 |
| Safe recovery permits progress | Resolver closure/reduction; loop frontier and retry counter | Both continuation consumers and external attempt log | newest raw evidence, timeout as no-exposure, permanent HOLD | J4/J5/J6 late rival evidence, exhaustion, allowed replacement; V5/V6/V9 |
| Bounded admission and work | Broker reserves/FIFO/deadlines; owner budgets/manifests | Full queue and bound snapshots, progress and referential closure | numeric cap without ordering or lost members | J1/J2/J5 N/N+1, blocked-prefix rebase, restart/drain; V4/V5/V6/V7 |
| Exact truthful delivery | Accepted render; original recipient; effects attempt | Emitted bytes/recipient and committed evidence projection | fresh model answer, fallback endpoint, unclosed success | J7 endpoint change, lost ack, disclosure narrowing; V4/V6/V7 |
| Complete runtime surface | Registered commands/handlers/writers and source adapters | Independent discovery compared in both directions | hand-authored registry compared only with itself | J1/J6/J7 omitted/extra live handler; final source-bound verification |

## Phase D — detail coverage, extensions and final closure

Expand cases by ledger row after I, repair defects through the same C→T→I order
when contracts change, and rerun affected joint histories. No parallel replacement
runtime or private reduced acceptance criterion. Recompute writer/ingress discovery
after every added path. Bind current verifier outputs to exact source tree, profile,
fixture versions, command, observed outcome and artifact path in the completion
ledger. `pending` is replaced only after execution and root inspection.

Exit D (R14–R17 COMPLETE): every ledger requirement and roadmap common DoD has
current evidence, every named dispatch transition and required counterhistory is
covered, canonical verifiers pass on frozen bytes, no unimplemented mandatory
branch remains, and the integrated tree passes its normal integrity/publication
checks. Cold same-model review lowers shared-context bias; it is not external proof.

## Checkpoints, file budget and verification cost

The semantic delivery unit is the common mechanism. Git checkpoints may be smaller
and must have meaningful boundaries (contracts, scenario specifications, cohesive
implementation modules). They are not separate milestone completions. Existing
`guard-edit.sh` counts dirty paths: target below 35 including tests/docs/catalogs/
review changes; hard edit guard at 40. This is not a 40-file limit on the final
integrated diff. Estimate each checkpoint before editing; do not pack unrelated
code into files, omit required tests or weaken the guard to fit it.

For intermediate feature-branch commits, run only the new tests, without full
regression or red-team review (user instruction, 2026-09-24). Use explicit
`CHIPLOG_COMMIT_MODE=checkpoint` and a JSON array of pytest selectors in
`CHIPLOG_CHECKPOINT_TESTS`. Markdown-only checkpoints may have no new tests.
Cheap structural checks remain; no cadence retro is required for a checkpoint.
The default `full` mode remains required when preparing the integrated change
for main/master. A passing checkpoint does not establish merge readiness.

At integration/merge preparation, run
`sh .harness/scripts/test.sh --preflight` before broad checks and again after
entrypoint/contract/fixture-driver changes. Serialize full/runtime gates to avoid
contention with authority deadlines. Review exact source inventory and hashes in
`inert_shared/r8-implementation-v1.json` and affected manifests/verification selectors
before repinning; record why new files belong. Freeze source, tests and index during
a full gate. Preserve delegated child-test coverage evidence.

Before each authorized checkpoint: inspect the concrete diff, run its new tests,
recap/ack applicable handoff decisions and use the checkpoint hook mode. Before
preparing a merge to main/master: run full regression/integrity checks and cold
critical review of the entire integrated diff, not merely its last commit. No repeated
permission request where the user's existing authorization applies. Merge/push
only checked objects; verify parent/tree and remote identity. No retro is introduced
by this plan. Future status reports distinguish CONTRACTS READY, TESTS RED,
COMMON-MECHANISM READY and R14–R17 COMPLETE.

## Replanning evidence and remaining uncertainty

Swarm: E grounded existing runtime/requirements and the actual dirty-path guard;
A compared contract, concurrency, integration, test and interaction-failure lenses;
E checked separate runtime/CLI surfaces and the test-gate constraints. The rejected
route was rebuilding a new physical framework; the selected route reuses existing
mechanics and integrates every owner before milestone-specific expansion.

The initial cold plan review found no critical blocker; it required explicit
inherited-change ownership and retention of full cancellation/integration evidence.
The cold final-artifact review read this plan, all three updated pointers and the
pytest gate and found zero critical/blocking defects. Its coverage clarification
is incorporated above: required ingress classes cannot disappear merely because
their adapters are missing. Phase C must still inspect exact contract deltas.
This plan changes no runtime and supplies no new
behavioral PASS. Exact contract deltas, path budgets per checkpoint and expanded
case IDs are Phase-C/T outputs, not silently presumed to exist. They do not change
the required mechanism boundary or defer a required capability beyond I.
