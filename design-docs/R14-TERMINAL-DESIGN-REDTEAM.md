# R14.6 historical accepted-terminal suffix design red-team

Scope: roadmap lines 83–187; accepted direct terminal publication, native history/accounting source, native suffix verifier and relevant tests. No runtime/tests modified; no production calls. Cold reviewer did not receive author context. Same-model analysis is not external verification.

## Protocol and evidence boundary

Plan: map promises to runtime checks, close the mechanism attack inventory, run isolated discriminators, preserve unverified mounted scope. Assumptions: current implementation is the target; physical-ahead commitment recovery is an admissible interrupted-publication state. A cold no-parent-context plan agent returned no critical plan flaws.

Phase 1: prove exact physical suffix integrity independently of each terminal's historical semantic prefix, while admitting registered per-member/per-seal interleavings. Out of scope: other R14 milestones, performance and production execution. Phase 2: internal integrity critic, causal-order critic and admissible-interleaving defender; these are analytical perspectives, not independent code reviewers.

Iteration 1 / E: read roadmap, actual runtime callback, suffix verifier and tests. Distinction established: native suffix semantic checking is conditional on physical commitment differing from commitment-journal anchor; normal committed reads do not necessarily exercise it.

Iteration 2 / E: isolated Python probe with mocked retained-terminal/owner-issuance decoding confirmed that a later valid-issuance intent B is rejected when allowed intents contain only seal A. Checked exact registered operation set: OPEN accounting, OPEN recovery and cancelled accounting are absent. Probe command was `.venv/bin/python -` using `unittest.mock.patch` and SimpleNamespace; no persistent state.

Iteration 3 / G: cross-seal and missing-family routes remain ACTIVE as P1 mechanism mismatches. Physical integrity and CLOSED-locator routes are SUPPORTING, with no verified acceptance bug found. No P0 found. Helper evidence does not close mounted runtime guarantee; root must mount proposed boundary tests. Pre-mortem: callback may be bypassed by normal committed state; another seal might really be foreign; mocked owner decoding cannot prove dispatch viability. These are bounded explicitly below. H: bounded-incomplete close because no full mounted interruption test ran here.

## P1-1: native semantic suffix rejects legitimate same-Run cross-seal effects

Type: verified code/helper issue; end-to-end interrupted recovery is unverified.

Promise: roadmap permits valid publications from different members or seals to interleave, while rejecting cross-seal substitution of a terminal's own sources.

Mechanism: `r14_native_selected_accounting_source.py:1151` builds `seal_call_ids` only from `accepted.selected_seal.initialized_records`; :1154 filters `accepted.source.target_inventory_rows` to those calls. `r14_historical_suffix.py:330` checks every later owner decision against that set and :340/:344 rejects a dispatch or outcome belonging to any other seal. This conflates allowed unrelated history with substitution into the target terminal.

Concrete input: direct terminal A from seal A at sequence n; later authenticated dispatch/evidence for member B from distinct seal B in the same Run; an interrupted materialization leaves current physical commitment ahead of the independent anchor. Historical read of A selects native suffix authentication and then rejects B as “belongs to another seal”. The isolated semantic probe returned `native terminal suffix dispatch belongs to another seal` with a valid-issuance stub for intent B.

Runtime anchors: `r16_effects.py:495` invokes suffix callback only for commitment mismatch; `r14_native_selected_accounting_source.py:1195` selects the native verifier; :1213 invokes its semantic check once native authentication succeeds. Thus ordinary committed reads are not claimed to fail.

Correct repair seam: collector :1559 enumerates all same-Run seals; :1613 builds all same-Run target rows; :1620 enforces exact initialized/observed row membership; :1634 maps each seal's own rows. Carry an authenticated per-call effect/seal relation over the captured history, admit B against B's own acceptance and seal, and continue binding A's terminal/locator only to A. Merely deleting membership checks would weaken substitution protection.

Red-test proposal: mount two accepted seals in one Run, retain terminal A, add valid B dispatch/evidence, interrupt at physical-commit/anchor boundary and reopen/read/replay A with no head advance. Require success. Replace B issuance source with A's acceptance or an unselected/foreign seal and require HOLD with no extra calls. Include both authorize and outcome operations. Current two-seal test starts with a cancelled first seal, so it cannot prove accepted A's historical suffix semantics.

