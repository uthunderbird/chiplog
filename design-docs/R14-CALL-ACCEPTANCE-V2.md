# R14 consequential acceptance with R16 v2 effects

Status OPEN. Full objective remains all requirements in R14-R17-COMPLETION.md.
This increment closes actual consequential acceptance, not all continuation,
scheduler, post-terminal recovery, dispatch retry or ingress requirements.

## Context and ownership

Published base bf42f45 has executable Run/tool/parser and isolated preparation.
R14 publication is staged in chiplog-r14-execution-publication (profile 12).
Repeated-ingress repair is staged in chiplog-repeat-ingress. R16 outcome work has
an active normal full commit gate in chiplog-r16-completion. Preserve every frozen
tree; do not edit those indexes/source bytes or start another full gate meanwhile.

Read sources: call_acceptance_contracts/preparation, execution_contracts,
r14_acceptance_contracts/records (legacy effects v1), r14_call_acceptance_port/preview,
effects/dispatch_v2_contracts and dispatch_v2, r16_dispatch_publication/authority/
history/inputs/registry, platform CallEffectBatch and owner publication journal.
The current loop producer is effects-version-neutral. The physical verifier is
v1-specific and must not silently reinterpret old retained data as v2.

Agent loop owns accepted/execution records. Effects owns the initial v2 intent
record. Deployment trust authenticates actual adoption. Broker captures sources,
retains original exchanges and performs one selected journal + physical batch.
The accepted response seal and original initialized record already exist.

## Plan

1. Under new-milestone, freeze separate v2 retained acceptance and physical envelope
   contracts plus public consumer shape checks. Preserve all legacy classes, digest
   preimages and schemas. Artifact includes this evidence map and pending criteria.
   Run preflight before broad checks and after contracts/entrypoints change.
2. Implement independent v2 envelope verification using original requests/results,
   without calling owner producers. Reuse version-neutral loop preparation through
   its isolated route; do not fake v1 effects to reuse the old verifier. Verify exact
   three-member physical order: accepted, execution intent, effects DispatchRecordV2.
   The semantic third reference is immutable ExternalActionIntentV2, not its record.
3. Add a separately versioned closed call policy. Existing hermetic PlanEffect v2
   policy explicitly lists only PUBLISH_PLAN_EFFECT/AUTHORIZE_SEND/COMMIT_FIRST_SEND;
   do not add acceptance to its old bytes or renew historical mandates. New policy
   has exact self recipient from retained independent resources, payload/bundle from
   initialized request_self_effect v2, fresh original horizon, complete disclosure
   and source roles. Effects supports the exact registered policy selected by origin;
   never accepts caller-selected arbitrary semantics or a permissive policy union.
4. Implement actual peer-authenticated preview and accept_call. Preview verifies
   selected original initialization, complete current inventory, registered tool
   and current active execution Run. Store exact display/mandate and precursor result
   before adoption. Adoption binds exact bytes and actual invocation principal;
   public DTOs never supply source observations, worker credentials or authority.
   Stable act replay is tenant/principal/operation/act_id and returns the original
   selected receipt before any attempt to acquire a fresh mandate. Changed bytes
   under that identity conflict. Unknown selection remains recoverable uncertainty.
5. Bridge independent source acquisition to executable Run schema through explicit
   schema routing. Extend actual worker registry rather than treating a constructed
   WorkerFence as proof. New combined runtime profile/factory composes registered
   executable loop and original dispatch resources; old profiles and fixture driver
   identities remain unchanged. No live model/provider or external sending.
6. Under the shared canonical gate and sole writer, recapture exact tenant cut,
   current Run/head/session, source policy/authority/disclosure, resource identity,
   original initialization and acceptance branch. Select only if original branch is
   INITIALIZED. Publish three owner records atomically through CallEffectBatch and
   retain original owner command/result/session evidence for recovery. Provider I/O
   never occurs in this path. Canonical history independently joins accepted,
   execution and immutable external intent; no embedded terminal result shortcuts.
