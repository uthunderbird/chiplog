# R14–R17 shared contract baseline

Status: candidate baseline; consumer checks in progress. R14–R17 implementation,
integration, recovery convergence, deployment and per-milestone retros remain OPEN.

## Plan and assumptions

This is the shared preparation slice requested before parallel R14–R17 work. Base:
`6f34dafc750ba894bbc7dd8a5a2659f61c87675e`, including test acceleration. Separate branches
`feat/r14` through `feat/r17` retain their implementations and requirement/evidence maps.
User-owned TRANSCRIPTS.md and drafts remain only in the original master worktree.

Freeze strict public owner DTOs and private broker wire shapes; validate them from
consumer imports; review exact source-catalog additions; run preflight, lint/types,
full tests and affected verification; perform one cold staged-diff swarm-red-team;
present the handoff and request explicit approval before committing this slice.
The initial coordination plan was independently reviewed without parent context;
file-budget, shared-patch, downstream-coverage and retro-evidence gaps were corrected.

The slice is contract preparation, not a runtime recovery implementation. Root owns
shared changes and distributes exact hash-checked patches. Each worktree budget counts
all dirty paths, including shared files/tests/reports/retro: target35, hard40. This
baseline creates a common commit boundary so combined work does not silently exceed it.
No commit authorization, external dispatch or deployment entitlement is inferred.

## Ownership and surfaces

| Owner | Contract surface | Boundary |
|---|---|---|
| agent_loop | recovery_contracts.py, recovery_frontier_contracts.py | exact tagged lineage/fences, complete terminal/pending call observations, immutable baseline, original obligation references, resume/successor |
| agent_loop | scheduler_contracts.py | revision-bound intervals, complete eligibility and bounds, overflow resolution, lease/epoch proposals and observations |
| agent_loop | contracts.py SchedulerRootReference | real RunRecord can carry immutable original tagged subject bytes/root fingerprint; legacy default remains NOT_APPLICABLE |
| agent_loop | delivery_contracts.py | accepted deterministic delivery manifests, exact origin/model-selected endpoint, prepared effect members for atomic completion |
| effects | contracts.py, fences.py | full dispatch lifecycle and semantic version tuple, acceptance/authorization/send/evidence/reconciliation/compensation, owner-local fence representation |
| broker | platform/_ingress_contracts.py | loss-slot/raw custody, source-specific authentication references, bounded FIFO/reserves/rebase/drain, page and quarantine observations |
| broker | platform/_owner_publication_contracts.py | closed operations, exact original-command replay lookup, single-owner and explicit Plan/effect, call/effect, completion/delivery invariant batches |

Capability code never imports the broker-private publication port. Effects mirrors
the inert fence wire shape through its own types; mechanical bridges must preserve
every field. It does not import another owner's executable model. Delivery attempts,
receipts and reconciliation retain effects ownership; agent_loop does not add a
competing transport reducer. Ingress authentication remains broker/deployment-trust
responsibility. An object with a trusted-sounding discriminator proves no authentication.

## Structural guarantees and evidence

| Claim | Owned data/decision | Observable | Forbidden substitute | Boundary mutation | Consumer test |
|---|---|---|---|---|---|
| Closed applicable fence | agent_loop tagged immutable wire | six disjoint variants parse/roundtrip | nullable catch-all fields | unknown/hybrid/foreign epoch, missing proof | test_recovery_contracts.py |
| Stable versus physical identity | immutable Run reference and tagged subject | exact subject bytes retained; epoch excluded from stable ref | physical epoch as root or cross-tag alias | bad/aliased base64, wrong domain/version | test_recovery_contracts.py |
| Complete branch representation | terminal and pending DTOs | required lineage/counter/result/obligation fields | last attempt as complete call | omitted heads, pending plus terminal, TRANSFER_OPEN | test_recovery_frontier_contracts.py |
| Bounded numeric wire | strict uint64 lease/generation fields | reject negative, overflow, bool and string | wrap/coercion | N/N+1 and type substitution | test_recovery_contracts.py |
| Version-bound dispatch | immutable five-component DispatchSemanticBinding | all components mandatory | compatible-version hint | each version omitted/extra, mutable field | effects/test_contracts.py |
| Exact inert bytes | registered base64 JSON codec | non-UTF8 roundtrip | lossy UTF-8 decoding/digest-only custody | arbitrary byte values | effects/test_contracts.py, test_delivery_contract.py |
| Atomic multiowner representation | explicit broker batch union | acceptance/Plan/completion cannot use single-owner operation | generic arbitrary owner records/callback | unknown operation/single-owner alias | test_owner_publication_contracts.py |
| Exact origin selection | accepted delivery union | ORIGIN binds ingress; MODEL_SELECTED is distinct | silent fallback/reconstructed recipient | missing binding, hybrid fields | test_delivery_contract.py |
| Scheduler proposed batch shape | policy/cardinality/lease/result DTOs | positive materialization/resolution/rollover consumers | untyped aggregate/member identity | missing heads, missing rollover edge | test_scheduler_contracts.py |

