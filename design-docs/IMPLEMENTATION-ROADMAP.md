# Chiplog implementation roadmap

## Status, authority, and interpretation

This roadmap turns the accepted implementation architecture into an executable dependency plan. It does not change product semantics or authorize exposure.

Source precedence is:

1. [`design-docs/VISION.md`](VISION.md), `VisionVersion: 2026-08-22.2`, is the product authority.
2. [`design-docs/project-architecture/NORMATIVE.md`](project-architecture/NORMATIVE.md)
   selects implementation mechanisms subordinate to the vision. A local ignored
   `grill/project-architecture/document.md` may retain restore/provenance material; it is neither a checkout dependency nor a second authority.
3. [`design-docs/HEXAGONAL-CODE-LAYOUT.md`](HEXAGONAL-CODE-LAYOUT.md) is the
   physical-layout projection. It is subordinate to the normative architecture; until R7 updates its
   predecessor-topology sections, any conflict is resolved in favor of the normative companion.
4. The Plan/Fact/Journal family and authored transcripts refine fixtures and contracts without overriding the three authorities above.

`bounded-incomplete`, `HOLD_ADOPTION`, and every readiness/release requirement remain in force. A green implementation verifier means only the claim named by that verifier. It never creates `READY`, an `EvaluationAuthorization`, or a `VisionReleaseProfile`.

## Current baseline

Implementation status at the 2026-09-12 review: R0–R8 are implemented.
R7 is the implemented successor runtime, not future compatibility work. R8 authority-read
contracts and operation gate are described in [`R8-CONTRACT-FREEZE.md`](R8-CONTRACT-FREEZE.md); their scope follows
the authority-read and operation-gate contracts in [`NORMATIVE.md`](project-architecture/NORMATIVE.md).
The current verification runner
executes R0–R8 checks, including the current surface inventory and compatibility ledger.
All deployment eligibility remains HOLD. Status here does not upgrade historical evidence.

R0–R6 are implemented under the predecessor in-process topology. The repository now has the
verification runner and registries, canonical identities/bytes, boundary manifests, SQLite
substrate, deployment-trust capability, planning/projection capabilities, their R4/R5 frozen seam,
and an authenticated CLI planning slice. Their IDs, durable record semantics, frozen contracts and
historical evidence remain valid for the claims they originally made.

They are not evidence for the later process-isolation, broker-routed DTO, runtime-generation,
authority-read, or operation-gate guarantees. R7 is the mandatory compatibility and convergence
boundary: it preserves those semantics while replacing every executable cross-owner and raw-authority
path. Until R7 and R8 pass, `stage0` remains `HOLD` even if an older R0–R6 profile was green.

The current quality commands are:

```sh
uv run pytest
uv run ruff check src tests
uv run mypy
sh .harness/scripts/test.sh
```

These remain repository hygiene gates. They are not sufficient DoD for any roadmap increment below.

## Verification contract

### Canonical profiles

Increment R0 creates one entrypoint:

```sh
uv run python -m chiplog.verification <profile>
```

The closed initial profile set is `fast | stage0 | stage1 | stage2-offline | promotion | release`. Every profile emits a versioned, machine-readable result artifact under `.artifacts/verification/` and fails closed on unknown verifier, fixture, invariant, surface, result schema, or unobserved fault injection.

Each result binds the Git tree, dependency lock, profile and verifier versions, capability/surface, invariant IDs, fixture digests, configuration and artifact digests, observed durable/external state, and one of `PASS | FAIL | INCONCLUSIVE | HOLD`. Individual results and feature vectors are preserved before aggregation.

### DoD rule for every increment

An increment is done only when all of the following are true:

1. Its named artifacts and public contracts exist; scope and non-goals are explicit.
2. The positive verifier observes the claimed durable or external outcome, not only a return value.
3. Every named negative fixture/mutant is reached and rejected for the expected reason.
4. Applicable exact-set registries contain no missing, duplicate, unknown, or orphan entry.
5. Atomic/effectful increments cover both sides of the linearization point, lost acknowledgement, identical replay, changed-fingerprint conflict, and both serial orders of relevant races.
6. The canonical profile command passes and emits replayable evidence for the current artifact identities.
7. `IMPLEMENTED`, `EVIDENCED`, `READY`, `EVALUATION_AUTHORIZED`, and `PRODUCTION_AUTHORIZED` remain separate states; the latter three default to false/`HOLD`.

### Planned verifier classes

| ID | Expected implementation and command | What it proves |
|---|---|---|
| V0 | `tests/verification/test_registry_coverage.py`; `... verification fast` | Exact-set coverage of invariant, record, surface, fixture, and verifier registries. |
| V1 | `tests/conformance/test_canonicalization.py`; `... verification stage0` | Golden canonical bytes, schema/codec/version/domain separation, mutation rejection. |
| V2 | `tests/architecture/`; `... verification stage0` | Package/export/signature ownership, import DAG, dynamic-resolution mutants, bridge and composition constraints. |
| V3 | `tests/platform/`; `... verification stage0` | SQLite/store versions, atomicity, backpressure, shutdown, genesis/binding recovery, broker fencing. |
| V4 | `tests/contracts/`; profile for the owning stage | Consumer-owned port behavior, typed failures, lifecycle/cancellation, adapter substitution. |
| V5 | `tests/conformance/`; profile for the owning stage | Closed reducers/registries, stale heads, replay/conflict, cross-owner construction and authority denial. |
| V6 | `tests/faults/`; profile for the owning stage | Reachable crash edges, lost acknowledgements, durable post-state, deterministic race histories. |
| V7 | `tests/security/`; profile for the owning stage | Tenant/principal/source substitution, secret/network canaries, confused deputy and indirect-reference rejection. |
| V8 | `tests/evaluation/test_transcript_compiler.py`; `... verification fast` | Strict transcript DSL, digests, modalities, partial-order and matcher/assertion registries. |
| V9 | `tests/evaluation/scenarios/`; `... verification stage2-offline` | Authored scenario on the exact production loop with observed trace/state/effects and hard forbids. |
| V10 | `tests/deployment_gate/`; profile for the owning stage | Exact surface inventory, default `HOLD`, last-boundary CAS races, eval/prod namespace separation. |
| V11 | `tests/external/`; explicitly authorized environment only | Real provider/channel state, identity and invocation count; mocks cannot satisfy it. |
| V12 | `tools/verify_release_evidence.py`; `... verification promotion|release` | Evidence completeness/freshness/applicability/open causes; verifies but never issues authority records. |

## Delivery graph and parallel work

