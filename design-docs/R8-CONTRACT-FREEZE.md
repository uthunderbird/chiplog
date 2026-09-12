# R8 authority tracing and operation gate

Status: implemented. Evidence is bound by the canonical Stage-0 profile; deployment eligibility remains HOLD.

## Authority and scope

Sources: `VISION.md` (2026-08-22.2), `project-architecture/NORMATIVE.md`
authority-read recorder paragraphs and operation-level deployment gate section,
`IMPLEMENTATION-ROADMAP.md` R8 and Stage-0 integration barrier, and the R7 contract freeze.
R8 adds the exclusive provenance path for authority-sensitive planning inputs and
the operation-boundary eligibility check. It does not issue READY, evaluation
authorizations or production profiles. Missing independent entitlement remains HOLD.

The current planning operation is direct-principal CREATE_INTENTION_LINE. Its
registered predicates must not acquire natural-language adoption requirements.
Proposal freshness contracts must nevertheless express the exhaustive displayed
proposal/adoption binding and reject stale adoption; later journeys cannot replace
the trace with declared dependencies. Future business journeys (calendar, scheduler,
compensation, recovery and delivery) are counterhistory boundary fixtures here, not
implementations of those later milestones.

Existing R6/R7 fixtures remain historical compatibility evidence. The canonical R8
entrypoint must independently prove its read and gate guarantees; an old runtime
pass cannot authorize exposure. Current CLI authority use and delivery require
classification and enforcement, not an implicit development exemption.

## Guarantee and evidence map

These suites exercise the implementation. Stage-0 PASS requires the complete canonical profile, not this inventory alone.

| Claim | Owned decision/data | Independent observable | Forbidden substitute | Boundary fixture | Evidence path |
| --- | --- | --- | --- | --- | --- |
| Exclusive authority provenance | Planning recorder owns ordered canonical source/value/head/version/generation/validity reads and registry inputs | Actual executed predicate inputs versus reproduced transaction-local trace; durable records after denial | Declared-only list, constructor/cache/projection/precomputed input, transitive derived input outside recorder | Omit/add/substitute/duplicate/reorder reads, inject each input class, change discovered dependency | `tests/r8/test_authority_and_surfaces.py`, runtime integration |
| Whole freshness equality | Planning owns exact proposal/display/principal/adoption/ingress/interpretation/command/result/trace/registry binding | Reproduction at publication cut, zero result on mismatch; new display and adoption needed | Subset head comparison, timestamp refresh, similar proposal, old adoption | Each bound field changed; missing/extra/unknown dependency; both race orders | `tests/r8/test_authority_and_surfaces.py` |
| Exact surface coverage | Architecture owns operation/owner/capability/handoff classification and executable reachability | Independently enumerated start/replay/fan-out/adapter/provider/channel edges versus inventory | Caller surface label, downstream assertion with independent entry, empty future registry as evidence | Missing/extra/alias/duplicate/downstream route and post-start substitution | `tests/r8/test_authority_and_surfaces.py` |
| Exact eligibility | Gate consumes independent current readiness and mode-specific entitlement bytes | Durable handoff log and independently observed isolated sink | Test green, self-issued READY, evaluation permit in production, cached permit | Wrong capability/cohort, missing/revoked/expired/superseded/unknown, live predicate/open cause | `tests/r8/test_deployment_gate.py` |
| Atomic validation and use | Broker owns generation and common order of eligibility invalidation and final durable handoff | Ordered decisions and sink observations on both sides of cut | Earlier check plus unguarded callback, non-atomic generation reread | Mutate generation/readiness/authorization/profile/cap/purpose/instrumentation/stop rule/cursor/lease before commit; reverse order reports crossed work | `tests/r8/test_deployment_gate.py` |
| Bounded exposure | Exact entitlement cap, duration, freshness, instrumentation and stop rules | Atomic used-cap count, trusted clock and handoff sequence | Reset counter, replay allocating fresh capacity, unknown clock | N/N+1, identical replay/changed reuse, expiry equality, concurrent final slot | `tests/r8/test_deployment_gate.py` |
| Offline non-reachability | Composition owns attested synthetic sink graph and disjoint entitlement namespace | Reachability inventory and external canaries; same gate logic with isolated sink | Boolean offline flag, real adapter under fake name, synthetic evidence as production entitlement | Real recipient/provider/channel, human authority/disclosure, dynamic leaf replacement | `tests/r8/test_authority_and_surfaces.py` |