Structural checks do not prove semantic cardinality, authenticated issuance, current
heads, canonical namespace derivation, actual emitted bytes, complete runtime registries
or atomic publication. These remain milestone runtime obligations. Race, omission,
addition, substitution, duplicate/reorder, N/N+1 and logical/physical identity mutations
remain required in actual broker histories; no family is waived by this baseline.

Clock proof and rollover proof use explicit acyclic payload domains excluding their
self-covering proof slots; final command fingerprints include the immutable proof.
Rollover payloads retain authority head, command identity, predecessor decision/edge
and exhaustion authority epoch. Excluding the entire authority object would omit
semantic authorization scope. Canonical
tagged recovery JSON places discriminants first, registry manifests retain explicit
ordered arrays. Rollover predecessor binds both decision and edge. Resolver version is
distinct from reducer version. Read-only terminal observations retain the complete
original attempt lineage/counter and complete-lineage reduction batch.

## Remaining integration obligations

R14 A99 cannot close until all executable R15–R17 writer paths are independently
enumerated and compared bidirectionally. R17 ingress coverage must exercise actual
CLI, Telegram push/poll, provider callback/poll, reconciliation and tool-result paths,
with zero dashboard ingress. An earlier core baseline is not final convergence.

The broker authenticates invocation and prepares isolated-owner outputs before taking
the writer lock. Inside the transaction it compares exact complete authoritative
observations, issuance, current fences and versions without cross-owner calls/awaits.
Replay authenticates the current caller but compares immutable original command bytes;
it never regenerates historical owner outputs or demands an old lease remain live.
Actual runtime implementations and these reached histories are outside this slice.

Each milestone still requires its full roadmap DoD and a separate retro through the
retro skill (unique ID, preserved script inputs, current-diff evidence where needed,
swarm, cold review and justified harness outcome). Retro completion requires its
authorized commit; this common preparation commit does not replace any of them.

## Validation record

`uv run pytest -q`: 526 passed, 406 outer Stage0 cases delegated with child coverage
confirmed. After the required effects blocker-inventory field was added, its affected
contract selection passed (16 tests); `sh .harness/scripts/lint.sh` passed Ruff,
formatting and mypy. `sh .harness/scripts/test.sh --preflight` and
`python3 .harness/scripts/checks/tests_layout.py --worktree` passed.

`uv run python -m chiplog.verification stage1`: PASS, artifact
`.artifacts/verification/1cb4ee980974e9fd0085917fcd96141ab40735ca66c1297b01b2bd7e77d7f68d.json`.
Subsequent edits only clarified the rollover-proof docstring and updated its reviewed
catalog hash; recovery contract and current-surface checks passed again (21 tests).
Cold staged review is pending. Hashes identify reviewed source bytes, not behavioral
evidence; no runtime or deployment permission follows from these results.

The closed scheduler operation set also includes atomic configuration genesis,
schedule amendment and policy amendment, matching the independently reviewed owner
configuration plan. After this addition, affected publication/scheduler/current-source
checks passed (13 tests). These names do not register executable runtime handlers.

The gate required a cadence retro before this preparation commit. Its five-lens
swarm and separate cold draft review are recorded in `.harness/retro/2026-09-20.md`;
final classification is `skip` with explicit evidence limits and existing controls.
It still requires the authorized commit, and replaces none of the four milestone retros.
