# R14 verification status

Status: OPEN, 2026-10-15. Acceptance authority:
`R14-REMAINING-ROADMAP.md` and original clauses in `R14-R17-COMPLETION.md`.
This is an evidence ledger, not a replacement acceptance boundary.

## Current R14.6 audit

| Requirement | Existing evidence or candidate check | Current conclusion / next action |
| --- | --- | --- |
| Mixed accepted/cancelled seal and per-seal V3 | `test_v3_mixed_seal_publishes_reads_after_reopen_and_replays_once` | Existing mounted baseline; retain, then verify current integrated tree. |
| Two seals in one Run, target-scoped terminal and Prepare | `test_v3_accounts_two_sequential_turn_seals_after_reopen_and_cross_order_replay`; `R14-MCP-DEBUGGING.md` | Corrected original selector passed under diagnostic clocks; production admission budgets and accepted cross-seal interrupted recovery remain unverified. |
| Per-seal replay and reopen without head advance | Same two-seal selector | Both V3 records, cross-order replay and reopen/head invariants passed in the corrected diagnostic run. |
| Three accepted members with distinct dispositions | `test_v3_reads_three_accepted_members_across_delayed_dispatch_and_replay`; owner `test_v3_accounts_three_accepted_members_with_distinct_existing_outcomes` | Frozen mounted diagnostic-clock selector passed with SUCCEEDED / FAILED_DEFINITE / RECOVERY_REQUIRED and delayed dispatch; separate same-fixture V3 reopen/replay also passed. Candidate remains outside the integrated checkpoint; production-clock verification is still required. |
| Dispatch after another member terminalizes | Three-member mounted selector closes first before accepting second | Frozen distinct-outcome selector passed with delayed dispatch. Integrating that candidate and verifying production admission remain required. |
| Multi-seal member interleaving | Two-seal selector | Sequential Turns alone do not prove all required member interleavings; inspect exact observations before claiming closure. |
| Complete physical membership / orphan detection after reopen | Mixed-seal selector has physical orphan damage; `test_r14_loop_history.py` | Existing candidates; inspect mutation scope and verify mounted rejection on current tree. |
| Cross-call / cross-seal evidence substitution | Owner duplicate/cross-seal test; two-seal source rejects foreign call lookup | These narrow checks do not prove mounted accepted-evidence substitution rejection. Add discriminating test. |
| Omitted sequence, broken chain, unknown family, foreign closed locator, later evidence excluded from earlier semantic prefix | `r14_historical_suffix.py`, direct-terminal historical/frontier tests | Cold design mechanism audit in progress; identify coverage and add red tests for confirmed gaps. |
| Three independent A54 N/N+1 limits | `test_call_and_manifest_bounds_include_exact_boundary`, `test_response_and_complete_owner_output_boundaries`, new `test_complete_physical_envelope_enforces_its_own_exact_byte_boundary` | Owner and physical builder boundaries verified in focused tests. This does not prove mounted sole-writer admission/reopen for each limit; preserve that scope in final audit. |
| No partial companions after rejection/interruption; no preaccept external call | Branch-competition and fanout mounted suites | Coverage audit and current execution needed. |
| Per-slice writer registration and durable-version admission | `r7_runtime.py` V39 manifest; publication/readers | Verify exact registered routes and physical schemas, including negative unknown versions. Registration presence alone is insufficient. |
| Production admission deadlines | Diagnostic clock deliberately scales `monotonic_ns`; production clocks unchanged | Relaxed tests prove function only. Separate real-clock mounted verification is required before closure. |

## Remaining scope

R14.7 through R14.13 remain OPEN. User delegated the read-only bound choice on
2026-10-15; selected `max_attempts=3` includes the initial attempt. Registered
policy version, allocation, retry CAS, source authentication and mounted
successor/reopen checks are still required. Preserve dependency gates in the
roadmap; proceed on independent accepted/cancelled branches where applicable.

Full completion additionally requires both continuation reduction gates, total
frontier with nonempty continuation preservation, writer fencing inventory,
durable model-attempt recovery, original-stream post-terminal work and full
normative evidence audit. Final integrated verification and cold design review
cannot be replaced by checkpoint tests.

## Commands used for this audit

`rg -n '^def test_|^async def test_'` on the named test files locates candidate
checks; reading their bodies determines their scope. Candidate existence does
not mean a passing runtime result. Actual executed results belong below or in
the debugging report, with their commands and clock conditions.

