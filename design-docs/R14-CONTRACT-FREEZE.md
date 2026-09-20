# R14 — recovery frontier and call accounting

Status: IMPLEMENTATION IN PROGRESS; shared identity/fence contracts committed in
`0168d61`. Full convergence and deployment remain HOLD.

## Context, plan and assumptions

Read IMPLEMENTATION-ROADMAP R14–R17; project-architecture/NORMATIVE persistence,
Run state machine, Turn/model attempts, recovery and A15/A38–A59/A99/A108;
HEXAGONAL-CODE-LAYOUT owner boundaries; R13-CONTRACT-FREEZE; existing loop contracts,
loop_sqlite, r13_runtime and platform/_sqlite. The coordination plan and cold review
are in R14-R17-COORDINATION.md.

Prepare typed boundaries before logic. First freeze agent-loop-owned lineage and
worker-fence values needed by R15. Then prepare call-frontier/accounting, recovery
reduction and resume/successor owner ports, validating public consumer shapes.
Implement R14.1 through R14.5 as semantic slices, preserving the full milestone.
An initial R14 integration baseline enables downstream work but does not close A99.
Final convergence requires actual R15–R17 writer and ingress paths.

Assumptions: current R13 five-state model attempt identity is reused; scheduler policy
shares agent_loop ownership; effects and ingress retain independent original streams;
cross-owner consumers use their own ports and mechanical bridges, not foreign executable
contracts. No new writer, database authority, model rerun after possible exposure or real
provider admission is permitted. Original master dirt is user-owned and absent here.

Initial local preparation inventory: this document, coordination document, shared
recovery_contracts.py, consumer test and handoff. Reserve room for complete tests,
source catalogs and retro within 35 total dirty paths (hard40). Full implementation
requires multiple semantic slices; a checked slice needs explicit commit approval before
another slice would exhaust the union limit. Do not compress unrelated logic to evade it.

## Shared contract v1 candidate

`agent_loop.recovery_contracts` owns strict immutable scalar identity/fence DTOs.
Tagged JSON v1 encodes `kind` first at each object, remaining object keys lexically,
ordered arrays unchanged. Frontier manifests will use explicit registry-ordered arrays;
generic sorted JSON is not their semantic order. The physical root is distinct from the
stable tagged execution lineage. Work epochs are distinct from physical-root epochs.

Trusted-clock proof references name an independently broker-issued immutable proof,
its version/digest, exact fence digest and command/submission scope. Constructing a DTO
is not authentication. Proof scope uses the command payload domain excluding proof;
the final command fingerprint includes proof. Root-owned broker verification is pending.

Applicability is closed over non-scheduler, live execution lease, pre-root decision,
post-terminal work and both rollover families. Pre-root first publication, exact-prefix
materialization and complete replay are distinct branches. No freely supplied boolean
or caller classification grants an authority lane. Writer registry resolves applicability.

Only shared files are distributed by hash-checked patch. The physical command/appender,
callable admission guards and unrestricted operation strings remain broker-private.

## Requirement-to-evidence map

All rows are planned HOLD; a path is not proof until its reached history is inspected.