```text
R0 verification substrate
├── R1 canonical identities and bytes
└── R2 boundary/ownership manifests
          └──────── contract freeze ────────┐
                                            ▼
                                  R3 SQLite transaction substrate
                                            │
                         ┌────── parallel ───┴──────┐
                         ▼                          ▼
              R4 deployment trust anchor   R5 planning + projection contracts
          │                                  │
          └──────────────┬───────────────────┘
                         ▼
               R6 authenticated CLI slice
                         ▼
         R7 compatibility + composition + broker
                         ▼
               R8 authority reads + deny gate
                         │
          ┌──────────────┼──────────────┐
          ▼              ▼              ▼
 R9 conversation    R10 journal     R11 calendar reads
          └──────────────┼──────────────┘
                         ▼
               R12 workspace barrier
                         ▼
               R13 production loop core
                         │
          ┌──────────────┼──────────────┬──────────────┐
          ▼              ▼              ▼              ▼
 R14 recovery      R15 scheduler   R16 effects    R17 delivery/inbox
          └──────────────┴──────────────┴──────────────┘
                         ▼
               R18 atomic workflows
                         ▼
               R19 scenario evals
                         ▼
               R20 readiness/release evidence plane
                         ▼
               R21+ capability journeys
```

Parallelism is allowed only after the predecessor freezes the shared identity and public-port contracts. R1/R2, R4/R5, R9/R10/R11, and the contract-first portions of R14–R17 are the principal parallel lanes. R3 starts only after the R1 identity/version and R2 ownership/port contract freeze, although non-contractual storage experiments may occur earlier without becoming evidence. Each convergence point is an integration barrier; directory ownership alone does not establish independence.

The topologically complete path to evaluated offline action evidence is `R0 → (R1 || R2) contract freeze → R3 → (R4 || R5) → R6 → R7 → R8 → (R9 || R10 || R11) → R12 → R13 → R14 → (R15 || R16 || R17) → R18 → R19`; R16 may build pre-recovery effect internals beside R14 but cannot complete until R14 passes. Real Calendar or Telegram exposure additionally requires R20, current independent entitlement records, and V10/V11 at the exact last reversible boundary.

### Post-R6 compatibility and supersession ledger

R7 owns this ledger as a closed exact set. `RETAIN` preserves durable/product semantics;
`ADAPT` changes only the runtime representation or authority path; `HISTORICAL_ONLY` preserves old
evidence without allowing it to prove a stronger successor claim; `DEPRECATE_AS_EXECUTABLE` keeps a
contract identity for replay/parity while making its old call path unreachable.

| Completed contract | Disposition after R6 | R7 convergence obligation |
|---|---|---|
| R1 IDs, canonical bytes, schema/version identities and frozen exports | `RETAIN`; `ADAPT` at owner boundaries | Keep durable identities and canonical bytes byte-compatible; cross-owner messages use inert schemas/bytes and owner-local strict models rather than shared executable Python objects. |
| R2 ownership/export/signature/bridge/bootstrap manifests | `RETAIN + ADAPT` | Extend the closed manifests with owner process, IPC peer/session, public DTO, runtime generation and realized graph; the old in-process graph is `HISTORICAL_ONLY` for isolation. |
| R3 SQLite/EventAppender/deletion/durable-inbox primitives | `RETAIN`; raw access `ADAPT` | Move raw connections, appender and irreversible execution behind the broker without changing committed durable semantics. R17 must re-evidence inbox guarantees against the complete ingress universe. |
| R4 trust records, ceremonies, decision journal and materialization | `RETAIN`; authority path `ADAPT` | Preserve durable record kinds and replay; make journal/storage/channel raw authority broker-only and replay the trust fixtures through authenticated IPC. |
| R5 planning/projection records and public semantics | `RETAIN`; execution path `ADAPT` | Run owner use cases in their declared compartments; replace repository/cross-owner object calls with broker-routed DTOs while preserving canonical committed results. |
| Frozen R1/R2 and R4/R5 fixtures/evidence | `HISTORICAL_ONLY` for new isolation claims | Keep every fixture and result addressable; map each to its successor R7/R8 test and forbid using an old pass as process-isolation, broker, IPC or gate evidence. |
| R6 CLI slice and bridge | semantic result `RETAIN`; bridge `DEPRECATE_AS_EXECUTABLE` | Use the R6 command/result/restart behavior as the parity oracle. The broker path must produce byte-compatible durable results, then static/runtime mutants must prove the direct in-process path unreachable. |

Every frozen export, schema, record owner, fixture and realized R6 bridge must occur exactly once in
the executable ledger. Missing, duplicate, unclassified or orphan successor entries keep R7 open.
The ledger records successor ownership; it never mutates a frozen predecessor contract in place.

## Increments

Numbered subitems below partition an existing increment's work; they do not independently
close its DoD or grant exposure. New owners and surfaces extend the current R7/R8 manifests
and rerun their relevant contracts. Reuse physical mechanisms while re-evidencing each new
owner, query, derivative and irreversible boundary; a predecessor pass is not successor evidence.

### R0 — Verification substrate and coverage registry

**Depends on:** current scaffold. **Parallel after completion:** R1 and R2.

Create the profile runner, evidence-result schema, invariant/fixture/surface registries, before/after state observer, deterministic fault/mutation runner, and compile-only transcript parser. The historical R0 result derived 97 numbered architecture invariants and registered A01–A97. The current verifier reads the promoted normative source and requires A01–A108; this source rebase does not rewrite or upgrade the historical R0 evidence. Register transcript fixtures T01–T04; unknown, renumbered, duplicated, or omitted source and registry items fail closed.

**DoD:** `V0` proves exact-set completeness and rejects missing/orphan rows; `V8` compiles the three authored transcripts, rejects an unknown discriminator/field/mixed modality, proves `design` is ignored, and reserves T04 stale-proposal. `uv run python -m chiplog.verification fast` emits a bound result artifact. This is verifier infrastructure only, not product evidence.

### R1 — Durable identities, canonical bytes, and version boundary

**Depends on:** R0. **Parallel with:** R2; freezes identity/version contracts required by R3.

Implement tenant/principal/record IDs, schema/codec/canonicalization bindings, fingerprints, owner tags, unknown-byte preservation, and exact-version admission. Keep shared data inert; domain policy remains owner-local.

**DoD:** V1 golden-byte tests and one-field mutation matrix pass; historical bytes are verified with their producing versions; unknown schema/store version is typed `HOLD` with no write. V2 rejects executable shared validators/enums/registries. Covers A02, A11, A29 and layout rules 1–3.

### R2 — Boundary, ownership, routing, and composition manifests

**Depends on:** R0. **Parallel with:** R1; freezes ownership/public-port contracts required by R3.

Generate closed package/export/signature/capability, executable-root, record-ownership, bridge, provider, surface, and synchronous routing manifests. Establish allowed dependency direction and reject private-package, adapter-to-adapter, generic append, callbacks/reverse cycles, dynamic import, and unregistered roots.

**DoD:** V2 exact-set and mutant suites fail on missing/extra owner, bootstrap, executable root, exported symbol or capability; forbidden edge, SCC, private import, dynamic resolution and generic `append(events)` leakage; every concrete record kind has one commit-boundary owner. Covers A14, A29, A63, A64 and layout rules 1–9, 12–14.