## Current focused results

`PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/capabilities/agent_loop/test_native_sealed_accounting_v3.py tests/composition/test_r14_native_sealed_accounting_records.py tests/composition/test_r14_accepted_direct_terminal_history_reuse.py`
returned `17 passed in 1.12s` with normal clocks. Scope is owner contract,
record/envelope binding and isolated terminal-selection checks, not full mounted
R14.6 completion.

Cold `$swarm-red-team` report: `R14-TERMINAL-DESIGN-REDTEAM.md`. It found two P1
composition failures at the physical-ahead commitment recovery boundary. The
cross-seal caller-boundary regression first failed with only `effect-first`
allowed, then passed after collecting intents from all authenticated same-Run
seals. `PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/composition/test_r14_native_selected_accounting_fast_path.py`
returned `2 passed in 0.91s`. This is a focused caller-boundary proof; mounted
interrupted recovery remains required. The missing OPEN/cancelled suffix-family
finding remains unresolved.

`PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/composition/test_r14_historical_suffix.py -k 'not covers_existing_per_call' tests/composition/test_r14_native_selected_accounting_fast_path.py tests/composition/test_r14_accepted_direct_terminal_history_reuse.py`
returned `25 passed, 3 deselected in 1.06s`. The separately executed
`tests/composition/test_r14_historical_suffix.py::test_native_suffix_covers_existing_per_call_terminal_publication_families`
returned `3 failed in 1.03s`, reproducing the missing physical sequence coverage
for OPEN accounting, OPEN recovery and cancelled accounting. These are known red
tests, not excluded completion requirements. Family admission must authenticate
causes, not only reconstruct canonical owner commands. A `$swarm-mode` design
investigation produced `R14-SUFFIX-CAUSAL-DESIGN.md`: validate each admitted
publication against its own expected-head prefix after authenticating the full
current physical effects cut. This is a proposed seam, not an implemented fix.
In particular, ordinary anchored direct reads must also validate OPEN causes;
the commitment-mismatch callback alone is insufficient. Existing atomic OPEN
recovery creates its own opening and does not require prior standalone accounting.

`PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/capabilities/agent_loop/test_execution_fan_out_preparation.py::test_call_and_manifest_bounds_include_exact_boundary tests/capabilities/agent_loop/test_execution_fan_out_preparation.py::test_response_and_complete_owner_output_boundaries tests/composition/test_execution_fanout_records.py`
returned `29 passed in 0.96s` before adding the physical-envelope boundary test.
`PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/composition/test_execution_fanout_records.py::test_complete_physical_envelope_enforces_its_own_exact_byte_boundary`
then returned `1 passed in 0.27s`. The new check admits a complete envelope at
its exact limit and rejects the same-size complete batch against limit minus
one, while the owner output remains below both limits. Fixed decimal width
prevents the bound's own encoding from moving the measured boundary.

The corrected original two-seal selector passed with diagnostic clock in
`/private/tmp/chiplog-r14-two-seal-record-fixed`; log:
`/private/tmp/chiplog-r14-two-seal-record-fixed.log`. It was launched before the
later same-Run suffix-intent repair, so its main process cannot certify that new
repair's mounted behavior. Result: `1 passed in 1899.00s (0:31:38)`.
Its first seal is cancelled, not an accepted historical
terminal. Keep the accepted cross-seal interruption discriminator separate.

The later consumption-family repair registers only the existing
`dispatch.consume_effect_send`, validates its supplied owner snapshot via
`validate_consumptions`, and checks same-Run intent membership. Red physical and
semantic composition probes failed before the repair; the focused suite after
repair, with real retained consumption-source negative checks and physical OPEN
prefix hash checks, returned `34 passed, 3 deselected in 1.13s` with
`PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/composition/test_r14_historical_suffix.py -k 'not covers_existing_per_call' tests/composition/test_r14_native_selected_accounting_fast_path.py`.
The three missing OPEN/cancelled family cases remain red. The physical OPEN
prefix helper alone grants no authority and does not by itself fix the
OPEN-family composition guarantee. The subsequent wiring state is recorded below.

## OPEN source fold follow-up