| Requirement | Planned public scenario / observable | Evidence target |
|---|---|---|
| R14.1 sealed fan-out | response and every ordered initialized call appear atomically; partial/extra/duplicate/reorder/oversize publish nothing | tests/composition/test_recovery_accounting.py |
| Exact acceptance branches | initialized-head CAS selects consequential intents, read-only acceptance or pre-accept cancel; cancellation after accepted unknown cannot become never-executed | tests/composition/test_recovery_accounting.py |
| Terminal accounting vs continuation | open exact recovery obligation satisfies accounting but bars Turn/completion; pending bars both; full manifests inspect every earlier response | tests/composition/test_recovery_accounting.py |
| R14.2 evidence/closure protocol | authenticated evidence may commit alone; only original registered resolver closes and publishes immutable recovered outcome; crash gap stays held | tests/composition/test_recovery_reduction.py |
| SemanticEvidenceReduction CAS | both consumers bind accepted witness+closure+resolver batch and current reduction identity/head; compatible/rival evidence in both serial orders preserves committed history and gates future use | tests/composition/test_recovery_reduction.py |
| R14.3 frontier closure | independent snapshot enumeration finds all declared and actual families/subjects, canonical absent/N/A/present, exact terminal or pending branch | tests/composition/test_recovery_frontier.py |
| Immutable baseline | objective/work, prompt/tools/schema, authority/policy, recipient/effect/no-retry and obligation predicates frozen at suspension | tests/composition/test_recovery_frontier.py |
| Total classification | fault > hold > valid binding-change successor > same; missing/unresolved/unknown cannot allocate successor | tests/composition/test_recovery_resume.py |
| Same-Run activation | transaction-local re-enumeration and full byte comparison; changed closure/registry/Run/head writes nothing; exact replay returns prior activation | tests/composition/test_recovery_resume.py |
| Atomic successor | predecessor+edge+initialization+new Run+lineage current Run+epoch observation all-or-none; exact bound fence; no substitute refreshed values | tests/composition/test_recovery_resume.py |
| Original obligations | successor references original stream or exact already-closed head, never copies/transfers/reparents; no-retry references survive | tests/composition/test_recovery_resume.py |
| Read-only lineage budget | one original call/counter/reducer through restart/resume/successor/takeover; full ordinal manifest; pending→pending/terminal atomic and no branchless state | tests/composition/test_recovery_readonly.py |
| R14.4 A99 writer registry | independently enumerate executable command/handler/DTO/record-kind/applicability at build/startup/runtime; omission/addition/alias/raw/wrong lane fails | tests/architecture/test_worker_commit_registry.py |
| Worker fence | Run/current lineage+physical selector+epoch+lease head/holder/session/id/generation/expiry/clock transaction-local; renewal stales unchanged-generation command | tests/composition/test_recovery_worker_fences.py |
| Exact decision replay | authenticate entire commitment before replay; first/prefix/complete distinct, no partial/rival/non-prefix state; old live lease not needed for exact historical replay | tests/composition/test_recovery_worker_fences.py |
| Post-terminal work | terminal accounting creates stable work+epoch+selector/genesis per open obligation; independent lease, original resolver first then work closure; no history rewrite | tests/composition/test_post_terminal_recovery.py |
| Work lease exhaustion | uint64 acquire/renew/takeover, max hold, authenticated atomic rollover, one selected epoch, stale old work fails and no new obligation | tests/composition/test_post_terminal_recovery.py |
| R14.5 five-state attempts | durable emission before bytes, exact selected response; lost response no retry, no-exposure proof fresh generation same lineage, stale generation rejected | tests/composition/test_model_attempt_recovery.py |
| Final convergence | R15 rollover/successor, R16 effect+reconciliation, R17 delivery/ingress worker lanes in actual registry and full V5/V6 reached counterhistories | tests/verification/test_recovery_convergence.py |

## Guarantee preflight

| Claim | Owned decision/data | Independent observable | Forbidden substitute | Boundary fixture | Evidence |
|---|---|---|---|---|---|
| Exact complete frontier | registry + authoritative scoped rows + branch manifests | second independent subject enumeration and byte encoder | caller subset/digest-only/current projection | omit/add/duplicate/reorder/hybrid/absent-vs-N/A/corrupt byte | frontier scenarios above |
| Accounted terminal | sealed manifest + acceptance-consistent terminal/result/obligation | atomic stored manifest equality over all responses | worker exit/accepted boolean/attempt outcome alone | pre/post-accept cancel and lost acknowledgement | accounting scenarios |
| Consumable recovery | original resolver closure/outcome + accepted witness + reduction head | both consumers transaction-local join | latest raw append or evidence-present treated as closure | compatible/rival evidence vs both consumers, two orders | reduction scenarios |
| Bounded non-resettable retry | immutable original lineage + counter/current pending head | restart/successor reads same logical identity/counter | new call/Turn/Run means new budget | N/N+1, ordinal gaps/reorder, pending hybrid, open unknown | readonly scenarios |
| Same-Run/successor exactness | immutable baseline + observed full manifest + writer CAS | complete winning batch or no records | precomputed classification/recomputed substitute fields | both CAS orders, changed head between read/use | resume scenarios |
| Authenticated worker fence | broker proof issuance registry + exact owner lineage/lease | reached old worker writes no new records | payload identity/clock assertion/same generation | renewal, expiry, takeover, epoch/session substitution | worker-fence scenarios |
| One current work epoch | original work subject + protected selector + journal rollover decision | rebuild/dedup selects one epoch, same obligation | reset/wrap/new subject/physical-root authority | uint64 max, rival rollover, pre/post-decision crash | post-terminal scenarios |
| Possible emission never retries | existing model-attempt lineage + emission/proof selector | external-byte observer and selected durable generation | timeout means no exposure/new Turn retry | before/after first byte, lost response, late stale generation | attempt scenarios |