### R3 — SQLite platform substrate and deterministic concurrency

**Depends on:** completed R1/R2 contract freeze.

Implement version admission, connection/transaction primitives, the broker-owned single writer/EventAppender, atomic publication skeleton, reserved authenticated-evidence lane, the generic authenticated durable evidence-inbox commit/ack state machine (including push acknowledgement, polling cursor and non-redeliverable disposition), authoritative logical-deletion fence/frontier primitives, typed derivative provenance/dependency registration, and deterministic clock/ID ports. Application code receives capability-owned ports, never the physical appender.

**DoD:** V3/V6 prove atomic all-or-none publication, bounded backpressure, cancellation-safe shutdown, reserved-lane progress, evidence commit-before-ack, every commit/ack crash window, polling-cursor monotonicity, explicit possible loss for non-redeliverable input, lost commit acknowledgement, identical replay, changed-fingerprint conflict and both CAS serial orders. Deletion fence/frontier absence, lag or mismatch blocks ordinary read/replay/publication; missing/orphan derivative registrations fail exact-set coverage. Unknown store version or owner/schema mismatch writes nothing. Covers A07, A08, A23, A25, A26, A28, A29.

### R4 — Deployment trust anchor and single-principal bootstrap

**Depends on:** R1, R3; R2 owner contracts frozen. **Parallel with:** R5 after shared identity review.

Implement protected monotonic `CurrentDatabaseState`, `DatabaseGenesis`, the non-rollbackable predecessor-bound `TenantDecisionJournal`, signed closed-lineage `DeploymentTenantBinding`, prepared/decided/materialized crash recovery, restore/relabel and journal-key/binding rotation ceremonies, immutable tenant/principal registries, CLI/Telegram/evidence authentication unions and broker-only Telegram `TransportOriginWitness`, rotation/revocation, future-contour prerequisite hold, and one-shot bootstrap. No ordinary work is admitted in unresolved recovery or without the exact single-principal contour.

**DoD:** V3/V6/V7 cover unavailable store/journal, rollback, mismatched/forked binding or journal lineage, every prepare/decision/materialization crash window, positive authenticated no-decision abort, ambiguous observation hold, decided-batch replay, same-tenant advance, cross-tenant new instance, second-principal/shared-endpoint denial, forged/missing transport witness, stale credential/session/source, authenticated late evidence, source authentication before durable commit, denial of acknowledgement on failed/ambiguous commit, and poll-cursor durability before request construction/emission with pre/post-application crash cuts. Operator-only recovery is the sole admitted path under mismatch. Covers A02, A13, A19, A20, A28, A85–A97.

### R5 — Planning owner and rebuildable projection

**Depends on:** R1, R2, R3. **Parallel with:** R4 after identity freeze.

Implement one narrow planning revision and its owner factory/use case/repository port plus a separately owned rebuild/query projection. The first command should be the smallest root/intention operation chosen from the adopted Plan registry; the choice must be recorded before schema freeze. Projection input cannot authorize a command.

**DoD:** V4/V5 prove typed tenant-bound construction, foreign/wrong-source/wrong-head/forbidden-predecessor rejection, exact replay/conflict, projection rebuild from committed events, checkpoint rejection/fallback, and projection-as-command denial. Covers A03–A05, A22, A29, A33.

### R6 — Authenticated CLI planning slice

**Depends on:** R4, R5.

Wire `CLI → planning inbound port → use case → owner factory → repository port → SQLite adapter → EventAppender`, then query/render through the independent projection path. No adapter calls another adapter.

**DoD:** V4 component test crosses only public ports; V7 substitutes tenant, principal, peer credential, payload-carried context and stale session; all deny with no write. V2 observes the realized call graph. A committed revision survives restart and renders from the rebuilt projection. Covers A02–A05, A10, A14.

### R7 — Canonical composition, owner isolation, and broker generation

**Depends on:** R2, R3, R4, R6.

Consume the promoted normative source and exact A01–A108 verification set. Close the
compatibility/supersession ledger above, then build
one canonical assembly manifest and graph builder, Dishka scopes, strict Pydantic public DTOs and
owner-local models, process-isolated owner compartments, tenant `AuthorityBroker`,
`RuntimeGraphGeneration`, authenticated IPC/session/proxy capabilities, finite call budgets,
resource-hold rules, drain/restart reconciliation, one-shot/idempotent raw-operation tokens, and
bounded read operations. Production and evaluation use identical broker binary, schema, owner
partition and application loop; only registered leaves differ.

R7 executes in one non-skippable sequence; the labels are internal milestones, not independently
evidenced increments:

1. **R7.1 — compatibility freeze:** exact predecessor→successor ledger and canonical parity corpus;
2. **R7.2 — inert boundary:** schemas/codecs plus owner-local Pydantic models; no executable shared object crosses an owner boundary;
3. **R7.3 — broker genesis:** authority-empty broker and one isolated owner generation;
4. **R7.4 — routed graph:** multiple owners, authenticated public-port DTOs, finite budgets and supervision;
5. **R7.5 — authority transfer:** broker-only raw storage/provider/channel handles, tokens and bounded reads;
6. **R7.6 — lifecycle parity:** drain, restart, epoch/session reconciliation and production/eval equivalence;
7. **R7.7 — R6 convergence:** CLI semantic/canonical parity followed by mechanical rejection of every direct in-process bypass.

**DoD:** V0 proves the promoted normative source contains exactly A01–A108 and the current registry
matches it; V1 proves retained canonical bytes and durable identities are unchanged across owner-local
encoders; V2/V3/V7 prove the compatibility ledger is complete, the R6 old/new paths have equal
canonical durable outcomes, the old path is no longer executable, manifest equals realized graph,
production/eval identity, reverse-order teardown, no raw handle/proxy escape, no cross-owner shared
session/address space, no await with a listed exclusive resource, no fallback across generation,
authority-empty quarantine, exact token consumption before raw execution, read two-point validation,
and restart epoch/session reconciliation. No R7 milestone alone advances evidence state. Covers A09,
A14, A20, A23, A65–A84 and the Stage-0 runtime contract.

### R8 — Authority-read tracing and operation deployment gate

**Depends on:** R7.

Make the typed manifest-recording read capability the exclusive source of authority-sensitive proposal/commit inputs. Implement `DeploymentGatePort`, exact surface inventory, generation-fenced eligibility check and default `HOLD` at the last reversible boundary. Evaluation and production permits are namespace-separated.

1. **R8.1 — authority trace and freshness:** extend R7 verified reads with the exclusive
   recorder and transaction-local whole-binding reproduction. Preserve the direct-principal
   CREATE row; natural-language adoption applies only to its registered rows.