Both OPEN accounting/recovery reader branches now check selected source headers
and the real physical prefix commitment. The mounted selector
`tests/composition/test_r14_native_open_obligation.py::test_selected_native_open_obligation_remains_outcome_unknown`
passed with diagnostic clocks before the later causal fold was added:
`1 passed in 1243.67s (0:20:43)`. Command used the clock environment documented in
`R14-MCP-DEBUGGING.md`, `--basetemp=/private/tmp/chiplog-r14-open-prefix-wired`;
log `/private/tmp/chiplog-r14-open-prefix-wired.log`. This is functional evidence
for the physical source-stamp wiring, not production timing or the later fold.

The later fold captures Run/fanout/acceptance prefixes before each OPEN and
checks all captured causes after one complete current effects acquisition.
Selected recovery history remains separate from exact physical Run history:
only an authenticated pending ABSENT batch can be removed from the latter;
COMPLETE stays and CONFLICT rejects. Exact adjusted whole-loop membership is
checked even on this selected-only OPEN path. Captured prefix Runs must exist
physically at or before the source sequence. No source collector is called from
the physical-ahead callback. Ordinary anchored reads also execute the causal fold.

`PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/composition/test_r14_native_selected_accounting_fast_path.py tests/composition/test_r14_historical_suffix.py -k 'not covers_existing_per_call'`
returned `59 passed, 3 deselected in 1.79s` after physical/pure-causal helpers and
wiring. Ten of those use real SQLite publication inspection for ABSENT/COMPLETE,
unverified absence, conflict and orphan states under suspension/successor operation
names; their Run transition semantics are inert inputs, not mounted owner proof.
Eight pure causal checks reject coherent cut-field substitutions, future Run or
outcome, and already-closed source histories, while allowing closure after source.
Three later wiring probes separately passed (`3 passed, 23 deselected in 1.04s`),
showing one effects acquisition with the physical snapshot for two captured OPEN
prefixes and rejecting absent/future prefix Runs before effects acquisition.

A cold no-parent design read found no new critical/blocking omission in the
wiring, conditional on its existing authenticated input contracts. The fresh mounted
run with the causal fold passed (`1 passed in 1389.17s`, exit 0):
`--basetemp=/private/tmp/chiplog-r14-open-causal-permitted`, log
`/private/tmp/chiplog-r14-open-causal-permitted.log`, same selector/environment as above,
with local socket permission. The first attempt at `open-causal-wired` failed
before OPEN at owner-process `listener.bind()` with sandbox PermissionError
(`1 failed in 7.87s`); it is not a functional verdict on the fold.
That run preceded the isolated pending/family/selector follow-up below. Mounted
pending suspension/successor recovery, accepted cross-seal
interruption/substitution, and production-clock witnesses remain required.

### Newly reproduced remaining OPEN blockers

Two target-selection probes in `test_r14_native_selected_accounting_fast_path.py`
failed (`2 failed, 26 deselected in 1.13s`, selector filter
`historical_cut_selects_call_seal or open_unqualified_cut`). OPEN `_current_cut`
still requires one native execution globally and does not pass a target call to
the existing outcome collector. The OPEN publication/read/replay paths also use
global singleton selection. A cold plan read additionally identified that startup
validation invokes the legacy whole-seal projection with only one call frontier;
target qualification alone cannot admit a multi-call seal. The next slice must
validate per-call frontiers while preserving singleton-only legacy whole-seal
access, authenticate all marked envelopes before filtering, and enforce unique
`(response_id, original_call_id)` keys. Resolver overlay must bind the exact call
and obligation; multi-resolver convergence remains an explicit later gap.

The early exact-history gate is another blocker: expected historical OPEN reads
set `exact_historical_sources=True`, which rejects pending suspension/successor
before the new fold. The outer-reader reachability probe
`test_exact_open_history_reaches_semantic_validation_before_pending_lifecycle_verdict`
returned `2 failed, 2 passed, 39 deselected in 1.28s`: both lifecycle/OPEN cases
fail before semantic validation; unknown-family and no-OPEN controls still HOLD.
This probe replaces source authentication with controlled inert inputs and proves
only early-gate reachability, not publication validity. Deferral must require the
later complete physical/causal proof. COMPLETE pending lifecycle needs an exact
final-batch predecessor/resulting proof in the effects callback; merely deferring
the gate would still leave it unsupported. Keep ABSENT/COMPLETE mounted recovery
and corrupted-predecessor/orphan negatives required.