Mutation coverage applies across all guarantees: omission; addition/unknown; substitution/
alias; duplicate/reorder; stale/race; N/N+1; logical/physical identity mismatch. None is N/A
for this milestone. Numeric bounds also require progress, order and referential closure.

## Existing integration hazards and final validation

R13Runtime.decide_publication currently admits only workspace.policy/conversation.accept;
new semantic commands need closed owner validation, not an arbitrary operation allow-list.
Physical replay checks fingerprint before admission_guard; the new broker must authenticate
the exact durable decision and commitment before selecting historical replay. Startup
materialization must carry decision-bound deletion fence/version instead of ambient defaults.

Inspect r8-implementation-v1.json and architecture/r8_implementation.py before changing
audited bytes; preserve historical R8/R12 component drivers and production/eval identity.
Run preflight after contracts/entrypoints, targeted boundary tests, lint/types, full tests
and relevant verification on stable source bytes. Do not repin evidence to silence failure.
Baseline preflight was run with `sh .harness/scripts/test.sh --preflight` and exited zero.

## Outstanding

Pure accounting and continuation joins are implemented in recovery_domain.py; they
do not yet prove transaction-local frontier classification or publication. The broker
publication coordinator now uses IndependentOwnerDecisionJournal in its real SQLite
tests instead of a test-only journal serializer. The adapter reads the complete
authenticated stream, validates canonical closed envelopes and owner byte digests,
rejects competing selections while a selected batch remains unmaterialized, and
binds exact materialization markers. Cold implementation review found and verified
repairs for missing digest validation and a competing-decision crash window.

`uv run pytest -q tests/platform/test_owner_publications.py`: 20 passed, including
decision-before-SQLite rollback followed by a rival command (HOLD, no second decision),
lost-ack reconstruction, concurrent exact replay, malformed authenticated rows,
independent append interference, binary reopen, every atomic publication variant
and monotone successor selection.
`uv run mypy src/chiplog/platform/owner_decision_journal.py
src/chiplog/platform/owner_publications.py tests/platform/test_owner_publications.py`:
PASS. These tests retain an explicit semantic-authority fixture; they do not prove
canonical invocation issuance, owner routing or a complete runtime writer registry.

Full current-branch regression after reviewing and pinning the changed source
catalog: `uv run pytest -q` — 550 passed, 411 outer Stage0 cases delegated with
confirmed child coverage (`.artifacts/r14/pytest-full.log`).
`sh .harness/scripts/lint.sh` — PASS. These are current-implementation regression
results, not completion evidence for the unimplemented milestone scenarios below.
After that full run, the closed operation names for scheduler genesis and schedule/
policy amendments were added from the reviewed R15 configuration plan. The affected
publication-contract, journal/coordinator and current-source tests were rerun:
`uv run pytest -q tests/platform/test_owner_publication_contracts.py
tests/platform/test_owner_publications.py tests/architecture/test_r8_current_surfaces.py`
— 27 passed. Operation names alone do not register executable runtime handlers.

The version4 closed preparation assembly now realizes agent-loop transition plus
scheduler preparation and a separate effects process. Real IPC tests exercise
successful scheduler lease and plan-effect proposals, exact canonical result bytes,
companion manifest preservation, stale sessions, unknown schemas/operations and
post-call loaded-module closure. Attestation mutants add a cross-owner or unknown
process module and are rejected. Versions1–3 retain their existing graphs.
The assembly received an independent scoped source review with zero P0/P1;
this reviews preparation and isolation, not publication authority or final deployment.
Reviewed streaming scheduler and current-send-fence changes were transferred with
before/after hashes in `.artifacts/r14/streaming-transfer.json` and
`.artifacts/r14/effects-fence-transfer.json`. The earlier full-regression result
above predates these assembly changes; new source inventory pins and final broad
verification remain outstanding.