2. **R8.2 — current executable surfaces:** derive and classify the actual canonical
   authority-use and handoff paths, including CLI, and wire enforcement through owner IPC
   and the broker. Future journeys remain isolated boundary fixtures, not business features.
3. **R8.3 — ordered eligibility and handoff:** bind independent current entitlement,
   invalidation, capacity and exact payload in the common order; observe both durable decisions
   and an isolated sink. Exercise individual eligibility mismatches with a current-generation
   request as well as stale-generation races, so one early rejection cannot mask every predicate.
4. **R8.4 — Stage-0 convergence:** wire the R8 evidence slice, current surface inventory and
   R0–R8 aggregation into the canonical profile; retain the narrow historical `fast` claim,
   reached bypass mutants and offline canaries. A green unit gate alone cannot close Stage 0.

**DoD:** V0/V10 reject missing/orphan/downstream-misclassified surfaces; V5 rejects direct/injected/missing/extra/untracked proposal dependencies; V10 mutates generation, readiness, authorization/profile, cohort, cap, purpose, instrumentation, stop rule, cursor and lease immediately before commit and observes no handoff. Every development/test/provider/channel bypass mutant fails, and the offline profile proves structural non-reachability of real recipients/providers. Covers A06, A12, A24, A30, A33, A36, A60–A62 and the operation-level gate.

**Stage-0 integration barrier:** `verification stage0` passes R0–R8 evidence against the promoted
normative source and exact A01–A108 registry, the post-R6 compatibility ledger is closed, and every
cohort-visible surface remains `HOLD`. Historical R0–R6 evidence is included only for its original
claims and cannot substitute for the R7/R8 successor evidence.

### R9 — Conversation and dashboard workspace

**Depends on:** R8. **Parallel with:** R10, R11.

Implement one canonical tenant conversation, channel-scoped visibility inside the immutable contour, history tools, typed dashboard registry/builder/navigation/LRU/budget, disclosure-label propagation, and one authoritative frontier per workspace batch.

1. **R9.1 — conversation and planning view:** bounded channel-scoped history and mandatory
   `core.conversation`, plus `core.planning` over the existing planning query contract.
2. **R9.2 — workspace projections:** typed family registry, immutable `DashboardScreen` /
   `ScreenSnapshotRef`, navigation, LRU and budget behavior. Reuse the stateless
   `agent-dashboard` boundary; Chiplog owns snapshot persistence, replay and transitions.
   Journal/calendar families join through their R10/R11 ports at R12.
3. **R9.3 — disclosure and deletion on current derivatives:** close the disclosure inventory,
   provenance and R3 fence integration for history, screens and caches; deletion must exclude
   current derivatives from ordinary reads/rebuilds/context. Full deletion UX remains R21.5.

**DoD:** V4/V5/V7 prove mandatory conversation outside the three-family LRU, bounded eviction,
lagging-screen non-authority, shared-frontier equality, background-refresh non-promotion,
cross-channel contour/label enforcement and model inability to narrow labels. The initial
`DisclosureSurfaceManifest` exactly covers every content-bearing path executable at R9 and rejects an
unknown, omitted, extra, aliased or dynamically substituted row; every later increment must extend
and re-close that exact set before its new path executes. Covers A19, A21, A24, A27 and owns A106.

### R10 — Evidence journal seed

**Depends on:** R5, R8. **Parallel with:** R9, R11.

Implement owner-authored `FactClaim`, candidate evidence, dispositions, journal projection/actions, exact displayed confirmation and receipt references while preserving Plan/Fact/candidate/provider separation. Direct fact admission uses the complete positive proof vector; ambiguity routes to confirmation.

1. **R10.1 — fact admission:** direct positive-proof grammar and exact displayed-confirmation
   path, each with owner-authored immutable identities, complete fingerprints and replay/conflict.
2. **R10.2 — current-head journal actions:** `record_fact`, `correct_claim`, `retract_claim`,
   `confirm_candidate` and provenance/lineage reads; prove stale-head rejection, unresolved
   competing successors and lost-ack replay. Defer the `REPLACE` command surface and complex
   correlation/merge to R21.3; correction cannot emulate replacement.
3. **R10.3 — T02 contract alignment:** version the authored scenario before executable use.
   Its current “Я не ходил…” negation cannot directly append under the normative positive-proof
   rule: show the exact claim and require confirmation, or retain clarification with no append.
   Keep a separate affirmative direct-admission fixture and preserve zero Plan mutations.

**DoD:** V5 and public-port component fixtures derived from the aligned T02 prove an authorized fact changes journal but not Plan; negative direct-admission vectors cover question, request, command, wish, doubt, conditional, hypothetical, quote/mention, third-party attribution, correction/retraction, negation, ambiguous subject and model/provider inference. Confirmation binds immutable display and exact current heads. These are component semantics, not a production-loop trajectory; full T02 execution joins R18/R19. Covers A04, A22, A30–A32.

### R11 — Read-only calendar boundary

**Depends on:** R8. **Parallel with:** R9, R10.

Implement agenda/detail observation through snapshot-bound bounded read ports. Provider state remains evidence; reads cannot write Plan or bypass the authority-read recorder.

**R11.1 — calendar query specialization:** reuse R7 bounded read/release and R8 tracing contracts,
extend exact query/owner/invalidation manifests, and add provider observation provenance and typed
staleness. The current planning-publication reader is not a generic calendar/workspace reader.
Use hermetic provider leaves for this barrier; real-provider exposure remains gated under R20/R21.1.

**DoD:** V4/V5 prove current snapshot/context identity, lag/stale/indeterminate typed results, no write/no effect, no adapter shortcut and no projection/provider-as-authority. Broker invalidation before response yields current result or `STALE_OR_INDETERMINATE_READ`, not a replacement session. Covers A05, A24 and the read-only foundation later specialized by A49, A52 and A57.

### R12 — Stage-1 workspace integration

**Depends on:** R9, R10, R11.

Integrate conversation, dashboard, journal and calendar observations through public ports. Relevant committed history must be read before a consequential proposal; every composed output preserves provenance and disclosure labels.

**R12.1 — shared batch and Stage-1 evidence:** assemble internal conversation/family/policy
reads in one transaction/frontier, mark external lag explicitly, and wire the Stage-1 profile.
Run the aligned T02 component contract and read-before-proposal fixture through public ports
in canonical composition. Do not create a temporary eval-only agent loop: production-loop
scenario execution begins with R13 and converges at R18/R19.

**DoD:** `verification stage1` proves that composed component contract; V5 forbids journal→Plan promotion, provider→Fact promotion, cross-frontier authority, stale candidate wording and label narrowing. Covers A19, A21–A32 applicable to the slice.

### R13 — Durable production-loop core and offline walking skeleton

**Depends on:** R7, R8, R12.