Cold `$swarm-red-team` on `gpt-6.1-sol`, effort low, without parent context
confirmed three P1 mechanisms in this pending route: the early gate; ABSENT
membership adjustment depending on `pending_source_publication` before the fold;
and missing COMPLETE final-pending anchor proof. Proposed narrow repair is to
carry the same authenticated pending entry through semantic decoding and the
existing ABSENT membership adjustment, then require a nonempty captured OPEN
fold. COMPLETE proof must bind that exact semantically validated selected entry,
sole pending identity, final physical sequence, sole publication, ordered exact
batch, independent anchor and stripped-prefix hash. No canonical history/source
collector may re-enter from that callback. The review found no additional P0/P1
in this conditional design; it produced no mounted-success evidence.

## Isolated follow-up awaiting checkpoint integration

`/private/tmp/chiplog-r14-debug-checkpoint` contains the later fixes while the
mounted run's main sources were frozen. The exact early gate can defer only the
same selected pending lifecycle entry, then requires nonempty OPEN causes and the
full late fold. ABSENT validators retain strict defaults; their exceptional outer
path still validates owner envelopes, suspension actor budget and typed records.
The original suspension referenced by a successor and successor-start remain strict.
COMPLETE pending lifecycle proves the exact final batch and real stripped prefix.
Existing invocation/resolution prefix authentication is carried from the successful
early verifier within the same held acquisition, never invented from a caller's
claimed commitment pair.

The first cold implementation review found a real P1: ABSENT with actual==anchor
skipped the effects callback and lacked physical head/predecessor binding. Four new
real-SQLite cases failed with DID NOT RAISE (future-head and wrong predecessor for
both lifecycle operations). The isolated physical-history helper now checks actual
tenant head against snapshot, pending expected_head against that head, and retained
predecessor against both actual hash and independent anchor. All 14 physical-history
cases passed afterward. Re-review closed that P1; no mounted pending success is claimed.

The three existing OPEN/cancelled suffix families now use their canonical retained
builders in the isolated verifier. Causal OPEN validation executes before a reader
exports these facts; cancellation's outer source decoder is retained. The former
three physical coverage failures pass. OPEN historical source selection is now
call/seal-qualified, and startup validates each exact call frontier without invoking
an incomplete legacy whole-seal projection. The legacy whole-seal reader stays
singleton-only. Public per-call OPEN writer/read/replay APIs remain the next slice;
this source/validator repair alone does not create a multi-call OPEN publication.

Command in that isolated tree with explicit its `src` PYTHONPATH and UV_NO_SYNC=1:
`./.venv/bin/python -m pytest -q --tb=short tests/composition/test_r14_native_selected_accounting_fast_path.py tests/composition/test_r14_historical_suffix.py`
returned `98 passed in 2.55s`, with no deselected cases. After restoring unrelated
copied source changes out of the isolated checkpoint candidate, the same command
returned `98 passed in 2.17s`. Ruff and diff checks passed. A new cold
`gpt-6.1-sol`/low `$swarm-red-team` read the actual narrow slice and found P0=0/P1=0;
it independently obtained 98 passed. Its mechanism audit covered omission,
unknown family, duplication/reorder, cross-owner substitution, stale/future sources,
ABSENT/COMPLETE pending boundaries and callback recursion. Same-model independence
limits remain. Some component acquisition/retained inputs are inert, as marked in tests.
This is focused component/boundary evidence, not mounted pending recovery, cross-seal
interrupted recovery, multi-resolver convergence or production-clock completion.

## Per-call OPEN API and current-source follow-up (2026-10-15, in progress)

Public accounting/recovery writers and readers now accept `original_call_id`.
Replay validates all marked OPEN envelopes before choosing that call; startup
accounting uniqueness is per `(response_id, original_call_id)`. Both source
validators reconstruct the retained historical cut and bind acquired facts to
the full current physical commitment, with before/after physical and anchor checks.
Pending admission adds only OPEN accounting/recovery to suspension/successor.
Direct-terminal remains excluded: cold plan review found that the common pending
callback would bypass its separate CLOSED-source semantic check.

Command in the main tree:
`./.venv/bin/python -m pytest -q --tb=short tests/composition/test_r14_historical_suffix.py tests/composition/test_r14_native_selected_accounting_fast_path.py`
returned `139 passed in 2.32s`; Ruff passed. The mounted
`test_selected_native_open_obligation_remains_outcome_unknown` run is still in
progress under the diagnostic clock, log
`/private/tmp/chiplog-r14-open-per-call-source.log`. Its source hashes are frozen
in `/private/tmp/chiplog-r14-open-per-call-source-hashes.json`.