All mutation families apply: omission, addition/unknown, substitution/alias,
duplicate/reorder, stale/race, N/N+1, and logical/physical identity mismatch.
N/N+1 applies to caps and trace bounds; order and complete dependency closure must
remain proved, so truncation never creates a valid trace. Authentication uses the
existing broker peer/session and independently supplied entitlement authority,
never a claimed principal/signer in request bytes. Ownership permits downstream
transport framing only; canonical authority decisions cannot be reconstructed.

## Requirement-to-evidence map

- A06/A30/A33/A36: typed recorder and exhaustive freshness contracts, direct-row
  preservation, reached dependency mutants and transactional reproduction.
- A24: one shared verified frontier for authority inputs; lagging projection
  injection rejects. Full workspace barrier remains owned by R12.
- A12/A60/A61: default HOLD, exact request/result unions, complete current
  eligibility at actual last boundary; no entitlement issuance.
- A62: offline structural non-reachability and eval/production separation.
- V0/V10: exact current surface set, unknown/downstream bypass rejection.
- Counterhistories: calendar intent, provider dispatch, Telegram and CLI delivery,
  scheduler-triggered effect, compensation, recovery retransmission and newly
  registered adapter, each with positive isolated witness and denied handoff.
- Stage-0 promotion: current A01–A108 source/registry equality, closed R7
  predecessor/successor ledger, successor integration evidence, R1–R8 canonical
  profile and retained default HOLD for cohort-visible surfaces.
- Repository checks: `uv run pytest`, `uv run ruff check src tests`, `uv run mypy`,
  `sh .harness/scripts/test.sh`; cold result audit before evidence promotion.

## Contracts before logic

Planning owns `capabilities/planning/r8_boundary.py`: read kinds, immutable trace
rows and complete proposal freshness binding. Broker/platform owns
`platform/deployment_gate.py`: closed immutable gate request, independent current
entitlement view, outcome and application port. No shared executable policy crosses
owner IPC. Consumer validation imports only these public modules.

## Implemented decision boundary

The independent tenant decision journal orders authenticated entitlement changes and
exact handoffs by predecessor CAS. SQLite gate state is a replay cache. The planning
cut records the full decided batch and its predecessor/resulting materialization
commitments after SQL constraints and before the fallible physical commit. Startup
recovery materializes that exact batch before admitting new work. Lost decision
acknowledgements are reconciled from authenticated history or reported indeterminate;
they cannot become a definite denial followed by a successful replay.
An identical command retrieves its authenticated existing result without another
handoff or cap allocation. Changed bindings conflict; foreign principals and any
foreign tenant-bound record identity receive no result.

The current surfaces are `planning.create` and `cli.render`. The canonical runtime
has no entitlement-provisioning adapter, so ordinary CLI operations remain HOLD.
Positive evaluation witnesses inject an independent fixture authority and isolated
sink. The source catalog binds the reviewed implementation, including transitive
helpers, to the runtime generation; verification never refreshes it automatically.
Behavioral positive controls and reached mutations remain necessary beside the pin.

The historical fast profile retains its R0-only claim. Stage 0 includes that
substrate, the current surface/offline check, the resolved compatibility ledger and
all R1–R8 slices. Its result never grants readiness, adoption or exposure. The
proposal/adoption contract is exercised as a boundary fixture; a natural-language
product journey is not implemented or claimed by R8.