Implement the exact production `Run`, `Turn`, `ModelCallAttempt` state machines, orchestration events, bounded budgets, typed model/tool schemas and `CompleteAcceptance`. First run a hermetic deterministic or model-free proposal→exact adoption→intent proposal→receipt journey; it has no real provider/channel reachability and receives no cohort-visible permit.

1. **R13.1 — prompt/schema artifact:** owned-static `promptstrings` integration with strict
   placeholders, typed response schema and stable prompt ID/version/content hash; bind ordered
   `ToolSpec`, generator/schema identities and exact rendered bytes to production/eval replay.
   Golden tests compare rendering and failure behavior. A richer upstream API is optional and
   cannot substitute a delegated source that loses the current validation contract.
2. **R13.2 — smallest adopted Planning command:** connect immutable interpretation, exact
   displayed proposal and adoption to the R8 recorder/committer for the chosen narrow operation.
   Prove stale adoption requires redisplay and new adoption; never treat model output as authority.
3. **R13.3 — single-loop scenario driver:** reuse the transcript compiler, artifact identities
   and fault observer to drive the actual loop through registered hermetic leaves. The first
   receipt reports only a committed local result; an intent proposal cannot claim provider success.
   Executed effect/recovery receipts require R16–R18. R19 extends this driver rather than creating
   another orchestrator.

**DoD:** V2 proves one production/eval loop; V5 covers all legal/illegal transitions, exact Run/Turn
heads, budget and completion joins, one durable `ModelCallAttemptLineage` per Turn/call slot, immutable
retry generations, one monotone selector, and pre-emission accumulator/`ModelVisibilityManifest`
sealing; V7 network/secret/real-recipient canaries prove offline isolation; V9 observes
proposal-before-authority and default-HOLD handoff. Merely schema-valid model output cannot mark
success. Covers A09, A13, A16, A20, A34 and owns A105.

### R14 — Recovery frontier and call accounting

**Depends on:** R3, R7, R13. **Parallel contract work with:** R15–R17 after identities freeze.

Implement closed recovery-frontier membership, immutable suspension baselines, `TERMINAL | READ_ONLY_RETRY_PENDING`, sealed fan-out, acceptance branches, terminal accounting versus continuation-ready joins, recovered outcomes, successor classification/publication, original-stream obligations and post-terminal recovery work.

1. **R14.1 — call accounting:** sealed fan-out, exact acceptance branches, cancellation and
   terminal/result/obligation joins with reached omission, rivalry and crash fixtures.
2. **R14.2 — recovery evidence reduction:** resolver-only closure plus versioned
   `SemanticEvidenceReduction` CAS. Both continuation consumers bind accepted witness/closure
   and the current reduction head, not the latest raw append; compatible and rival post-closure
   evidence must yield the allowed same-identity advance or typed hold.
3. **R14.3 — resume and successor:** transaction-local frontier classification/publication,
   same-Run admission and cross-successor read-only pending branch with a non-resettable budget.
4. **R14.4 — worker and post-terminal fencing:** exact writer applicability registry, original
   recovery streams and post-terminal work; freeze the R15–R17 seam before their recovery integration.
5. **R14.5 — model-attempt recovery:** five-state lineage, lost response and proof-gated
   no-exposure replacement, then the complete R14 convergence suite. Contracts may freeze early;
   partial recovery evidence never closes R14.

**DoD:** V5/V6 execute omission/addition/duplication/reordering/branch-mixing mutants; partial/rival
fan-out; pre/post-accept cancellation; unknown/recovery results; evidence-before-closure races;
same-Run/successor/hold/fault total classification; lost-ack replay; non-resettable read-only budget
across resume/restart/successor. `WorkerAuthoritativeCommitRegistry` covers every writer path, record
kind and applicability variant bidirectionally, including pre-root, live execution, post-terminal work
and rollover families. `ModelCallAttempt` exercises the exact five-state lifecycle; possible emission
never retries without registered proof of no exposure, and a permitted replacement stays in the same
lineage under a fresh selector generation. Every sealed call has exactly one admissible branch. Covers
A15, A38–A47, A49–A52, A54–A57, A59 and owns A99 and A108.

### R15 — Scheduler occurrence and lease lineage

**Depends on:** R13; R14 contracts frozen before takeover/recovery. **Parallel with:** R16, R17.

Implement revision-bound occurrence identity, `INDIVIDUAL | COALESCED | SKIPPED` disposition, canonical aggregate manifests, tagged lineage roots, leases/generations, takeover and exact scheduler successor fencing.

1. **R15.1 — bounded interval decisions:** exact schedule/policy/bound heads, zero/one/N
   occurrence algebra, monotone `SchedulerIntervalBoundHead` and complete eligible manifests.
2. **R15.2 — overflow and resolution:** one active overflow hold blocks amendment; a sufficient
   successor bound permits one `SchedulerIntervalResolutionDecision` that parents every sub-batch
   and alone advances the boundary. Reject omitted/rival members, duplicate resolution and early amendment.
3. **R15.3 — execution lineage:** materialization/disposition CAS, leases, takeover and physical
   epoch rollover; integrate with R14 before claiming scheduler recovery.

**DoD:** V5/V6 cover all three competing CAS serial orders, crash/restart around atomic skip/coalesce,
stable replay, representative/namespace substitution, expiry without takeover,
takeover-before-submit, lease/root/generation/clock mismatch, uint64 exhaustion without wrap/reset,
authenticated journal-decided physical-epoch rollover, rival rollover, stale old-epoch work and
duplicate scheduler delivery. Lookup, rebuild and dedup expose exactly one current physical epoch per
stable lineage and preserve original Run/authority bindings. No rival Run/effect is allocated. Covers
A17, A18, A40, A43, A47, A51, A53, A58 and owns A98; specializes the scheduler rows of A99.

### R16 — Effects, outbox, dispatch binding, and reconciliation

**Depends on:** R5, R8, R13; R14 before any retry/recovery. **Parallel with:** R15, R17.

Implement owner-produced effect intents, Plan/effect atomic publication, full version-bound `SEND_COMMITTED` reducer, immutable `DispatchSemanticBinding`, provider attempt/transmission/outcome, stable idempotency, outbox, ambiguity, reconciliation and separately authorized compensation.

**R16.1 — specialize existing publication mechanics:** reuse R3/R7 broker-owned atomic records,
fingerprints and replay, adding the effect owner, exact acceptance/intent envelope and gated fake
dispatch/reconciliation. Do not rebuild the physical appender or defer the full dispatch reducer;
R18 integrates its owner contracts with the complete loop.

**DoD:** V5/V6 cover every named dispatch state/transition, crash edge, domain+intent atomicity, no provider inside the transaction, stale binding, the exact initialized-head `ToolCallAccepted` CAS with atomic intent publication, a reached pre-accept-dispatch mutant, unknown outcome without blind retry, exact reconciliation, changed/rival replay, compensation with fresh authority, and late evidence. T01 runs with a fake adapter; T03 reaches lost response and observes zero replacement attempts. Covers A06–A08, A30, A35, A37, A48.