7. Extend cancellation for executable Runs and both winning orders. When acceptance
   wins, later cancellation cannot yield NOT_EXECUTED. When cancellation wins,
   acceptance cannot publish effects. Shared inventory uses the same actual branch
   CAS. A changed current head of the same original Run is allowed only after fresh
   validation while retaining original identity. Different successor Run ownership
   requires a selected adoption/lineage proof, never an arbitrary ACTIVE Run; add
   that registered join with the successor increment if no proof exists yet.
8. Connect accepted v2 intents to R16 original-current input, history, authorization,
   one-shot SEND and outcome readers under the new closed call policy. Acceptance
   alone remains no SEND authority. Prove real canonical history/recovery, no effect
   before acceptance, zero partial physical membership, original selected recovery
   without owner reissue, and late/cross-operation replay. Track scheduler acceptance
   as still required until its authenticated lease path is implemented; do not infer
   it from non-scheduler producer success.

## Assumptions and unresolved details

- Hermetic tests only; existing authorization covers commit/merge/push; no retro.
- Distinct worktree for this increment; other prepared work keeps its frozen bytes.
- At most 40 changed paths, target below 35 including catalog/tests/doc/review. Split
  contracts+verifier and actual runtime into semantic increments if necessary; the
  final acceptance obligation stays open until the public runtime path is proven.
- Original initialized tool arguments are exact canonical SelfEffectArguments bytes;
  separately validate whole sealed ConsequentialToolCall bytes and exact tool/schema.
- Bundle members are opaque labels of this single inseparable payload, NOT references
  to planning entities, external facts or independent grants. Freeze this meaning in
  the new call policy. Preserve all original labels and order with no normalization.
  Require 1..32 unique nonempty labels, each at most 4096 UTF-8 bytes. For each ordinal
  starting at zero, define the canonical member body as effects sorted UTF-8 JSON
  with schema_id="chiplog.call.bundle-member.v1", original_call_id, ordinal and label.
  Its subject is original_call_id + "/bundle/" + decimal ordinal; the ExactHead is
  effects.reference(subject, canonical body), committing body bytes via SHA-256.
  This is an owner-interpreted reference to retained original call content, not an
  assertion that a corresponding physical record or external authority exists.
  Mandate.origin.sealed_arguments retains all input labels, allowing an independent reader
  to rebuild and compare the complete ordered member list. Cross-call substitution,
  changed label/order, duplicate labels and N/N+1 bounds reject. No missing member
  may be dropped and no unrelated scope may be inferred from a label or hash.
- Acyclic order: original initialization -> preview/mandate -> precursor/adoption/
  acquisition -> immutable intent -> loop accepted/execution -> effects initial
  record -> physical batch. None contains its own resulting record digest.
- The profile and source-bound catalog changes require exact inventory/module closure
  checks. Historical R14/R16 interpreters must reject unknown new schemas, not guess.
- Source policy membership, executable worker capture and current-versus-original
  ownership are implementation obligations, not guaranteed by any DTO field.

## Guarantee preflight and observable evidence

| Claim | Owned decision/data | Independent observable | Forbidden substitute | Boundary fixture | Planned evidence |
|---|---|---|---|---|---|
| Original call binding | owner initialized record, sealed bytes and captured response | selected fanout and exact origin equality | bare model call label/current Run identity | cross-call, original/current head swap, changed tool/schema/arguments | v2 envelope and real preview negatives; HOLD |
| Exact adoption | deployment trust invocation plus original retained display/mandate | actual peer/tenant/principal mismatch rejects at writer | DTO construction, content hash alone, refreshed horizon | changed display/act/peer, stale issuer/resource | actual public adoption; HOLD |
| Complete atomic acceptance | two loop records plus effects v2 initial record | exact independent journal and physical SQL membership | v1 synthetic owner result, partial companion set | omit/add/duplicate/reorder/substitute/crash | publication/readback and restart; HOLD |
| Closed semantics | separately registered call policy and five semantic components | source registry plus independent literal/schema checks | rewriting PlanEffect v2 semantics or arbitrary policy object | unknown profile/component/schema, old driver | compatibility and dispatch rejection; HOLD |
| Current authority/CAS | independently observed worker/current Run/source cut | mutation after IPC writes no acceptance | supplied fence, stale proof, empty inventory | both cancel/accept orders, worker/head/lease/source mutation | writer-boundary race observers; HOLD |
| Original recovery | selected original command/results and act identity | crash replay recovers identical bytes and no owner/provider calls | regenerate proposal or new acquisition | before/after journal and SQL selection, expired replay, foreign actor | actual reopen/replay; HOLD |
| Bounded complete envelope | exact ordered physical members and all nested retained bindings | serialized physical batch size and referential closure | call count or payload-only bound | N/N+1 size, omitted bound metadata, duplicate bundle | boundary consumer and runtime; HOLD |