## P1-2: registered existing terminal families are missing from the native suffix closure

Type: verified allowlist/coverage issue; mounted interrupted-publication counterexample unverified.

Promise: roadmap requires accepted/cancelled histories and existing per-call terminal publications, including admissible recovered outcomes, to compose before per-seal V3.

Mechanism: `r14_historical_suffix.py:26` registers only direct terminal and V3 loop publications. :103 collects only these into `steps`; :150–152 requires every later tenant sequence to be present. Existing `agent_loop.accepted-open-accounting.v1`, `agent_loop.accepted-open-recovery.v1` (`r14_accepted_open_accounting_records.py:21/:24`) and `agent_loop.native-one-call-cancelled-accounting.v1` (`r14_native_accounting_records.py:31`) are absent. A valid later publication in any of these families leaves a hole and rejects the native suffix. Unknown families should HOLD; these are known required families, not proposed extensions.

Concrete input: terminal A selected; sibling B publishes admissible RECOVERY_REQUIRED accounting then recovery, or selected cancelled accounting; current physical state is ahead of journal anchor while historical A must be reconstructed. Even if every physical row/envelope is exact, `steps` omits B's loop sequence. The membership probe printed `registered=False` for each family.

Red-test proposal: mount direct A plus OPEN/recovered B and cancelled C; interrupt after each registered publication's physical batch before anchor completion; require historical A and per-seal V3 reopen/replay to succeed when causal prerequisites hold. Reorder B recovery before its OPEN cause, substitute call/seal bindings, or use an unknown family and require HOLD. Authenticate envelopes without source-reconstructing validators inside the suffix verifier, preserving the no-recursion contract.

## Mechanism inventory

1. Omission: exact `range(target+1, head+1)` compared to selected and physical publication sequences rejects missing sequences; P1-2 identifies omission of legitimate registered families from enumeration. Discriminator: mounted omitted-middle and known-family recovery cases.
2. Addition/extension: physical sequence membership and exact per-sequence row tuple reject extra rows; owner operation allowlist rejects unknown families; unsupported loop rows produce coverage mismatch. Discriminator: extra physical orphan or unknown family after A. Existing generic suffix tests are supporting evidence, not mounted native guarantee evidence.
3. Duplication/reordering/conflicting identity: duplicate step sequence rejects; exact ordered row tuples bind batch order; terminal validator enforces `(response_id, original_call_id)` uniqueness before filtering. Discriminator: duplicate marker or reordered physical companion.
4. Substitution/aliasing/cross-owner: retained command equality and exact CLOSED locator bytes/decision/sequence check protect own terminal; P1-1 shows overrestriction of unrelated valid seals. Discriminator: valid B versus B-with-A-source at mounted boundary.
5. Stale/race/collision: authority gate, database identity, snapshot/journal checks and publication guards bind live state; separate snapshots still taken inside native verifier rather than passed one captured history context. No race demonstrated under the gate; no additional P1 assigned without a counterexample.
6. Implicit default/fallback: resolver-prefix verifier precedes native fallback. Ordinary anchored reads bypass suffix authentication; report does not infer suffix failure for those reads. Discriminator: force physical-ahead boundary instead of helper-only assertion.
7. Lifecycle: historical CLOSED locator constructs `latest_at_cut` and sequence-bounded prefix (:1354–1403) while retaining full effects physical cut. Three-member mounted test :1095 covers delayed dispatch, but outcomes are SUCCEEDED/FAILED_DEFINITE/SUCCEEDED, and two-seal test :1224 starts with cancelled A. Accepted cross-seal and OPEN-family interrupted reopen remain unverified.

## Promise versus demonstrated guarantee

Physical native suffix mechanism reconstructs exact contiguous commitment chain and complete selected batches in a private database snapshot. Historical CLOSED reader joins its exact retained locator and excludes later records from closure prefix. This review found no supported claim that these checks accept corruption. The stronger composition guarantee does not stand for valid registered OPEN/cancelled families and same-Run cross-seal history at the conditional recovery boundary until the proposed mounted red tests and narrow repairs pass.

Owner: root implementation agent. Next action: mount the two bounded red scenarios, repair authenticated relation/family closure, rerun negative substitution/orphan cases. No all-R14 readiness claim; no full regression performed by this reviewer.