Subsequent assembly checkpoint: the reviewed source inventory was updated with
exact changed-file hashes and scope/evidence recorded in
`.artifacts/r14/assembly-boundary-review.json`. `uv run pytest -q` completed with
584 passed and 413 outer Stage0 cases delegated with confirmed child coverage
(`.artifacts/r14/pytest-assembly-full.log`). Two copied scheduler files were then
formatted with AST and comment equality checked, and `sh .harness/scripts/lint.sh`
passed (263 source files). The full run predates the later planning prepare-only
refactor; its affected runtime checks are recorded separately, not implied by 584.

The independent owner journal now exposes a complete immutable authenticated
snapshot including pending selected decisions. The physical commitment helper
uses the caller's existing transaction, exact schema and main-qualified tables;
it rejects unverifiable input with contextual typed errors. Real WAL publication,
TEMP table/view shadows, selected-decision crash recovery and malformed journal
tail tests exercise these mechanics. Neither helper proves an atomic cross-journal
admission cut or grants command authority. The planning preparation accessor
returns exact isolated-owner proposal bytes without publishing Plan; legacy create
delegates to it and still owns its original publication path.
After that refactor, `uv run pytest tests/composition/test_r7_planning_runtime.py
tests/composition/test_loop_adoption.py tests/composition/test_production_loop.py
tests/platform/test_authority_snapshot_commitment.py
tests/platform/test_owner_publications.py -q` — 49 passed.
An independent actual review of journal/commitment snapshot APIs found zero P0/P1
and reran 35 targeted checks (`.artifacts/r14/authenticated-cut-review.md`).
`sh .harness/scripts/lint.sh` and the worktree test-layout check pass after the refactor.

Still outstanding: full call-frontier/command implementation, evidence reduction CAS,
resume/successor and model-attempt recovery; exact-prefix materialization recovery;
canonical broker integration, all reached milestone counterhistories and cross-milestone
convergence; final cold result review; R14 retro and explicitly authorized commits.
No milestone completion claim is made.

The additive `R14PlanningRuntime` now binds selected owner decisions to the existing
physical database identity, independent authority commitment, and durable read ledger.
All three journal families block fresh selection until their selected publication is
materialized. Startup enumerates competing pending families before replay, reproduces
selected bytes with an exact resulting-commitment guard, and updates the anchor and
read ledger before marking materialization. Recovery does not call a live semantic owner.

`uv run pytest -q tests/composition/test_owner_runtime_recovery.py --tb=short`:
8 passed. Reached histories cover bootstrap/reopen; crashes after selection, physical
commit, anchor update, and read-ledger update; reciprocal pending legacy and actual
planning-gate barriers; corrupted physical bytes with a contextual chained integrity
error; and historical marker replay after a later publication without rewinding the
anchor. Owner records in these lifecycle tests deliberately use inert semantic fixtures;
the planning-gate case uses the actual planning producer and an independent test
entitlement. Neither establishes fresh owner invocation issuance or deployment authority.
The runtime received an independent source review with zero confirmed P0/P1 before
source inventory admission; exact reviewed pins and evidence limits are recorded in
`.artifacts/r14/delivery-runtime-boundary-review.json`. A subsequent competing-journal
mutant bypasses composition selection to seed two pending families and verifies that
startup rejects them before changing physical state; the focused legacy selector
passes both its normal and rival cases. The complete command registry, actual
scheduler/effects/ingress publishers, canonical
entrypoint wiring, and all remaining milestone scenarios above remain outstanding.

The staged review found a parser-selection defect: generic transition validation
could accept legacy completion bytes under a captured delivery generator. The
original accepted transition is retained as a regression fixture. Both continuation
and legacy completion now parse the captured bytes through the artifact's registered
generator and exact schema. The pure legacy parser moved into the capability; its
AST and all four generated tool schemas remain unchanged. Actual isolated IPC checks
the incompatible completion and continuation generator/schema cases, including both
singleton tool subsets. The repair's source review closed the original P1; exact
reviewed source hashes are recorded in the corresponding parser-review artifact.