Cold implementation review then found a P1 missed by inert acquisition probes:
the real cut builder admitted anchor lag only through resolution-prefix proof,
so COMPLETE pending OPEN would still reject before replay. In the isolated
`/private/tmp/chiplog-r14-debug-checkpoint`, the real-builder regression returned
`None` for the valid case before repair. Historical construction now obtains
the canonical exact history proof before admitting an anchor lag; fresh cuts
still require equality. It requires facts' commitment equal current physical
commitment and rechecks both physical commitment and captured anchor afterward.
The same two-file command with isolated `src` PYTHONPATH returned
`143 passed in 2.36s`; cold re-review independently obtained 143 passed and closed
the P1 with no new P0/P1. The four builder probes retain inert source facts and
outer history acquisition, so they do not establish mounted recovery.

A new isolated mounted test injects the materializer's real `after_commit` fault
for OPEN accounting, checks COMPLETE pending with predecessor anchor through
the actual outer reader, then reopens and checks exact replay and one external
transfer. It is running; no passing outcome is claimed. ABSENT, recovery-family,
lifecycle, multi-call and cross-seal interruption coverage remain required.

Both initial mounted fault probes returned real red results:
`after_commit-accounting` failed in 90.33s at native scope historical initialization;
`before_commit-recovery` failed in 92.26s at historical ingress materialization.
Both paths used the historical-prefix verifier, which had no OPEN delegation.
The isolated repair delegates only the two OPEN families after canonical command,
header, tenant/database and source-stamp checks. Its physical proof uses the exact
final batch or exact ABSENT physical head/hash/anchor; it grants historical reads
only. The existing canonical outer causal fold remains mandatory before replay.
The three-file focused suite including `test_r14_recovery_pending_prefix.py`
returned `153 passed in 3.41s`. The two mounted red selectors are rerunning in
`/private/tmp/chiplog-r14-open-pending-prefix-fixed.log`.

Cold review found another concrete pending window: `_finish_decision` advances
the independent anchor before appending MATERIALIZED. A crash between them leaves
COMPLETE with anchor==result, while the current final-batch helper requires
anchor==predecessor. This remains a P1 to repair and verify. Historical ingress
always requests the prefix proof, even when actual==anchor, so equality alone
does not bypass that pending check. No startup-recovery completion is claimed.

The frozen main-tree mounted OPEN run finished successfully:
`1 passed in 1407.81s (0:23:27)`. Its five recorded source hashes were verified
unchanged afterward. This is the per-call/source candidate before the later
isolated pending-cut builder, prefix and effects-callback repairs; it does not
validate those later changes or production-clock performance.

After the historical-prefix repair, the mounted COMPLETE accounting case got
past its initial outer read but failed reopening in 142.40s: selected source
facts were unavailable. MCP debugger confirmed exact startup stack, `facts=None`,
head 19/source sequence 18, stable physical commitment and stable predecessor
anchor. The next probe disproved an acceptance acquisition failure: accepted
facts were present for the exact call. The outcome collector caught
`EffectsIntegrityError`, caused by `physical authority differs from independent
commitment`; its suffix callback knew resolution/direct-terminal but not OPEN.
There were zero direct terminals in this fixture. The isolated callback now
tries the exact OPEN historical-prefix proof; the boundary regression first
returned false before repair. The proposed extra accepted-history reuse was
therefore not implemented. With the anchor-position physical repair, the focused
three-file suite returned `159 passed in 3.48s`. Further mounted fault results
are pending and remain required before startup recovery is declared verified.

## OPEN checkpoint `dd0fabc` and remaining mounted verification

The call-qualified API/source and pending-cut repair is committed as
`dd0fabc` (10 files). Its checkpoint gate ran the focused fast-path,
historical-suffix and existing recovery-prefix files: `162 passed in 4.06s`.
R8 development structure/import preflight passed before those selected tests.
After integrating exact candidate bytes into the main workspace while preserving
all unrelated dirty bytes, the same three-file command returned
`162 passed in 3.69s`. Cold code review found no new P0/P1 semantic bypass.

The effects callback now recognizes exact pending OPEN prefix proof. For sole
pending OPEN accounting/recovery only, historical source acquisition passes the
canonical outer reader's physical Run snapshot under the continuously held gate;
these batches add no Runs, and effects still checks the exact physical Run map
and frontier. Lifecycle/direct-terminal/V3 snapshots are not admitted by this
exception. COMPLETE physical proof accepts predecessor or resulting independent
anchor, and always reconstructs the stripped prefix against entry.predecessor.