### R17 — Durable inbox, delivery, and channel parity

**Depends on:** R8, R13. **Parallel with:** R15, R16.

Specialize the R3/R4 authenticated durable inbox for Telegram witness/replay identity, then implement CLI/Telegram response delivery, outbox workers and evidence-bound deterministic consequential rendering. The common acknowledgement contract is reused, not reimplemented.

1. **R17.1 — universal ingress custody:** extend and re-evidence the existing commit/ack
   primitives over every actual ingress row, including destructive-read loss slots and exact bytes.
2. **R17.2 — admission and recovery:** bounded FIFO/reserves, deadline-preserving rebase,
   restart/drain joins and immutable quarantine/parser lineage.
3. **R17.3 — delivery:** exact origin/recipient selection, disclosure revalidation,
   deterministic evidence-bound rendering and ambiguous delivery without model rerun.
4. **R17.4 — channel parity:** CLI/Telegram canonical-loop integration and the complete
   ingress/delivery counterhistory suite. R17 remains broader than a Telegram adapter.

   **Early live CLI slice:** [Codex OAuth dialogue](CODEX-CLI-AGENT.md) connects the
   existing AgentLoop to a direct model transport, with separate persistent OAuth
   storage and default `gpt-5.6-terra` / `low`. This conversational/proposal slice
   does not complete R17.4: Planning adoption, external delivery and R14 recovery
   are not mounted; unknown model outcomes are retained without automatic replay.

**DoD:** V4/V6/V7 close `EvidenceIngressSurfaceManifest` over Telegram push/poll, CLI, provider
callback/poll, reconciliation and tool-result paths and rerun the shared commit/ack contract against
every row. Before the earliest irreversible handoff each path has one durable receipt token; destructive
reads preallocate a loss slot and exact-CAS raw bytes into custody. Acceptance/ack/cursor/source release
requires one durable custody state with exact replayable bytes or an enumerable loss obligation.
Admission uses non-borrowable reserves, strict ready-generation FIFO, complete bound snapshots and
deadline-preserving blocked-prefix rebase; restart/drain joins token, custody, FIFO and bound state.
Authenticated malformed useful bytes enter one CAS-selected quarantine/reprocessing lineage without
rewriting custody. Tests also prove duplicate ingress, stale/forged transport witness, delivery
ambiguity without model rerun, explicit `ORIGIN_EXACT | MODEL_SELECTED_EXACT`, exact authenticated
provider-recipient dedup, last-boundary endpoint/disclosure revalidation, prospective-only narrowing,
committed-query rendering and no success claim without closed evidence. CLI and Telegram use the same
application loop/projection while retaining distinct authentication bindings. Covers A10, A19, A32,
A34, specializes A28, and owns A100–A104 and A107.

### R18 — Atomic cross-owner workflows and recoverable action loop

**Depends on:** R10, R14–R17.

Add named typed publications for Plan/effect, Mark done Plan/Fact, and provider receipt/candidate evidence. Owners construct immutable results independently; the coordinator only validates and commits the complete envelope/idempotency map.

1. **R18.1 — bounded Planning lifecycle convergence:** extend the CREATE-only baseline with
   exact-current-head amend/supersede/retract and the authority rows required by the Stage-2
   journeys, preserving full atomic result/replay semantics. Register direct acts, exact bounded
   mandates, adopted proposals and specialized stricter predicates without broad fallback;
   the assessor-only representation row remains non-Plan. Rich root transformations remain R21.3.
2. **R18.2 — atomic workflow and scenario barrier:** integrate the independently constructed
   owner results with R14–R17; run T01–T04 through the R13 production-loop driver, including
   the aligned T02. New owner-level authority maps and lost-ack reconciliation remain required
   even though physical multi-record publication already exists.

**DoD:** V5/V6 prove all-or-none publications, per-result authority map, owner/schema/fingerprint enforcement, unauthorized no-existence disclosure, lost-ack replay without owner rerun, and missing/corrupt result reconciliation. T01–T03 run offline through the exact production loop; T04 stale proposal is added and must redisplay rather than act. Covers A04, A06–A10, A22, A29–A35, A41, A46, A59.

**Stage-2 semantic barrier:** `verification stage2-offline` passes R0–R18. All real Calendar, Telegram recipient, scheduler, disclosure, compensation and recovery-transmission surfaces still stop at `HOLD` without exact current entitlement.

### R19 — Scenario evaluation and promotion evidence

**Depends on:** R18.

Complete transcript-to-bundle compilation, injectable boundary harness, trace/state/effect observation, deterministic feature extraction, LLM-judge artifacts, immutable verdict policy and retained reruns. Eval assembly substitutes registered leaves but preserves the production loop, broker, owner partition and security profile.

1. **R19.1 — executable bundle coverage:** extend the R0 compiler and R13/R18 driver with
   complete arrange/observe/assert-transition contracts, retained traces and dashboard sequences.
2. **R19.2 — deterministic verdicts:** versioned feature vectors, reached hard forbids,
   partial-order checks, offline canaries and replayable artifact binding.
3. **R19.3 — judge and promotion runs:** independent seeded passes, disagreement retention
   and immutable rerun identities. Judges cannot replace component or deterministic evidence.

**DoD:** V8/V9 run T01–T04; an unused required fixture, hard forbid, partial-order violation,
deterministic gate, unknown field or offline real-network reachability fails. Eval/model trace and
replay paths occur exactly once in the current `DisclosureSurfaceManifest` and join the selected
historical model-attempt visibility manifest. Dev uses one trajectory/pass; promotion uses at least
three trajectories and two independently seeded judge passes per trajectory. Critical disagreement
yields retained `INCONCLUSIVE`; no judge or average forgives deterministic failure. Covers A09, A16,
Evaluation architecture, and closes the eval rows required by A105–A106 and A108.

### R20 — Readiness, evaluation, production, and release evidence plane

**Depends on:** R8, R19.

Implement capability-scoped coverage/applicability manifests, readiness evidence ingestion, cause ledger/open-cause projection, frozen profiles, evaluation authorizations/results, release approvals and independent-role bindings. Keep this control plane separate from semantic tests and the deny-only operation port.

**DoD:** V10/V12 prove exact transition registries, freshness/current-head checks, role separation, open-cause preservation/resolution, evaluation completion not production approval, no `HELD → ACTIVE`, and exact per-surface gate decisions at the last reversible boundary. Missing/stale/partial/self-authored evidence remains `HOLD_ADOPTION`. `promotion`/`release` profiles verify records but cannot issue them. Covers A12 and VISION evaluation/release governance.

### R21+ — Capability journeys, one bounded contract at a time

**Depends on:** R18–R20; each journey declares additional dependencies.

