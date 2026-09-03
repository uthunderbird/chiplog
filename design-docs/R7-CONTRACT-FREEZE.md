# R7 contract freeze

Status: implemented and promoted as Stage-0 evidence.

## Goal and boundary

R7 preserves the R1–R6 durable identities, canonical bytes, record semantics and CLI outcome while
replacing executable cross-owner and raw-authority paths with one canonical, broker-routed,
process-isolated runtime generation. The implemented boundary includes owner/generation/session
identity, finite call bounds, inert payload bytes, closed disjoint call outcomes, owner-local strict
DTOs, exact routed graph checks, process launch, authenticated local IPC and broker-owned raw
authority.

The production CLI now enters through `open_r7_runtime`. The R6 composition remains only as a
historical parity oracle for its frozen tests; the production-entrypoint bypass gate rejects imports
of that composition, the private planning use case, the SQLite planning adapter and the raw writer.

## Sources and decisions

- Product authority: `VISION.md`, version `2026-08-22.2`.
- Runtime authority: `design-docs/project-architecture/NORMATIVE.md`, especially the owner boundary and runtime
  generation contract at lines 97–115.
- Scope and order: `IMPLEMENTATION-ROADMAP.md`, R7.1–R7.7 and the post-R6 compatibility ledger.
- Predecessor seams: `R1-R2-CONTRACT-FREEZE.md`, `R4-R5-CONTRACT-FREEZE.md`, and
  `R6-INTEGRATION-DECISION.md`.

Inspection resolves the transport question: the public contract is transport-agnostic and carries
canonical inert bytes; local allowlisted IPC is an implementation constraint. Pydantic DTO code is
owned by planning and is not shared executable policy. Dishka is likewise a graph-realization
mechanism and does not appear in public signatures.

## Contract ownership

- `chiplog.capabilities.planning.r7_boundary` owns `R7PlanningCreateDTO` and
  `R7PlanningResultDTO`; the frozen R4/R5 package-root export set is unchanged.
- `chiplog.platform.broker` owns broker session, budget, call/result/failure values and
  `AuthorityBrokerPort`.
- `chiplog.composition` owns `OwnerGeneration`, `RuntimeGraphGeneration`, and `R7GraphBuilder`.
- `chiplog/inert_shared/r7-planning-v1.json` is non-imported inert schema input; it owns no policy.

The DTO payload contains no repository, connection, transaction, adapter, SDK, proxy, container,
callable, or foreign private type. Calls bind tenant, broker epoch, runtime generation, owner and
session for both caller and expected callee, plus operation, request, schema and finite positive
budget/deadline. Failure variants are closed and success/rejection cannot coexist.

## Acceptance criteria for this freeze

1. A consumer imports every contract only from the intended public package surfaces.
2. The planning request accepts exact strict values, is immutable, and rejects unknown or coerced
   fields.
3. The broker call shape can express owner/generation/session fencing, canonical payload identity,
   finite call/depth/deadline budget, and typed failure.
4. Owner processes receive no raw authority handle and attest an empty environment plus denied raw
   filesystem/network acquisition before generation admission.

The R7 Stage-0 slice is promoted as one indivisible R7.1–R7.7 increment; R8 remains `HOLD`.

## R7.1 executable freeze

`chiplog.architecture.r7_compatibility` derives the exact predecessor universe from the frozen R1
signature set and R4/R5 export, schema, record, provider and sink sets, then adds the named R1–R6
evidence fixtures and realized R6 composition bridges. Every predecessor has exactly one disposition,
successor owner and successor evidence identity; missing, duplicate and orphan rows reject.

The first parity case freezes canonical input for `planning.create_intention_line`, all observable R6
outcome branches, and the three byte-level comparison surfaces: durable records, committed result and
restart projection. The executable parity test builds independent R6/R7 stores and compares the full
durable record rows and rendered output.

## Implemented runtime evidence

- One spawned Dishka `APP` container and authenticated session exists per immutable owner; exact
  broker-to-public-port request and result schemas are checked against the closed manifest.
- The owner profile clears its environment, closes the listener after authenticated connection, and
  installs a deny-raw audit boundary for filesystem, new socket, subprocess and dynamic-library
  acquisition. Attestation actively probes filesystem and network denial.
- The broker owns R4 trust, SQLite, `EventAppender`, durable epochs and operation/read ledgers.
  `IDEMPOTENT_EXACT` issuance and the unique response slot commit before writer execution.
- Authority reads bind an independently recomputed full snapshot to a separate HMAC-authenticated
  materialization commitment journal, physical schema/file observation, prepared-query edge and
  bidirectional invalidation registry; release is one ordered ledger cut.
- Restart rotates epoch/key/endpoint, closes the prior generation, reduces issued work to durable
  uncertainty, and starts fresh owner sessions. Shutdown closes admission, drains accepted calls and
  tears owners down in reverse manifest order.
- Production/evaluation manifests preserve broker binary, security profile, application loop, owner
  partition and capabilities; only registered same-owner leaves differ.

## Requirement-to-evidence map

The R7 promotion decision is intentionally separate from implementation. The rows below name the
executable evidence that must remain green before the registry can move from `HOLD` to `PASS`.

| Requirement | Observable evidence |
| --- | --- |
| A09, A66, A78, A82 | `test_runtime_manifest.py`: production/evaluation runtimes realize the same broker, security profile, application loop, owner partition and capabilities, with only registered leaf substitutions. |
| A14, A65, A73, A80 | `test_runtime_manifest.py`, `test_broker_routing.py`, `test_owner_process_isolation.py`: the exact manifest rejects omissions, additions, cycles and raw capabilities; process attestations and routed schemas reconstruct the realized graph. |
| A20, A67, A74, A79, A81 | `test_runtime_restart.py`, `test_owner_process_isolation.py`: durable epoch is distinct from APP scope; every owner has one process/session/generation; quarantine precedes admission; restart rotates workers and reconciles issued work; teardown is reverse ordered. |
| A23, A68, A71, A75 | `test_authority_ledger.py`, `test_r7_planning_runtime.py`: only the broker holds the writer; owner-mediated trust is revalidated at commit; tokens are durably issued before execution and terminal dispositions never re-execute. |
| A69, A70, A83 | `test_owner_process_isolation.py`, `test_inert_owner_models.py`: each fresh owner process loads only its own policy module, clears its environment, denies raw OS acquisition and exchanges strict canonical inert values. |
| A72, A76, A77 | `test_authority_reads.py`, `test_read_release_order.py`, `test_read_invalidation_manifest.py`: reads are bounded by two independently observed points, invalidation races suppress stale bytes and fresh generations reconcile pending work. |
| A84 | `test_broker_routing.py`, `test_r7_public_boundary.py`: finite depth/deadline budgets and exclusive-resource holds reject before delivery; outcomes are closed and generation fenced. |
| R7.1–R7.7 compatibility | `test_compatibility_ledger.py`, `test_r7_planning_runtime.py`, `test_r7_bypass_gate.py`: the predecessor ledger is exact, R6/R7 durable bytes and rendering match, and direct, transitive, relative and dynamic old-path bypasses are rejected. |
| Stage-0 V0 predecessor | `test_invariant_registry.py` and `test_profiles.py`: the promoted normative set remains exactly A01–A108 and the closed verification registries match it. |

Promotion passed the full repository gate and cold, no-parent-context audits with no ratified P0/P1
findings. Referentially closed bounded pagination covers exact-five manifests, duplicate-sequence
rejection, page boundaries, and the 1000/1001 predecessor boundary.