The new actual after-anchor accounting fault first failed startup in 142.52s.
After snapshot repair, the two selectors `[after_anchor-accounting]` and
`[before_commit-recovery]` in `test_r14_open_pending_mounted.py` passed:
`2 passed in 380.89s (0:06:20)`. They check the real outer read before replay,
reopen without pending, exact replay with unchanged history, and one external
transfer. The additional four-selector run on the identical clean candidate
finished `4 passed in 789.18s (0:13:09)`. Together the two invocations cover both
OPEN families before COMMIT, after COMMIT and after anchor advancement; all six
selectors passed. Both source trees still match the frozen ten-file candidate.
Logs are `/private/tmp/chiplog-r14-open-pending-snapshot-fixed.log`
and `/private/tmp/chiplog-r14-open-pending-other-boundaries.log`.

The next mounted test file remains outside this checkpoint: three accepted
members now require SUCCEEDED/FAILED_DEFINITE/RECOVERY_REQUIRED with delayed
dispatch, followed by accepted-first-seal/second-seal-OPEN interrupted recovery.
Cold plan review required native scope on first capture, a complete historical
locator triple, and bounded claims for the foreign CLOSED-to-OPEN negative;
those corrections precede execution. The three-member selector has passed; the
cross-seal selector is still running, with sources frozen. A separate guarded
script reopened the exact three-member fixture and replayed V3, verifying that
selected history and external transfers were unchanged (exit 0; log
`/private/tmp/chiplog-r14-three-member-v3-reopen.log`). This is diagnostic-clock
functional evidence, not production latency evidence. Full pending lifecycle recovery, strict
same-kind cross-seal evidence isolation and production-clock evidence remain
required; neither this checkpoint nor diagnostic-clock passes close R14.6.

The mounted SUSPENSION after-COMMIT case reproduced a historical scope refusal:
`test_selected_open_pending_batch_recovers_after_reopen[after_commit-suspension]`
failed in 195.09s at `r17_native_one_call_scope.py` (initialization physical prefix
could not be authenticated with an independently pending suspension). A narrow
prefix repair now decodes the canonical retained suspension and reuses the same
exact header, batch, predecessor and independent-anchor proofs. Cold review of
the plan and actual diff found zero P0/P1. The quick three-file regression command
for `test_r14_native_selected_accounting_fast_path.py`,
`test_r14_historical_suffix.py` and `test_r14_recovery_pending_prefix.py` returned
`162 passed in 4.47s`; its SQLite matrix now exercises suspension prefix dispatch.
The initial mounted rerun still failed (210.62s): MCP inspection found the
canonical command's preimage field must be normalized to the physical journal
representation, as in the existing suspension validator. That correction passes
the 82-test SQLite/semantic unit file. Reopening the exact failed fixture then
passed scope authentication but failed at startup's effects commitment check:
startup's selected reader does not yet activate the pending lifecycle fold.
A cold-reviewed narrow startup condition and fresh mounted results remain
required; no lifecycle recovery success is claimed.

The subsequent narrow repair activates the existing deferred OPEN fold for a
selected-only startup with exactly one pending suspension, and gives the
historical OPEN source a separately verified physical Run projection after the
canonical outer fold. The complete semantic snapshot remains intact. Pending
suspension preservation uses the exact outer fold, global singleton selected
suspension, annotated decision and existing selected validator, instead of the
ordinary no-pending reader. No direct/V3/successor pending exception was added.

The physical-source selection unit first failed (`None` rather than the physical
projection; three sibling cases passed), then the three-file quick command
returned `162 passed in 3.31s`. Cold reviews of the actual source diff returned
zero P0/P1. The original ABSENT failed fixture reopened with exit 0 and one
original transfer. MCP debugger session
`f0a675d1-3845-4432-8418-5703124651e1` reopened the original COMPLETE failed
fixture: prefix proof True, selected suspension exact refs/obligation, final
script exit 0 and one transfer. Both planned breakpoints matched the expected
stack and operands; the session was closed.

Fresh mounted three-boundary rerun log:
`/private/tmp/chiplog-r14-suspension-pending-full.log`. Its before-COMMIT case has
passed; after-COMMIT and after-anchor are pending. No aggregate pass is claimed.
The two-seal interrupted OPEN selector is still running. A separately cold-read
script for own CLOSED positive control and foreign CLOSED locator rejection is
prepared but not executed. Same-kind cross-seal evidence and full pending
lifecycle coverage therefore remain OPEN.