Add long-term capabilities only with a named user journey, exact VISION clauses, owner/public ports, surface inventory, negative counterhistory, scenario bundle and release evidence applicability. Recommended order follows dependency, not feature appeal:

1. **R21.1 — real adapters:** Calendar creation/reconciliation and Telegram owner ingress/delivery,
   each under exact evaluation authorization and its own V11 external observations.
2. **R21.2 — recurrence:** the bounded user journey over the R15 scheduler contract.
3. **R21.3 — richer Plan/Fact/Journal:** replacement, correlation and root transformations beyond
   the R10/R18 slices; do not repeat the already required basic correction/retraction work.
4. **R21.4 — evidence divergence and reconciliation:** exact material claim/disposition
   dependencies, `EVIDENCE_DIVERGED` views and separately authorized Planning reconciliation.
   Any earlier journey using such dependencies must implement this contract before execution;
   this entry cannot postpone its last-boundary checks.
5. **R21.5 — complete logical-deletion journey:** privacy-safe dependency preview, content-minimal
   receipt, transitive derivative/pending-action exclusion and retained-copy non-repopulation.
   Reuse R3 fences and per-surface provenance; current-surface exclusion never waits for this UX.
   Physical byte erasure remains separately held.
6. **R21.6 — working agreements and delegation preparation:** single-principal policy and private
   proposal/reference preparation only; no delegate acceptance or cross-principal authority.
7. **R21.7 — multi-principal/multi-party prerequisites:** privacy, affected-party rights and exact
   contour migration/attestation before a second-principal surface is admitted.
8. **R21.8 — executable delegation:** depends on R21.7 and the relevant owned-root/undertaking
   contracts; separately owned delegate target, delegate-authored acceptance and exact reconciliation.
9. **R21.9 — additional action journeys:** proactivity, compensation and additional providers,
   specializing the existing dispatch/recovery contract with fresh authority and surface evidence.
10. **R21.10 — production authorization:** only after the independent VISION readiness/release process.

**DoD:** each capability has a dedicated `verification <capability-profile>` extending V0–V12, at least one positive scenario and one discriminating counterhistory per governing clause/surface, exact external observation where real adapters are claimed, and a capability-scoped coverage fixed point. Passing semantic evidence does not clear adoption.

## Traceability matrix

Every architecture invariant maps to exactly one primary increment below. Secondary consumers may reference it but do not own coverage.

| Invariants | Primary increment | Primary evidence |
|---|---|---|
| A01 | R0 | authority pointer and exact-set registry |
| A02 | R4 | tenant/principal and deployment-binding substitution suite |
| A03 | R5 | planning command/construction conformance |
| A04 | R5 | Plan/Fact/candidate/projection store-role separation |
| A05 | R5 | projection rebuild and provider/projection non-authority |
| A06 | R8 | exact proposal/adoption head and gate races |
| A07 | R16 | atomic domain revision and effect intent |
| A08 | R16 | explicit unknown outcome and unsafe-replay denial |
| A09 | R13 | production/eval loop identity |
| A10 | R17 | committed-query channel rendering |
| A11 | R1 | exact-version admission/canonical bytes |
| A12 | R20 | independent readiness/release records and default hold |
| A13 | R4 | model/network/secret and tenant-scoped tool boundary |
| A14 | R2 | hexagon manifests and shortcut mutants |
| A15 | R14 | suspension baseline/resume/successor proofs |
| A16 | R19 | feature vectors and deterministic hard gates |
| A17 | R15 | stable revision-bound occurrence identity |
| A18 | R15 | lease-generation takeover histories |
| A19 | R9 | canonical conversation and channel visibility |
| A20 | R7 | durable identity versus container scopes |
| A21 | R9 | conversation/dashboard family contract |
| A22 | R10 | journal non-authority and candidate separation |
| A23 | R3 | single writer and reserved evidence lane |
| A24 | R9 | one authoritative workspace frontier |
| A25 | R3 | strict logical exclusion, not physical-erasure evidence |
| A26 | R3 | typed provenance and authoritative deletion fence |
| A27 | R9 | monotone disclosure-label propagation/narrowing |
| A28 | R4 | authenticated durable-inbox acknowledgement and source binding |
| A29 | R2 | exact commit-boundary ownership registry |
| A30 | R10 | exact candidate/display/head confirmation binding |
| A31 | R10 | direct FactClaim positive proof vector |
| A32 | R10 | evidence-aware consequential delivery assertions |
| A33 | R5 | closed Plan actor×operation×authority registry |
| A34 | R13 | atomic `CompleteAcceptance` and delivery intent |
| A35 | R16 | exact dispatch semantic binding |
| A36 | R8 | authority-read trace and dependency mutants |
| A37 | R16 | separately authorized compensation |
| A38 | R14 | closed recovery-frontier membership and branches |
| A39 | R14 | deterministic total recovery disposition |
| A40 | R14 | atomic successor publication with scheduler fencing extension |
| A41 | R14 | independently owned external/recovery streams |
| A42 | R14 | terminal versus retry-pending branch distinction |
| A43 | R14 | no-retry lineage through takeover/successor |
| A44 | R14 | atomic sealed-response fan-out |
| A45 | R14 | acceptance-consistent terminal/result/obligation closure |
| A46 | R14 | resolver-only recovered-outcome batch |
| A47 | R14 | transaction-local successor classification and scheduler proof binding |
| A48 | R16 | exact-head acceptance plus atomic intent before dispatch |
| A49 | R14 | distinct bounded read-only retry branch |
| A50 | R14 | accounting versus continuation joins |
| A51 | R14 | active-Run/live-lease admission fencing |
| A52 | R14 | lineage-wide read-only retry budget/reducer |
| A53 | R15 | coalescing boundary and complete eligible manifest |
| A54 | R14 | sealed fan-out count/byte bounds |
| A55 | R14 | cancellation closes orchestration but not accepted streams |
| A56 | R14 | transaction-local `TurnStarted` continuation join |
| A57 | R14 | cross-successor read-only pending branch |
| A58 | R15 | three-way occurrence disposition CAS and namespace identity |
| A59 | R14 | post-terminal recovery work ownership/status join |
| A60 | R8 | exact operation-level deployment gate at last reversible boundary |
| A61 | R8 | closed evaluation/production eligibility modes and default hold |
| A62 | R8 | attested offline non-reachability and gated real surfaces |
| A63 | R2 | closed hexagon package/export/capability universe |
| A64 | R2 | closed bootstrap/executable universe |
| A65 | R7 | realized runtime graph versus manifest attestation |
| A66 | R7 | production/eval graph equivalence and registered leaf substitution |
| A67 | R7 | immutable runtime generation and authority-empty candidate execution |
| A68 | R7 | broker revalidation at irreversible commit |
| A69 | R7 | pure or fresh-isolated factory behavior closure |
| A70 | R7 | sanitized authority-empty worker/quarantine OS profile |
| A71 | R7 | broker sole raw authority and proxy membrane |
| A72 | R7 | generation invalidation drain to definite/unknown outcome |
| A73 | R7 | process split remains one deployable and ownership model |
| A74 | R7 | broker restart epoch/key/session/replay reconciliation |
| A75 | R7 | closed token modes and pre-execution durable issue |
| A76 | R7 | bounded two-point-validated read and inert DTO |
| A77 | R7 | read drain and stale-result suppression |
| A78 | R7 | production/eval broker security-profile identity |
| A79 | R7 | one process/session/generation per capability owner |
| A80 | R7 | broker-routed public-port DTOs and synchronous DAG |
| A81 | R7 | owner-preserving drain/restart and durable disposition |
| A82 | R7 | production/eval owner partition and capability equality |
| A83 | R7 | inert-only shared artifacts and owner-local executable policy |
| A84 | R7 | resource-safe bounded cross-owner awaits/choreography |
| A85 | R4 | genesis/deployment binding and audited restore |
| A86 | R4 | one-shot bootstrap-only principal creation |
| A87 | R4 | principal-preserving rotation and emergency recovery |
| A88 | R4 | complete current channel authentication binding |
| A89 | R4 | evidence authentication before durable inbox commit |
| A90 | R4 | exact immutable single-principal contour |
| A91 | R4 | closed broker-mediated identity ceremonies |
| A92 | R4 | decision journal as predecessor-bound authority source |
| A93 | R4 | deployment binding to journal identity/lineage, not mutable heads |
| A94 | R4 | broker-only Telegram transport-origin witness |
| A95 | R4 | held multi-principal prerequisite/attestation registry |
| A96 | R4 | prepared/no-decision/decided replay semantics |
| A97 | R4 | poll-cursor durability before request construction/emission |
| A98 | R15 | uint64 exhaustion hold and authenticated physical-epoch rollover |
| A99 | R14 | bidirectional worker-commit fence registry across execution and recovery variants |
| A100 | R17 | receipt token before every irreversible ingress handoff |
| A101 | R17 | exact-byte custody before acceptance/ack/cursor/source release |
| A102 | R17 | bounded admission FIFO, reserves, deadlines and complete drain state |
| A103 | R17 | exact executable evidence-ingress surface manifest |
| A104 | R17 | immutable raw quarantine and CAS-selected parser lineage |
| A105 | R13 | durable model-attempt lineage and pre-emission visibility seal |
| A106 | R9 | exact-current disclosure-surface manifest, extended by every later surface owner |
| A107 | R17 | explicit delivery origin, exact recipient tuple and prospective narrowing |
| A108 | R14 | five-state model-attempt lifecycle and proof-gated no-exposure replacement |