All mutation families apply: omission, addition/unknown value, substitution/alias,
duplicate/reorder, stale/race, N/N+1 and logical/physical identity. No N/A dismissal.
Cold plan review precedes implementation. Root owns the result and independently
inspects actual delegated findings; reviewers do not own implementation.

## Contract evidence

The cold plan finding about undefined bundle referents was corrected and rechecked
with no remaining P0/P1 (`/tmp/chiplog-acceptance-v2-plan-cold.md`). This is a plan
review, not implementation acceptance. The new public retained preparation carries
the complete effects request, including original current observations, and both
owner outputs. The distinct physical envelope preserves legacy interpretation.
Its shape fixes three members but deliberately does not authenticate them or prove
their semantic membership; the independent verifier remains required.

`uv run pytest -q tests/composition/test_acceptance_v2_contracts.py
tests/composition/test_acceptance_publication_contracts.py`: 4 passed. Mypy for the
new contract and consumer and ruff passed. The consumer checks complete retained
input shape, canonical envelope roundtrip, legacy rejection, forbidden permission
fields and 2/4-member rejection. Initial preflight failed in a fresh environment;
after environment setup and exact admission of the new source, the explicit gate
self-test and `sh .harness/scripts/test.sh --preflight` both completed with exit 0.
No gate or reproducer was weakened. Runtime guarantees in the table remain HOLD.

## Independent structural verifier

The new closed call policy fixes exact self-recipient scope and original ordered
bundle derivation without changing PlanEffect policy bytes. The v2 verifier checks
the original call against mandate payload/bundle, full registered display, original
acquisition digest graph, both owner results, current observation bindings, initial
effect record preimage and the complete physical envelope. It never calls the owner
reducers. The old loop link verifier is shared with explicit registered semantics;
the v1 entrypoint still supplies exactly its original semantics.

`uv run pytest -q tests/composition/test_acceptance_publication_records.py
tests/composition/test_acceptance_publication_contracts.py
tests/composition/test_acceptance_v2_contracts.py
tests/composition/test_acceptance_v2_records.py
tests/composition/test_call_dispatch_policy.py
tests/architecture/test_r8_current_surfaces.py tests/verification/test_r8_surface.py`:
135 passed (log `/tmp/chiplog-acceptance-v2-verifier-regression.log`). Whole-tree
mypy passed for 466 files; preflight passed. Fixtures use actual pure owner outputs
and include fully recomputed malicious bundle/display graphs, not only stale hashes.
Neither fixture nor verifier authenticates selected history or resource custody.

Remaining: cold staged review, normal commit gate, actual private preview/adoption
issuance, original initialization Run-head proof, complete authenticated owner/source
inventory, writer CAS, cancellation races, history joins and dispatch integration.
Structural success is not publication permission or completion of acceptance.

Cold staged review found two missing decidable predicates: equal cross-owner
materialization commitment and the exact registered clock contract. Both attacks
used recomputed valid owner outputs. Added lasting tests first; both failed before
the repairs (`/tmp/chiplog-acceptance-v2-cold-repro-before.log`). The verifier now
checks both predicates. The positive fixture now explicitly uses the registered
clock instead of inheriting a generic preview fixture clock. All 19 v2 record tests
passed after repair (`/tmp/chiplog-acceptance-v2-cold-repair.log`); affected mypy and
whole-tree ruff passed. Revised staged cold review remains required.