The fresh SUSPENSION run subsequently finished: `3 passed, 6 deselected in
1484.84s (0:24:44)`, covering before-COMMIT, after-COMMIT and after-anchor.
All seven candidate file hashes still match the frozen manifest
`/private/tmp/chiplog-r14-suspension-candidate-hashes.json`; their ASTs match the
source/test files in checkpoint `943e4a4` (import formatting differs only).
This establishes those three diagnostic-clock SUSPENSION recovery cases,
including the real outer pre-replay read, unchanged exact replay, preserved
original obligation and one external transfer. It does not close the full
pending lifecycle inventory or production-clock requirement. The normal-clock
fresh before-COMMIT SUSPENSION case is now running, with builtin clock assertions
inside the pytest process and source profile recorded before/after.

That ordinary-clock case failed in 214.40s: SUSP writer returned STALE before
the injected before-COMMIT fault. Builtin clock assertions passed at test entry
and teardown; `/private/tmp/chiplog-r14-production-clock-profile.json` reports
`unchanged_profile=true`, 1206 tracked source/test/config file hashes and HEAD
`943e4a4`. The recorded profile includes the still-uncommitted main performance
changes, so this is neither clean-HEAD evidence nor a passing production case.
The log is `/private/tmp/chiplog-r14-production-clock-suspension.log`.
It tests close/reopen within one process with retained resources, not a fresh
process restart. Admission operands are now being inspected with conditional MCP
breakpoints; no permanent budget/clock change has been made.

### Cross-seal pending anchor and native SUSP phase attribution (2026-10-15)

Frozen pair run: `chiplog-r14-three-outcomes-cross-seal.log` returned one pass,
one failure in 5401.28s. Three distinct mounted dispositions passed; second-seal
accepted OPEN recovery after COMMIT failed the exact reader because the first
seal's complete direct terminal required physical commitment equal to anchor.
MCP session `86cd68cb-098d-441d-a1e1-f3810ab8d5a7` confirmed COMPLETE first
batch (expected head 20), sole pending OPEN recovery (expected head 34), exact
snapshot joins and pending predecessor/resulting equal to anchor/current cut.
Session closed without recovery; whole original fixture archived first.

Candidate physical-prefix correction preserves the target COMPLETE requirement
and all snapshot joins; it authenticates the sole canonical pending suffix with
the existing physical-prefix proof. Both frontier callers use the held runtime
proof; the pure strict predicate remains unchanged. Target semantic reconstruction
still runs. Cold actual review found no blocking defect; return-None compatibility
for a non-COMPLETE member was retained. Focused command:
`PYTHONPATH=src UV_NO_SYNC=1 .venv/bin/python -m pytest -q tests/composition/test_r14_direct_terminal_pending_open.py tests/composition/test_r14_direct_terminal_frontier.py tests/composition/test_r14_historical_suffix.py tests/composition/test_r14_native_selected_accounting_fast_path.py`
returned 171 passed in 1.27s. The new seven-case orchestration matrix mocks the
prefix verdict; independent physical rejection evidence is in historical-suffix
tests. Exact-fixture candidate reopen is still running; no mounted green claim.

Original-code phase observer reproduced native SUSP STALE in 204.23s with an
unchanged source manifest and no observer errors. Inert reopened cut collection
profile completed with the same cut and unchanged sources. Attribution identifies
repeated history acquisition; neither profile closes the production-budget gap.

Further exact-fixture discriminator: the first candidate passed the physical
predicate and failed the direct-cut builder's final unconditional anchor check.
MCP session `4acb9fdb-fa00-4101-83ea-6b16aa727ef7` observed a non-None fully
built cut exactly equal to retained first-terminal evidence; only the independent
anchor differed from current physical commitment. The candidate now retains
strict new-cut admission and requires an authenticated snapshot, both historical
source stamps and the existing pending-prefix proof for this historical exception.
Six boundary cases additionally cover missing stamps/snapshot, failed proof and
changed CLOSED commitment; semantic DTOs are replaced only in that focused test.
The real semantic/physical composition remains the running original-fixture reopen
(`chiplog-r14-cross-seal-pending-reopen-builder-fixed.log`), not a completed pass.