Authored evidence fixtures have one primary owner:

| Fixture | Primary increment | Expected verifier |
|---|---|---|
| T01 `calendar-proposal-confirmation` | R18 | V9: proposal/read/adoption/atomic intent/one fake effect/receipt; duplicate forbids |
| T02 `fact-claim-without-plan-change` | R10 | V9: one owner FactClaim, zero planning revisions, explicit response semantics |
| T03 `unknown-calendar-outcome` | R16 | V6/V9: one attempt, unknown outcome, recovery obligation, zero blind retry |
| T04 stale proposal after authoritative head change | R18 | V9: changed head invalidates adoption and forces redisplay/no effect |

## Explicit holds and activation triggers

| Held item | Why held | Activation trigger | Exit evidence |
|---|---|---|---|
| Real Calendar/Telegram/cohort-visible surface | No seed/development bypass; semantic completion is not entitlement. | R19 evidence plus exact current `READY` and scoped `EvaluationAuthorization.ACTIVE` or production profile. | V10 at last boundary plus V11 observed external identity/count. |
| Physical byte erasure | Version one promises logical exclusion, not irrecoverability. | Product authority makes a physical-erasure claim. | Storage/WAL/snapshot/backup adversarial recovery evidence and successor VISION coverage. |
| Multi-principal/multi-party mode | Initial contour is exactly one immutable principal. | A named second-principal journey and complete migration/attestation design. | Cross-principal noninterference, disclosure provenance, affected-party and contour-migration suites. |
| Persistence-survival/migrations | Version one makes no migration promise. | Durable-data preservation across binary/schema versions becomes a product promise. | Shadow/cohort/abort/rollback/restore parity and compatibility evidence. |
| Tenant encryption/key/backup controls beyond file boundary | Trigger is not yet reached. | Backup, cross-tenant restore, stronger isolation or production persistence claim. | Key custody/rotation/revocation, backup isolation and restore attestation. |
| Capability-to-VISION coverage beyond implemented journeys | No speculative empty registries. | A capability enters R21 planning. | Capability-scoped applicability fixed point with independent surface inventory. |
| Production adoption | VISION assurance is bounded-incomplete and governance predicates are unmet. | Independent VISION readiness and release process completes. | Current signed readiness/release records, no open causes, freshness and V12/V10 pass. |

## Integration and sequencing rules

- One integration owner holds each vertical slice; adapter teams may work in parallel only against frozen public contracts.
- A registry grows when a concrete variant is implemented, but its coverage check always rejects unknown runtime members. Empty future packages are not milestones.
- The current verifier source and exact invariant registry are A01–A108; R7 consumes that set.
  Later executable surfaces extend their closed current manifests without changing historical
  A01–A97 evidence identities.
- A completed pre-R7 contract is never silently reinterpreted: its compatibility-ledger row and
  successor evidence must close before the corresponding old executable path is disabled.
- Recovery states are introduced before the operation that can create their uncertainty, never retrofitted after exposure.
- No effect-capable surface lands before R8 default-HOLD gating and exact surface registration.
- No provider/channel mock result is external evidence; V11 is required for a real-adapter claim.
- No stage closes on package shape alone. At least one positive witness and one reached negative witness are mandatory.
- R21 replaces an unbounded “Stage 4”: every capability is a separately finishable journey with its own evidence and entitlement state.

## Open decisions that block specific increments, not the whole roadmap

| Decision | Needed by | Safe work before decision |
|---|---|---|
| Protected monotonic store implementation and authenticated CAS primitive | R4 | R0–R3 and R4 port/fixture contract |
| First admitted planning operation and smallest record registry | R5 schema freeze | R0–R3 and candidate conformance fixtures |
| Platform-store unavailable read behavior (`deny all` versus operator quarantine) | R4 | R4 recovery-state model; ordinary admission remains denied |
| Exact last reversible boundary per Stage-2 surface | R8 surface registration | deny-only port, inventory schema and race harness |
| Independent attester and evidence-artifact custody/retention | R20 | semantic evidence generation through R19 |
| First real evaluation cohort/provider/channel bounds | R20/R21 | complete offline semantic and gate evidence |

No unresolved item above permits a weaker default. Until decided, the applicable path remains `HOLD`.