The six-file focused pytest selection above, additionally including
`test_r14_direct_terminal_historical_cut.py` and
`test_r14_accepted_direct_terminal_history_reuse.py`, returned 184 passed in
1.45s in MAIN and 184 passed in 1.86s in the isolated checkpoint tree. The
checkpoint R8 structure/import gate plus those tests passed (184 in 1.43s).
Ruff passed for the four changed source modules and both test files. Cold actual
review found no critical/blocking defect in either correction. This checkpoint
is partial correctness progress; it closes neither R14.6 nor production budgets.

### Exact interrupted cross-seal fixture reopened after `5d16f4c`

`chiplog-r14-cross-seal-pending-reopen-builder-fixed.log` printed
`cross-seal exact fixture reopened; two original transfers`; execution session
79980 completed with exit 0. Original H1-bound paths and custody were retained,
with V39/NativeRecoveryCutSource and the fixture's original diagnostic-clock
profile. Startup recovered the sole pending publication. This is the resumed
original failed fixture on current MAIN WIP, not a passing fresh full selector
or production-clock evidence. The source/test candidate hashes recorded before
the run remained unchanged through the checkpoint integration.

The original pair still records 1 passed / 1 failed in 5401.28s. Its remaining
positive V3/replay assertions now run separately on the recovered fixture via
`chiplog-r14-cross-seal-resume-baseline.py`. Reads must preserve the post-recovery
history; second V3 may advance head once; subsequent cross-order V3 and terminal
replays must preserve the new history and two transfers. The strict same-kind
CLOSED locator substitution probe remains gated on this continuation's pass.

A separate history-only SUSP optimization is being tested in an isolated tree;
its focused 170-test pass and cold review do not close handoff №34. Normal-clock
mounted result is pending. See `R14-PERFORMANCE-DIAGNOSIS.md` for its source
profile and unchanged authority/budget boundaries.

### First fresh native-clock SUSP pass, isolated history-only candidate

`chiplog-r14-history-reuse-native.log`: **1 passed in 209.62s**, session 12777
exit 0. Selector:
`test_r14_open_pending_mounted.py::test_selected_open_pending_batch_recovers_after_reopen[before_commit-suspension]`.
Source tree `/private/tmp/chiplog-r14-suspension-history-reuse`: HEAD `5d16f4c`
plus the five inherited source WIP files and three-module held-history candidate.
Builtin monotonic clocks verified in the pytest interpreter at entry/teardown;
no clock shim or actor-budget change. Source manifest unchanged with exit 0.
This proves the fresh pending-read/reopen/replay SUSP slice in that explicit
profile. It is not a clean-HEAD/full-MAIN result and does not discharge handoff
№34's remaining production multi-seal route or №36's negative inventory.
The candidate remains isolated until MAIN's frozen cross-seal run finishes.

Additional checks for the isolated candidate: native recovery cut's 24 fast tests
passed in 0.62s (`-k 'not selected_native_cut_reopens'`; two mounted cases were
not rerun). The clean checkpoint tree's R8 structure/import preflight and selected
six pytest selectors passed: 173 in 1.79s. Scope: fast-path/history suffix/pending
terminal tests plus exact historical-cut, CLOSED-source and history-forwarding
selectors. No production pass is inferred for the clean checkpoint source profile
without its five inherited performance changes; the real-clock result belongs
only to the explicitly retained isolated WIP profile above.

### Inherited capture performance slice

Five inherited performance modules are now saved with their actual dependencies:
local immutable digest reuse, reuse of already authenticated historical admission,
V3 artifact reconstruction reuse and removal of redundant capture postpublication
reads. Every next capture consumer still reads fresh selected history; default
standalone publication keeps its output check. The native scope also checks the
Run head/digest before scope issuance.

On the frozen MAIN WIP, execution runtime tests returned 7 passed in 33.51s;
recovery preparation/V3 record tests returned 22 passed in 0.83s. Three real
producer corruption probes returned 3 passed in 10.33s: after completing
Accumulate, Prepare or Emit, corrupt the exact selected row, reanchor physical
storage, require typed refusal, zero model requests and unchanged journal/head.
They prove legacy capture outcome fences, not native-scope append rejection or
absence of AUTHENTICATE IPC. Cold actual reviews found zero P0/P1.

The appended permanent test preserves those executed probe bodies. The earlier
normal-clock SUSP pass remains tied to its recorded isolated WIP profile; saving
these source bytes in a checkpoint does not convert that result into a new HEAD
production run. R14 remains OPEN; cross-seal continuation and strict CLOSED
locator substitution are still pending.
