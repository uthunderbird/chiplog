# R14 verification status

Status: OPEN, 2026-10-16. Acceptance authority:
`R14-REMAINING-ROADMAP.md` and original clauses in `R14-R17-COMPLETION.md`.
This is an evidence ledger, not a replacement acceptance boundary.

## Current R14.6 audit

| Requirement | Existing evidence or candidate check | Current conclusion / next action |
| --- | --- | --- |
| Mixed accepted/cancelled seal and per-seal V3 | `test_v3_mixed_seal_publishes_reads_after_reopen_and_replays_once` | Existing mounted baseline; retain, then verify current integrated tree. |
| Two seals in one Run, target-scoped terminal and Prepare | `test_v3_accounts_two_sequential_turn_seals_after_reopen_and_cross_order_replay`; `R14-MCP-DEBUGGING.md` | Normal-clock sequential two-seal selector passed at `143b5c1` (1376.04s; evidence below). Its first seal is cancelled. Accepted OPEN cross-seal interrupted recovery remains unverified. |
| Per-seal replay and reopen without head advance | Same two-seal selector | Both V3 records, cross-order replay and reopen/head invariants passed on normal clocks at `143b5c1`; verify the final integrated tree before closure. |
| Three accepted members with distinct dispositions | `test_v3_reads_three_accepted_members_across_delayed_dispatch_and_replay`; owner `test_v3_accounts_three_accepted_members_with_distinct_existing_outcomes` | Normal-clock selector passed at `143b5c1` with SUCCEEDED / FAILED_DEFINITE / SUCCEEDED, delayed dispatch, replay and reopen (465.83s; evidence below). The earlier RECOVERY_REQUIRED combination has diagnostic-clock evidence only. |
| Dispatch after another member terminalizes | Three-member mounted selector closes first before accepting second | Normal-clock three-member selector at `143b5c1` passed delayed dispatch after the first terminal. Preserve this observation in final integrated verification. |
| Multi-seal member interleaving | Two-seal selector | Sequential Turns alone do not prove all required member interleavings; inspect exact observations before claiming closure. |
| Complete physical membership / orphan detection after reopen | Mixed-seal selector has physical orphan damage; `test_r14_loop_history.py` | Existing candidates; inspect mutation scope and verify mounted rejection on current tree. |
| Cross-call / cross-seal evidence substitution | Owner duplicate/cross-seal test; two-seal source rejects foreign call lookup | These narrow checks do not prove mounted accepted-evidence substitution rejection. Add discriminating test. |
| Omitted sequence, broken chain, unknown family, foreign closed locator, later evidence excluded from earlier semantic prefix | `r14_historical_suffix.py`, direct-terminal historical/frontier tests | Cold design mechanism audit in progress; identify coverage and add red tests for confirmed gaps. |
| Three independent A54 N/N+1 limits | `test_call_and_manifest_bounds_include_exact_boundary`, `test_response_and_complete_owner_output_boundaries`, new `test_complete_physical_envelope_enforces_its_own_exact_byte_boundary` | Owner and physical builder boundaries verified in focused tests. This does not prove mounted sole-writer admission/reopen for each limit; preserve that scope in final audit. |
| No partial companions after rejection/interruption; no preaccept external call | Branch-competition and fanout mounted suites | Coverage audit and current execution needed. |
| Per-slice writer registration and durable-version admission | `r7_runtime.py` V39 manifest; publication/readers | Verify exact registered routes and physical schemas, including negative unknown versions. Registration presence alone is insufficient. |
| Production admission deadlines | Diagnostic clock deliberately scales `monotonic_ns`; production clocks unchanged | Normal-clock sequential two-seal and three-member selectors passed at `143b5c1`; accepted OPEN cross-seal recovery and the final integrated tree still require verification. |

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

### Fresh native three-outcome and pending damage witnesses

A fresh normal-clock mounted three-member selector passed in 665.48s, session
5971 exit 0. It proved SUCCEEDED / FAILED_DEFINITE / RECOVERY_REQUIRED, delayed
dispatch, V3 and exact terminal/V3 replay without head advancement, rejected an
unselected-call write without journal changes, and reopened qualified terminals.
Profile: isolated HEAD `8dff0ff` plus mounted-test candidate module SHA256
`284f90a6327ccf8cd824124eafb024fef578f20d9e4817f780c156a5e1a3f1b3`.
All 1207 source/test/config hashes matched before/after. Builtin clocks and the
project import path were asserted inside pytest at entry/teardown. No authority
budget change. This is one seal, not production two-seal evidence; V3 replay
after that separate fixture's reopen was not asserted by this selector.

Two fresh normal-clock pending SUSP negatives passed in 204.75s, session 52276
exit 0: before-COMMIT ABSENT with corrupted exact Run predecessor; after-COMMIT
COMPLETE with an orphan row outside its final batch. Each first passed the real
pending outer read, then reanchored damaged storage, preserved target state,
required typed outer/startup refusal before context yield, and kept pending,
loop journal, tenant head and one external transfer unchanged. Source/test/config
and external probe hashes matched before/after on the same isolated 8d profile.
The first attempt failed because the test whitelist omitted the concrete
OwnerJournalIntegrityError. After reading its source wrapper, that explicit type
was added; runtime code was unchanged. These are outcome fences, not proof of
the exact rejecting stage or all-family negative inventory.

Original cross-seal continuation session 25277 exited 0: both V3 and terminals
replayed exactly with unchanged history and two transfers. It remains diagnostic
clock evidence on the original fixture. The subsequent strict CLOSED probe
failed its own-locator positive control after closing the second outcome; it
never reached the foreign-locator negative. MCP inspection established that the
probe used an internal outcome fingerprint instead of the complete canonical
outcome digest. The corrected read-only
probe completed as session 30384 after using SHA256 over complete canonical
outcome bytes. It proved both own-locator positives before rejecting genuine
A-to-B and B-to-A foreign locators, then exact replayed both V3 and per-call
terminals with two original transfers and unchanged history. This remains
diagnostic-clock proof on the original fixture; no fresh production-clock
two-seal success or completed R14.6 claim follows from these results.


### Native second-admission diagnosis and local command reuse

Fresh native two-seal execution failed at the second accept_call with
STALE:admission-expired (948.92s total); it did not reach the second interrupted
publication. An MCP run, with no pause before expiry, stopped earlier in
prepare_call_exchange after reading previews/history. The original mandate's
60-second admission expired; trust session/snapshot and current worker matched.
This is not evidence of a 15-minute worker lease failure. The debugger was closed.

A read-only profile after ordinary reopen attributed 26.971s of a 27.191s preview
read to full history. Command construction occurred five times (8.898s cumulative).
The isolated repair derives the recovery command once per native validation and
passes it through private synchronous authentication. All historical cutoff,
physical, journal, trust and owner-result checks remain. Public boolean wrappers
and substituted source callbacks retain their dispatch. Reuse requires the exact
native class, original method function and the same bound receiver; no result is
cached across validations. A cold review found callback replacement bypass in the
first candidate; new dispatch regressions reproduced it before the guard repair.

The command-reuse profile reduced five constructions to three and function calls
from 2,658,197 to 2,214,298. Elapsed read time did not improve in these samples:
27.417s versus 27.191s. Both reads preserved head29, loop/owner journals and one
transfer. Post-restart attribution is not proof of original admission viability.
A 17-test focused selection passed before the callback guard repair; six focused
checks passed after the original-function guard, and three dispatch cases pass
after adding bound-receiver identity. The final checkpoint hook verifies its own
staged source and selected tests. Fresh native two-seal success remains required.

The earlier strict CLOSED probe used an internal outcome fingerprint instead of
SHA256 of complete canonical bytes. MCP proved that the reader correctly rejects
that wrong domain. The corrected read-only probe completed with both own-locator
positives and both foreign-locator rejections; see session 30384 above.

### Post-accept history cache verification

The private runtime-local cache retains full post-accept history only when
before/read/after `CallHistoryInputs` match under the authority gate. Its key
contains exact selected journals and configuration, runtime path/device/inode,
schema-checked main authority commitment, and equality with the independent
commitment journal. A hit repeats `_require_no_pending`; preview bytes are still
re-read, validated and joined, while authentication, mandate, dispatch and scope
remain fresh. Unsupported cache inputs still run the full history reader and do
not seed an entry.

`PYTHONPATH=src .venv/bin/python -m pytest -q tests/composition/test_call_history_witness_mounted.py`
returned `17 passed in 234.75s`. The new public Common CLI acceptance case
observes a post-commit cache hit with no second full history read. It separately
proves public-reader fallback and typed rejection for a changed independent
anchor and an unregistered SQL table, plus cache miss after authority-row
mutation, full read on unsupported cache inputs, and refusal while the pending
guard is held. The existing
`tests/composition/test_call_preview_runtime.py -k 'actual_atomic_call_acceptance or public_acceptance_exact_replay'`
selection returned `4 passed in 57.47s` for atomic acceptance, replay and
interruption/reopen behavior on its standard runtime fixture. These checks
support the local reuse mechanism only: they do not establish deadline relief or
replace the missing fresh production-clock two-seal acceptance.

### Current-main production-clock multi-member and two-seal passes (2026-10-15)

On current `feat/r14-r17-integrated` at HEAD `143b5c1`, with ordinary Python
monotonic clocks, no deadline shim, and no admission-budget changes:

- `PYTHONUNBUFFERED=1 PYTHONPATH=src ./.venv/bin/python -m pytest -s -q tests/composition/test_r14_native_selected_accounting_source.py::test_v3_accounts_two_sequential_turn_seals_after_reopen_and_cross_order_replay --basetemp=/private/tmp/chiplog-r14-two-seal-main-native-oct15`
  returned `1 passed in 1376.04s`. It accepts the second-Turn call within its
  60-second mandate (preview 11.78s; accept 16.79s; writer selected with 41.15s
  remaining), closes its direct terminal, publishes and replays V3 for both
  seals in cross order without advancing the head, reopens, and reads both V3
  records plus the second direct terminal. The first seal in this selector is
  cancelled; it does not prove an interrupted accepted OPEN across seals.
- `PYTHONUNBUFFERED=1 PYTHONPATH=src ./.venv/bin/python -m pytest -s -q tests/composition/test_r14_native_selected_accounting_source.py::test_v3_reads_three_accepted_members_across_delayed_dispatch_and_replay --basetemp=/private/tmp/chiplog-r14-three-outcomes-main-native-oct15`
  returned `1 passed in 465.83s`. It proves three accepted members with
  `SUCCEEDED / FAILED_DEFINITE / SUCCEEDED`, delayed dispatch after the first
  member, exact terminal and V3 replay without head advancement, rejection of
  an unselected call without writes, and reopen reads for all three terminals.

Before/after SHA256 values match for the tracked source, test and ledger files
captured before these runs. These results close the fresh production-clock
sequential two-seal and three-member rows only. Accepted interrupted cross-seal
recovery, mounted same-kind substitution negatives and the remaining R14.6–R14.13
requirements remain open.

### Accepted OPEN suspension guard attribution (2026-10-15)

The selector `test_accepted_open_recovery_reopens_across_seals_and_rejects_each_foreign_locator` passed second-call authorization, SEND and accepted-OPEN recovery, then returned `STALE` at the first suspension. A normal-clock run with local owner sockets returned `1 failed in 242.88s`; it is a failure witness, not completion evidence.

A follow-up mcp-debugger run used the prewritten per-breakpoint expectations in `/private/tmp/r14-cross-seal-suspension-stale-debug-expectations.md`, with seven verified non-pausing logpoints and no actor-budget or clock changes. BP1 recorded `now=1360682491378125`, deadline `1360711244135916`, and `source_cut_validated=False`. BP2 recorded an equal `ExecutionRecoveryCut` at `1360732449080083`: the held source collection took 49.957s, while only 28.753s remained at BP1. Conditional BP4 then recorded expiry at `1360732589054083` with `source_cut_validated=True`. BP3 source mismatch, BP5 caught exception and conditional SQLite head/preimage points did not fire. The test returned `STALE` in 234.16s.

Runner `/private/tmp/chiplog-r14-cross-seal-suspension-debug-runner.py` wrote `/private/tmp/chiplog-r14-cross-seal-suspension-debug-profile.json`: HEAD remained `143b5c1318ee054b3e14ed2453873f87cf2da504`; all 1,215 captured source/test/config files matched before and after. The debugger session was closed. This proves the guard-held re-collection crosses the fixed actor budget on this history. The fresh subset already carries `authenticated_execution_history`, produced by `read_execution_recovery_history(selected_only=True, exact_historical_sources=True)` under the authority gate. The collector then called the accepted-OPEN frontier reader, whose validator reopened that same history, and `_current_cut` reopened it again while independently checking current authority commitment, database identity, tenant, selected decision and outcome, and physical effects preimage. A boundary test initially failed because the history argument was not forwarded; the forwarding change now passes while the gate remains held. Next action: run this same mounted cross-seal selector without timeout changes; the suspension must pass and execution must reach second-seal recovery and locator-substitution negatives.

### Accepted suspended lineage diagnosis (2026-10-16)

The production-clock cross-seal selector still returns HOLD at its first resume.
A late-armed code-object-filtered call/exception/return trace recorded a normal
`_collect_held` return at the exact lineage comparison, with nine authenticated
Run records and accepted recovery inputs present; no exception was recorded.
The saved trace is `/private/tmp/r14-open-resume-return-trace-20261016.json`.
Read-only SQLite inspection of that fixture confirms eight pre-suspension Run
records followed by one causally linked SUSPENDED record.

The two source contracts differ: cancelled accounting retains pre-suspension
facts; accepted facts are freshly constructed from authenticated current history
and the subset reader requires their last Run reference to match the current
Run. Unconditionally stripping the suspension suffix therefore rejects accepted
resume. The repair strips it only for retained cancellation accounting and
preserves exact full equality for accepted facts, plus separate selected
suspension and physical-publication validation. A cold design review found no
critical/blocking defect in this bounded repair; this is model review, not
external independent verification.

The existing accepted-closed subset mounted test now performs resolver policy,
invocation, resolution and semantic reduction, then resumes without another
external send. Before the repair,
`uv run pytest -q tests/composition/test_r14_native_recovery_source_subset.py::test_source_subset_reopens_exact_accepted_closed_outcome_without_resend --tb=short`
returned `1 failed in 655.50s` at that resume with HOLD. The post-repair rerun
with `--basetemp=/private/tmp/r14-accepted-lineage-green-20261016` also returned
HOLD (`1 failed in 661.35s`), so the lineage repair is insufficient and the next
rejection still needs localization. The test was then corrected to retain its
original SUSPENDED reopen assertion before resolver/reduction and resume; its
current form has not yet been verified. The full cross-seal selector remains
pending. These results do not close R14.6 or R14.

### Resolver authority source projection diagnosis (2026-10-16)

A restart-only mcp-debugger runner reopened that failed fixture at its original
path. Before setting breakpoints, expectations for all collector return sites
and wrapper exits were written to
`/private/tmp/r14-accepted-resume-all-return-breakpoints-expectations.md`.
All 54 points were verified. The debugger stopped at collector line 1492,
after `classify_native_occurrences` returned false. The actual frame confirmed
full source-lineage equality (9/9) and a selected suspension, proving the prior
lineage gate now passes in this diagnostic history.

Classifying each captured occurrence independently rejected exactly two PHYSICAL
rows: `chiplog.loop.original-resolver-authority-policy.v1` and
`chiplog.loop.original-resolver-invocation.v1`. Both were present in the physical
inventory but absent from the projected known sources. No product clocks or
budgets were changed. The runner ended with HOLD and the debugger was closed;
its restarted resource construction is diagnostic evidence, not a substitute
for a full mounted pass.

The fast source-projection regression initially failed on the omitted policy
source (`1 failed in 0.60s`). The repair retains the already validated resolution
envelope through a private reader, preserves the public Prepared result, and
projects its exact resolver policy/registered authority/authenticated invocation
sources into the existing selected/physical reconciliation. It does not admit
these schemas without source proof. The regression also rejects omitted known
sources and changed physical bytes; missing physical membership remains the
collector's reconciliation obligation.

`uv run pytest -q tests/composition/test_r14_historical_suffix.py tests/composition/test_r14_native_recovery_cut.py tests/composition/test_r14_native_selected_accounting_fast_path.py -k 'not selected_native_cut_reopens'`
returned `189 passed, 2 deselected in 1.38s`. R8 structure/import and Ruff pass.
The repaired restart-only resume passed the collector and reached owner
preparation, which returned `TERMINAL_RECOVERY_FAULT /
RESUME_INTEGRITY_FAULT_OBSERVED`. This proves source classification progressed,
not that resume succeeded. The next action is to capture the exact owner request
and reproduce its underlying `CallPreparationRejected.reason` directly in the
pure preparation function. The corrected mounted restart/resume test and full
cross-seal selector remain pending. R14 remains OPEN.

### Owner request and R16 record-marker diagnosis (2026-10-16)

The restart-only request capture completed without owner submission/publication.
It saved 33,423,674 canonical bytes at
`/private/tmp/r14-captured-accepted-resume-request.json`. A direct pure regression
(`/private/tmp/test_r14_captured_resume_request.py`) failed in 0.49s with
`INTEGRITY_FAULT: native frontier member lacks authenticated source preimage`.
The missing EVIDENCE markers are the exact R16 outcome `record.record` references
retained by the immutable semantic reduction. The collector had projected raw
carrier and nested evidence heads, but not those body-domain record references.

The repair projects record-marker aliases only from the independently
authenticated native R16 outcome sources, preserving the existing physical
representative for classification. The owner-side preimage check verifies exact
canonical bytes, physical carrier identity/raw SHA, and recomputed original
command/snapshot/kind/predecessor body reference. It covers both evidence records
and the closed reconciliation witness; immutable reduction refs are unchanged.
A fast real-record test first failed the valid marker case, then passed evidence
and closed-witness cases plus subject/carrier/body/marker/schema/owner negatives.

An inert projection experiment on the captured input passes the missing-preimage
guard after adding the three corresponding aliases. Its next Run mismatch is
worker session: the restarted runtime has a fresh worker, while this route
requires the original same-worker Run. No stale worker credential was installed
or sent to a runtime. The mounted test now has separate `suspended` and `resumed`
variants, preserving SUSPENDED reopen and placing resume inside the original
runtime before ACTIVE reopen. Both current variants remain unverified.

An additional pure, in-memory same-worker diagnostic reaches
`obligation resume rewrote existing EVIDENCE heads`: baseline EVIDENCE is the
registered singleton ABSENT marker, while current EVIDENCE has its selected
record heads. Owner transition handling requires a discriminating red test;
no relaxation has been implemented. R14 remains OPEN.

The marker repair passed its cold scoped mechanism review with no confirmed
P0/P1. `uv run pytest -q tests/capabilities/agent_loop/test_native_recovery_cut_validation.py tests/composition/test_r14_historical_suffix.py tests/composition/test_r14_native_recovery_cut.py tests/composition/test_r14_native_selected_accounting_fast_path.py -k 'not selected_native_cut_reopens'`
returned `208 passed, 2 deselected in 1.38s`; R8 structure/import and Ruff pass.
These are local validation/projection results, not mounted resume completion.

### Exact obligation-resume transition and source provenance (2026-10-16)

The fast transition boundary now uses real original-resolution/reduction records
while isolating outer cut and suspension authentication. Its initial two positive
cases failed: the registered singleton ABSENT evidence marker was treated as an
existing evidence head, and AUTHORITY used the opening before its local binding.
The repair normalizes only the exact singleton ABSENT evidence prefix, validates
the opening before the family loop, retains inherited authority heads, and admits
only the exact absent-to-current semantic reduction transition. It also compares
the terminal's external-effect revision with the acceptance revision. Negative
cases assert specific rejection reasons for rewritten/extra evidence, unexpected
reductions, missing/extra authority, policy changes and unrelated frontier changes.

Two further tests first failed for omitted original opening/terminal source
projection and ambiguous physical/logical terminal aliases. The projection now
retains the exact sources from the authenticated resolver request; terminal role
selection requires the original-call logical subject and still checks full source
equality against invocation. Duplicate matching logical terminals still reject.
Original agent-loop source provenance is preserved only after independent journal
selection proves exact owner/schema/bytes, physical reference, and complete
selected-decision revision. A same-subject source replaces the generic physical
projection rather than introducing a second role. Mutation checks cover decision
head/fingerprint, physical identity, bytes, owner and schema.

`uv run pytest -q tests/capabilities/agent_loop/test_obligation_resume_transition.py tests/capabilities/agent_loop/test_execution_recovery_preparation.py tests/capabilities/agent_loop/test_native_recovery_cut_validation.py tests/composition/test_r14_historical_suffix.py tests/composition/test_r14_native_recovery_cut.py tests/composition/test_r14_native_selected_accounting_fast_path.py -k 'not selected_native_cut_reopens'`
returned `247 passed, 2 deselected in 1.96s`. Ruff passes. These checks prove the
local boundaries, not mounted owner admission. The fresh same-runtime production
clock selector is running; an earlier run was explicitly interrupted after
47.31s because it had loaded code before the final source-provenance change.
The cross-seal accepted-open selector and full R14 completion remain OPEN.

The first fresh mounted selector failed before resume, at suspension, after
64.58s. A real `$mcp-debugger` attach to a fresh same-runtime fixture isolated the
original caught exception: `collect_held:775 -> _collect_held:1623 ->
_assemble_accepted:2636`, `selected effects source lacks one exact recovery
preimage`. Source lines refer to the debugger-frozen tree with SHA256
`aa4a49f11f7e57dbac77e2dad3a62cba3d726d24a91efe36309ca3f8bdd9eba1`.
Before setting breakpoints, reachability/value/stack/next-step expectations for
all rejection sites were saved to
`/private/tmp/r14-suspend-provenance-expectations.md`. No product clocks changed;
the session was closed and the server reported zero remaining sessions.

The failed join compared only effect subject ID and head. Two authenticated
preimages shared those fields but had different fingerprint domains: the existing
normalized carrier fingerprint and the new immutable body-domain marker alias.
The extracted unchanged join reproduced two red fast cases (carrier plus marker
wrongly ambiguous; marker alone wrongly accepted). The repair matches the entire
expected normalized subject, retaining existing carrier semantics and preserving
the separate reduction marker. Duplicate carriers still reject; nested logical
children retain their child fingerprint and reject a carrier-digest replacement.
A cold plan review found no critical issue with that repair.

The six-file fast-suite command above now returns `251 passed, 2 deselected in
1.91s`; Ruff passes. A fresh same-runtime mounted run is pending on this tree.
The cold scoped design review of the preceding provenance/transition changes
found no substantiated P0/P1; its artifact is
`/private/tmp/r14-resume-provenance-redteam.md`. It did not prove mounted admission.

The next mounted run passed cut construction and reached the owner, then failed
suspension after 63.59s with `missing or duplicate accepted-recovery source`
for the terminal (`matches=2`). The raw terminal alias still carried generic
command-subject provenance while its logical alias carried exact decision-subject
provenance. The projection now builds one independently verified decision mapping
per original physical row, rejects conflicting full decision references, and
applies that provenance to raw and logical aliases alike. Owner strict source
deduplication and invocation equality are unchanged. A focused real-terminal
test reproduces the conflicting-provenance HOLD and proves same-provenance
alias collapse; a same-revision/different-decision-subject control rejects.
The cold plan reviewer found no critical flaw in that mapping.

The fast command above returns `253 passed, 2 deselected in 1.78s`; Ruff passes.
The fresh mounted selector on this final mapping is still pending.

The final-mapping production-clock selector completed with a new failure after
820.58s: `execution resume publication STALE` at
`r14_execution_resume.py:549`. It passed suspension, selected original resolution
and semantic reduction (last committed sequence 26), and passed owner resume
preparation; no resume was selected. This is stronger admission-path evidence,
but still not a successful resume/reopen witness. Checkpoint `d784095` retains
the fixes and explicit pending mounted status; its gate ran `213 passed in
1.76s`. A subsequent cold alias-domain mechanism review found no scoped P0/P1
and ran 35 focused tests; artifact `/private/tmp/r14-alias-domain-redteam.md`.

The fresh same-runtime mcp-debugger run identified the first STALE branch:
`r14_execution_resume.py:512`, fixed 30-second actor-budget expiry in the first
admission guard. Actual stack: `guard -> platform/_sqlite.py:652 _publish`.
All preceding predicates passed: current cut/suspension equal, classification
`SAME_RUN`, current preparation equal to owner result, same worker/session and
decision head. Store current/expected head were both 26, exact physical preimage
matched, transaction was open and zero resume rows had been inserted. The later
184.46-second elapsed reading included debugger pause time and is not an exact
unpaused cost measurement. The non-debugger 820.58-second failure independently
establishes this is not solely a debugger-induced timing problem.

Expectations for every breakpoint were recorded before setup in
`/private/tmp/r14-resume-guard-debug-expectations.md`. Initial attach occurred
before fresh actor issuance; only rejection branches could subsequently pause.
No product clock or deadline changed. The debug selector ended with the same
STALE after 985.99s including pauses; the session was closed and the server
reported zero remaining sessions.

The failed frame also established restricted-witness eligibility for this exact
fixture: canonical runtime/source/resources configuration, supported native loop
prefix, no owner operations outside the existing closed allowlist, no top-level
historical H1 checkpoint descriptor, and native profile V5. A cold full
`$swarm-mode` audited transitive dependencies and proposed a typed invocation-local
exact-journal/config witness, keeping live actor/trust/worker/session/pending and
deadline checks in both guards and retaining the store's actual head/preimage
comparison. Its artifact is `/private/tmp/r14-resume-guard-swarm.md`. General V5
reuse is not justified: historical H1 custody/checkpoint files expand inputs for
unsupported prefixes. Decision guards see the candidate postimage and must not
compare it to the original source preimage. The optimization is not implemented;
the next step is a fast red call-count/no-write guard test before that change.
R14.6 and the full R14 milestone remain OPEN.


### Resume proof reuse implementation — 2026-10-16, verification pending

Implemented invocation-local journal/config input reuse for the closed native V5
prefix and immutable prepared resume artifacts before fresh actor issuance.
Successful fast-path tests require exactly one source collection, one preparation,
and one classification; both writer guards retain live checks. Unsupported inputs
retain full collection. A negative test exposed missing trust bracketing around
initial collection; verified trust is now captured before collection and checked
afterward, then retained through refresh and both guards. This is an input-binding
repair, not a demonstrated production exploit.

Validation command: `uv run pytest -q
 tests/composition/test_r14_resume_publication_guards.py
 tests/composition/test_r14_resume_artifacts.py
 tests/composition/test_r14_resume_journal_inputs.py
 tests/capabilities/agent_loop/test_execution_recovery_preparation.py
 tests/capabilities/agent_loop/test_obligation_resume_transition.py
 tests/capabilities/agent_loop/test_native_recovery_cut_validation.py
 tests/composition/test_r14_historical_suffix.py
 tests/composition/test_r14_native_recovery_cut.py
 tests/composition/test_r14_native_selected_accounting_fast_path.py
 -k 'not selected_native_cut_reopens'` — **285 passed, 2 deselected, 2.71s**.
Ruff and `git diff --check` pass. The previous mounted process was explicitly
terminated after source changed; its result cannot validate this implementation.
Fresh mounted resumed-source test is running with basetemp
`/private/tmp/r14-resume-proof-reuse-trust-20261016`; no successful mounted result
or end-to-end speedup is claimed yet. R14 remains OPEN.


### Resume publication passes; historical OPEN Run drifts on reopen (2026-10-16)

The exact fresh command recorded above terminated with **1 failed in 873.06s**.
It published resume at sequence 27, passed selected-resume read and same-runtime
subset collection, then failed entering the reopened runtime. Startup rejected
`accepted/open recovery selected source cut differs:
current_run,current_run_bytes,cut_fingerprint`. This is a new downstream failure,
not the earlier actor-budget rejection and not a successful mounted witness.

Source inspection: historical accepted facts retain the entire authenticated
current Run lineage. `build_accepted_open_recovery_cut` selected its final Run
although the caller supplied the original source tenant sequence. A resumed Run
therefore changed the rebuilt historical OPEN cut. The current repair selects
the unique latest physical Run publication at or before that source sequence,
requiring same authenticated Run lineage, exact row ID/owner/schema/bytes and
full reference fingerprint. Current full physical commitment checks remain on
both sides; live-cut selection remains current. No caller-supplied Run override,
clearing of obligations, deadline change or permissive ACTIVE-baseline exception.

`uv run pytest -q tests/composition/test_r14_open_cut_run_prefix.py --tb=short`
first gave **3 failed, 1 passed in 0.64s**: the old builder selected the resumed Run
for every bound, even before the first Run. Expanded source-prefix/identity
negatives plus existing source fast paths now give **89 passed in 1.19s** via
`uv run pytest -q tests/composition/test_r14_open_cut_run_prefix.py
 tests/composition/test_r14_native_selected_accounting_fast_path.py --tb=short`.
The new fixture uses real Run bytes and SQLite while isolating source-port
acquisition; it grants no publication authority. A focused unchanged-path reopen
of the terminal failed DB is running through
`/private/tmp/r14_verify_resumed_reopen.py`; its result is not yet available and
cannot substitute for a fresh complete mounted witness. R14 remains OPEN.


### Reopen advances to reduction's later resume interval (2026-10-16)

The focused saved-DB reopen passed the former OPEN-cut check, then failed at
`r14_original_resolver_policy_publication._validate_startup_state`: its selected
policy was physically COMPLETE and actual equalled the anchor, but shared suffix
authentication returned false. Source trace shows shared suffix validation calls
`validate_loop_semantic_reduction_startup`, whose later interval contained resume
at sequence 27. `_authenticate_append_evidence_suffix` admitted readiness and
next-Turn loop publications but rejected resume as unregistered, although the
outer suffix family already admits it.

New real-SQLite interval tests first gave **5 failed, 2 passed in 0.68s** with
`uv run pytest -q tests/composition/test_r14_resume_evidence_interval.py --tb=short`.
The positive hit the exact unregistered-family rejection; four negative cases
also demonstrated the selected-resume validator was never reached. The repaired
branch invokes the existing full selected-resume validator with
`check_history=False`, retaining independent owner reconstruction, exact
suspension/closure/reduction and physical checks. Exactly one frozen original
obligation must match the resume Run and interval's original R16 evidence stream.
Empty/foreign originals, invalid resume, unknown family and physical orphan
reject. This helper fixture isolates the authority validator; it cannot establish
mounted acceptance. A fresh focused reopen with both repairs is running; source
is frozen for that process. R14 and the complete mounted witness remain OPEN.


The second saved-DB probe passed `_prepare_startup` including the original
policy/invocation/resolution/reduction checks, then failed starting agent_loop.
The temporary runner lacked a `__main__` guard: multiprocessing spawn re-entered
its top-level `asyncio.run(main())` (a second `REOPEN_START` was observed). This
is a diagnostic-launcher defect, not a product restart verdict. The runner is now
guarded; `runpy.run_path(..., run_name="__mp_main__")` completes without reopening.
A corrected probe is running; no product source changed for this correction.
Cold scoped reviews `/private/tmp/r14-open-run-prefix-redteam.md` and
`/private/tmp/r14-resume-interval-redteam.md` report no confirmed P0/P1, with
explicit helper-versus-mounted limits. Focused source/recovery/suffix suite:
**265 passed in 2.65s** (the prior 258-test command plus
`tests/composition/test_r14_resume_evidence_interval.py`). Existing resolver/source
and semantic reduction modules: `uv run pytest -q
 tests/composition/test_r14_loop_semantic_reduction_publication.py
 tests/composition/test_r14_original_call_resolution_source.py
 tests/composition/test_r14_original_resolver_policy_publication.py --tb=short`
returned **26 passed in 36.12s**. Ruff and R8 structure/import passed.


The corrected saved-workspace reopen probe terminated **exit 0**:
`STARTUP_PASSED 166.967`, `SUBSET_PASSED 220.314`,
`REOPEN_PASSED_NO_HEAD_ADVANCE_OR_RESEND 220.423` (seconds). Command:
`uv run python /private/tmp/r14_verify_resumed_reopen.py
 /private/tmp/r14-resume-proof-reuse-trust-20261016/test_source_subset_reopens_exa0/native-recovery-accepted-sources.sqlite`.
It used actual persisted workspace/custody and unchanged product validators/clocks,
read the exact ACTIVE resume, collected its subset, preserved tenant head 27 and
compared the persisted provider transfer log before/after (one transfer, unchanged).
This establishes the repaired restart boundary on that failed workspace, not a
fresh complete two-seal witness. The next fresh acceptance discriminator is
`test_accepted_open_recovery_reopens_across_seals_and_rejects_each_foreign_locator`;
its readiness/next-Turn/second-seal/interrupted-recovery/substitution steps remain
required. R14.6–R14.13 remain OPEN.


### R14.6 clause/body audit on f8a6844 (2026-10-16)

This audit reread R14.6 in `R14-REMAINING-ROADMAP.md`, the original completion
ledger, and the actual test/source bodies below. Commands used to locate checks:
`rg -n '^def test_|^async def test_' <named test files>` followed by `sed -n`
on their bodies. A candidate selector is not an executed pass. Existing executed
normal-clock results remain recorded above at their original source revision;
final integrated verification is still required.

| Clause / observable | Inspected mechanism or candidate | Remaining evidence/action |
|---|---|---|
| Exact initialized bytes and complete ordered fanout; no preaccept emission | `test_execution_fanout_runtime.py::test_real_fanout_retains_owner_bytes_and_never_executes_initialized_calls` reads actual retained response and initialized inventory; `test_execution_fanout_records.py::test_resigned_initialized_set_mutations_reject` covers omit/add/duplicate/reorder/manifest/classification | Retain mounted producer→physical read plus fast negative builders in final integrated checks; builders alone do not prove no-write admission |
| Fanout interruption publishes all or none, no owner reissue | `test_execution_fanout_runtime.py::test_selected_fanout_recovers_all_members_without_owner_reissue` faults before COMMIT, asserts zero rows for the pending sequence, forbids owner reissue on startup, compares every recovered physical byte | Run current integrated selector; preserves five-member atomic envelope, not arbitrary stress matrix |
| Both accept/cancel orders with a sibling and no partial companions | `test_execution_call_branch_competition.py::test_selected_acceptance_and_native_cancellation_compete_on_one_initialized_cut`; `test_selected_unmaterialized_cross_family_winner_recovers_before_rival_replays` | Both parameter values required; inspect actual no-partial member sets and zero transfer observations, not only race exception |
| Complete mixed terminal V3 and orphan rejection after reopen | `test_v3_mixed_seal_publishes_reads_after_reopen_and_replays_once` inserts a same-owner/schema orphan at sequence zero after reopen, updates anchor, then requires canonical reader membership rejection, unchanged head/decisions, one V3 | This deliberately reaches full reader beyond selected-only preflight. Retain current-tree execution; no inferred pass from test existence |
| V3 waits for every seal member | `test_v3_interleaves_two_sequentially_accepted_members_across_replay_and_reopen` requires early V3 HOLD, zero V3 publications and unchanged head, then second terminal permits full V3 | Preserve full mounted observations; current test has two accepted members within one seal |
| Three accepted calls / delayed dispatch / distinct outcomes | `test_v3_reads_three_accepted_members_across_delayed_dispatch_and_replay` closes first before accepting second; normal-clock recorded pass at 143b5c1 is SUCCEEDED/FAILED_DEFINITE/SUCCEEDED | This is one seal and two outcome kinds. Owner test additionally covers SUCCEEDED/FAILED_DEFINITE/RECOVERY_REQUIRED; that three-kind combination has only earlier diagnostic mounted evidence |
| Two seals and seal-scoped cross-order replay | `test_v3_accounts_two_sequential_turn_seals_after_reopen_and_cross_order_replay` normal-clock pass at 143b5c1 | Its first seal is cancelled; it does not prove two accepted OPEN causes or interleaved late evidence across seals |
| Accepted OPEN cross-seal interruption/substitution | Running `test_accepted_open_recovery_reopens_across_seals_and_rejects_each_foreign_locator` closes/resumes first cause, creates second seal, loses second recovery COMMIT ack, rejects first outcome ID/decision/sequence substituted into second | Still pending. Even a pass must be compared with the required multi-seal interleaving; do not equate this sequential flow with every interleaving |
| Per-call writer/read/replay qualification; all marked envelopes validated before filtering | `test_public_open_api_forwards_exact_original_call`, `test_open_writer_replays_requested_call_after_validating_all_marked_envelopes`, `test_open_historical_cut_selects_call_seal_among_two_native_executions`, unqualified ambiguity controls in fast_path | Narrow fast tests are green in prior checkpoint evidence; final mounted multi-call OPEN lifecycle remains separate |
| Physical suffix chain, finite families, causal per-terminal semantic prefixes, complete current physical snapshot | `test_r14_historical_suffix.py`; fast_path `test_open_causal_source_joins_captured_prefix_and_excludes_future_closure`, `test_historical_suffix_admits_authenticated_accepted_intents_from_both_run_seals` | Isolated proof boundaries are explicit in test docstrings. Preserve omitted sequence/fork/unknown-family/changed physical row and foreign historical locator negatives plus mounted lifecycle |
| SEND consumption bound to prior SEND/ticket/intent/child | `test_consumption_source_validation_binds_exact_prior_send_without_history_reentry` covers missing/late/duplicate SEND, ticket and intent substitution with real retained body validation | Journal acquisition is isolated in that fixture; retain actual mounted SEND→consumption and physical pending recovery evidence |
| Three distinct A54 limits N/N+1 | `test_call_and_manifest_bounds_include_exact_boundary` checks count and manifest; `test_response_and_complete_owner_output_boundaries` checks owner output; `test_complete_physical_envelope_enforces_its_own_exact_byte_boundary` keeps owner proposal below the total envelope limit in both cases | These are owner/builder tests. Mounted no-write admission/reopen for each independent limit is not yet established by this audit |
| Selected read-only member cannot be filtered or partially accounted | Source frontier returns no complete V3 for an InitializedCall lacking terminal; inspected mounted fixtures contain consequential/proposal members | Need explicit selected read-only whole-seal HOLD/no-write witness. R14.7 retry bound decision is 3, but executable allocation is still unimplemented; do not claim lineage safety from consequential fixtures |
| External source stamp, post-IPC fresh history, actor/worker/session/source admission and CAS | V3 writer keeps stamp outside owner request, reacquires history/projection/facts/request and compares head/commitment after IPC; final guard checks actor/worker/session/anchor/selection | Actual transaction source binding under review: V3 command currently has no expected_preimage_commitment and final guard does not read current physical snapshot. Requires a grounded negative test before accepting this invariant |
| Durable-version and writer admission before execution | Static owner manifests and physical schema variants exist; operation-registry tests explicitly call their declarations inert | This audit found no mounted unsupported-version-tuple/HOLD_ADOPTION witness or complete slice command/handler/record/applicability no-write witness. Inspect/mount these before claiming per-slice admission; broad discovery remains R14.10/13 work |

Specific candidate defects to reproduce after the live run: V3 writer helper
`_authenticated_accepted_open_recoveries` still rejects `len(found)>1` globally,
though its consumer filters by response/call; this conflicts with the intended
multi-seal/multi-call history. `NativeRecoveryCutSource._collect_held` still rejects
an ACTIVE Run retaining suspension_baseline/original_obligations, so readiness
after an authenticated obligation resume needs a discriminating red witness.
These code observations are not yet claimed as mounted failure attribution.
The running test's current code is frozen. Full R14 remains OPEN.

### V3 transaction source and recovery membership (2026-10-16, working tree)

The cross-seal discriminator terminated with `1 failed in 1215.60s` at first
post-resume readiness: `complete authenticated recovery cut unavailable; HOLD`.
The persisted last publication is execution-resume at sequence 28. No second
seal or interrupted second recovery was reached; this is not a two-seal pass.
An attached debugger is inspecting that exact saved workspace without changing
the native recovery collector or product clocks.

`test_r14_v3_writer_preimage.py` uses the actual V3 publication function and
SQLite writer, with source/actor/IPC ports explicitly isolated. Its untampered
control passed. After a raw physical insertion at unchanged tenant head, the
red test observed `('COMMITTED', 1, True, (1,))` instead of
`('STALE', 0, False, (0,))`. The live submit now carries the captured physical
preimage commitment into the existing transactional writer check. The durable
command builder and retained wire format remain unchanged. This proves the
transaction boundary, not mounted source authentication or a cooperative CAS
exploit.

The V3 recovery-envelope extractor now permits distinct (response, call) pairs
across one or multiple seals, rejects duplicate pairs, and parses every marked
envelope before filtering. The isolated extraction test was red on the old
global singleton check and now covers empty/single/multiple/duplicate/malformed
histories. Whole-history and mounted multi-seal authority remain separate.

Command: `uv run pytest -q tests/composition/test_r14_v3_writer_preimage.py
tests/composition/test_r14_v3_open_recovery_membership.py
tests/composition/test_r14_native_sealed_accounting_records.py
tests/capabilities/agent_loop/test_native_sealed_accounting_v3.py --tb=short`
— 19 passed in 0.66s before formatting. Scoped Ruff check passed after formatting.
R14.6 and the full R14 goal remain OPEN.

### Post-resume readiness HOLD: debugger evidence (2026-10-16)

Attached `$mcp-debugger` to the exact sequence-28 saved cross-seal workspace.
Before arming breakpoints, `/private/tmp/r14-readiness-hold-debug-expectations.md`
recorded expected reachability, values, next steps and stack for conditional
breakpoints at `_collect_held` lines 1018 and 1019 (`run.state == 'ACTIVE'`).
Both were reached as predicted. Stack: `_collect_held -> collect_held -> collect
-> main` in `/private/tmp/r14_readiness_hold_debug.py`.

Observed: ACTIVE latest Run, retained baseline present, one original obligation,
zero no-retry references, no selected current suspension, accepted inputs with
both closure and reduction present. Closure.original equals the retained
obligation; closure/reduction IDs match; reduction is CONSUMABLE. The guard
returned None solely because the baseline was present. A prior manual pause
showed real history/owner validation preceding this guard, not actor deadline
failure. Step-out stopped before the caller assigned its result (reading that
local returned NameError); the runner's subsequent assertion independently
confirmed None. No debug evaluation modified product state or clocks.

Runner terminal exit 0 printed `R14_READINESS_SOURCE_NONE_CONFIRMED` and
`R14_READINESS_DEBUG_DONE_NO_HEAD_ADVANCE_OR_SEND`, comparing persisted heads and
transfers before/after. The debugger was closed; `list_debug_sessions` returned
zero sessions. This attributes the first rejection and does not prove a repaired
whole cut or readiness publication.

The collector eligibility regression was red on the old guard. Current working
tree authenticates exact selected resume and current closed original inputs,
while preserving no-retry rejection. Fast eligibility/proof/transition/artifact
command: `uv run pytest -q tests/composition/test_r14_resumed_cut_eligibility.py
tests/composition/test_r14_resumed_cut_proof.py
tests/capabilities/agent_loop/test_obligation_resume_transition.py
tests/composition/test_r14_resume_artifacts.py --tb=short` — 44 passed in 1.51s.
These isolated ports establish control/proof comparisons only. Cold design review
found that retained suspension baseline/pair must additionally be joined into
the complete physical classifier on the resumed path. A saved-workspace whole-cut
check is running; neither these fast passes nor this diagnosis closes R14.6.

The first saved-workspace whole-cut check (eligibility repair only) terminated
exit 1: `resumed native cut remains unavailable`. The subsequent repair derives
`provenance_suspension` from the exact selected resume and reuses the existing
baseline/pair byte/schema/physical membership checks. State-specific suspended
return handling is unchanged. Cold scoped follow-up found the missing join
repaired by inspected code, with no additional confirmed P0/P1; this is not a
mounted pass. The 44-test command above passed again in 1.38s and Ruff passed.

Pending processes, observed live after this repair: saved-workspace check
`uv run python /private/tmp/r14_resumed_cut_check.py
/private/tmp/r14-cross-seal-open-resumed-prefix-20261016/test_accepted_open_recovery_re0/native-v23-prepare.sqlite`
(exec session 69469), and `uv run pytest -q
tests/composition/test_r14_execution_suspension.py --tb=short` (exec session 46787).
Poll these exact handles; no verdict yet. The prior runner session 54597 is
terminal, not a reason to relaunch either current process. Full two-seal and
readiness/next-turn verification remain required after a valid whole cut.

The existing mounted suspension/resume regression terminated successfully:
`uv run pytest -q tests/composition/test_r14_execution_suspension.py --tb=short`
— 2 passed in 86.41s. This preserves the current SUSPENDED publication/reopen
behavior; it is not the accepted-original post-resume readiness witness.

Cold review of the planned saved continuation found two additional fixture/read
boundaries. The cross-seal test reopened two provider scenarios with cap 3,
which the real constructor rejects before runtime startup. Its reopen now keeps
the original cap 2. Separately, provisioning accepts repeated ordered provider
scenarios, but historical custody loading rejected their duplication. Actual
provision-to-historical-read tests for CONFIRM/CONFIRM and LOST/LOST were red
(2 failed, 12 passed), then green after removing only the uniqueness predicate.
The new tests retain issuer key/credential/grant/cap and byte equality, assert
no live provider creation, and keep malformed scenario/type/cap controls.
`uv run pytest -q tests/composition/test_r16_historical_dispatch_custody.py
--tb=short` — 14 passed in 0.13s; scoped Ruff passed.

Combined fast command: `uv run pytest -q
tests/composition/test_r14_resumed_cut_eligibility.py
tests/composition/test_r14_resumed_cut_proof.py
tests/capabilities/agent_loop/test_obligation_resume_transition.py
tests/composition/test_r14_resume_artifacts.py
tests/composition/test_r16_historical_dispatch_custody.py --tb=short`
— 58 passed in 1.45s. The same saved whole-cut process remains pending. It began
before the custody-loader repair, so a subsequent fresh process must exercise
the complete repaired code. No full readiness/two-seal verdict is claimed.

Saved whole-cut session 69469 terminated **exit 0**:
`R14_RESUMED_CUT_VALIDATED 352.007` and
`R14_RESUMED_CUT_DONE_NO_HEAD_ADVANCE_OR_SEND`. It collected the real complete
current cut, independently called `validate_native_recovery_cut`, required ACTIVE
latest Run with one retained original and one closure/reduction, and compared
tenant heads/provider transfers before/after. This proves the repaired source
boundary on the saved workspace; it does not yet prove readiness publication.

Prepared `/private/tmp/r14_saved_cross_seal_continue.py` retains the original
test tail from readiness through second-seal acceptance/SEND, lost recovery ACK,
reopen, three locator substitutions and idempotent replay. Cold plan/static
review found no critical runner blocker after preserving cap 2 and providing
only the second response to the fresh model adapter. `runpy.run_path(...,
run_name='__mp_main__')` confirmed no main side effects. Before any publication
the runner requires exactly head 28, one original OPEN/resume/V3, one transfer,
no pending entry, one Turn and exact latest selected resumed Run. It is now
running on the same saved DB/custody with current code, no product-clock or
validator patches. The only fault injection is the original test's lost ACK
after the second OPEN recovery COMMIT. No continuation verdict yet.

Continuation session 3585 terminated exit 1 before runtime yielded, at owner
attestation identity mismatch during restart generation. The temporary runner
imported the test module at top level; spawn reimported its foreign deployment-
trust policy modules into other owners. This was a diagnostic-runner error,
not evidence of a readiness failure. Main guard alone did not prevent import
pollution. A minimal real V39 start/attest reproduced the identity rejection;
moving every project import inside guarded main and using explicit imports
produced `ATTEST_SMOKE_PASSED 5`, exit 0, with the actual repaired runner's
`--attest-smoke` path. All five expected owners were attested, so this is not an
empty-connection success. No product verifier or clock was altered. SQL tenant
head remains 28 and persisted provider transfers remain 1 after the failed
startup; unchanged startup sidecars are not claimed. Repaired continuation
requires a new process; do not treat session 3585 as live or its failure as a
two-seal verdict.

The READ_ONLY audit found executable-history V3 is currently DTO-only; the real
V2 fanout cannot select a legitimate READ_ONLY member. Unregistered-response
rejection is not the required selected-member whole-seal HOLD witness. Keep that
conditional R14.6 clause OPEN until the real R14.7 registered lineage path exists;
its fixture must terminalize all accepted/cancelled siblings first to ensure a
nonterminal sibling cannot mask improper READ_ONLY filtering. No synthetic row
or mocked selection is accepted as mounted evidence.

Repaired continuation is live as exec session **53458**, last output
`SAVED_CROSS_SEAL_STARTUP`. Poll this exact handle. The superseded session 3585
and red/green attestation smoke processes are terminal. Current product source
is checkpoint `ed4b8bf`; only this ledger and the one-off harness observation
are subsequently modified. No readiness or second-seal pass yet.

Session **53458 is now terminal, exit 1**. Startup and selected resumed-target
checks passed; actual accounting preparation succeeded, but the isolated
continuation owner returned HOLD: `recovered pair, closure, witness or reducer
binding differs`. Readiness was not published and the second seal was not reached.
This supersedes the live status above; do not restart this terminal handle.

Direct typed reads of the saved physical closure/reduction established that
closure.accepted_witness equals reduction.anchor.accepted_witness, but is absent
from reduction.complete_ordered_evidence: the former is an R16 RESOLVE_OBLIGATION
record, while consumed evidence comprises the APPEND_EVIDENCE records. The generic
domain join requires witness membership in that append manifest. The producer
requires an APPEND-only manifest plus a separate resolved anchor. New
`test_original_continuation_witness.py` reproduces the same HOLD in 0.33s using
actual resolver/reducer producer outputs at the explicitly isolated post-cut-
validation projection boundary. It is RED, not mounted authority evidence.
Existing generic omission/duplicate negatives must remain effective. A cold
full swarm is evaluating the typed original-resolver join; no producer bytes,
source manifest or generic predicate have been changed to force a pass.

R14.7 context/contracts preparation is in `R14-7-IMPLEMENTATION-CONTEXT.md`.
Its 44 public contract tests passed in 0.41s and cold scoped design review found
zero confirmed P0/P1, with every runtime guarantee explicitly HOLD. Preflight
found a separate real R8 open regression: commit 58de358 removed both verifier
imports while leaving their canonical open calls. A malformed-source/no-storage
test was red with NameError, then green in 0.24s after restoring imports.
`sh .harness/scripts/test.sh --preflight` and the actual corruption reproducer
both exited 0; no catalog repin or verifier replacement occurred. The R8 import
repair happened while the diagnostic continuation process was alive; its native
collector/join code was unchanged, and this is not a final frozen-source/full
regression result. Handoff 42 retains the remaining original-witness join work.

Continuation follow-up: the producer regression and unchanged generic negative
were rerun together with `uv run pytest -q
tests/capabilities/agent_loop/test_original_continuation_witness.py
tests/capabilities/agent_loop/test_recovery_accounting.py::test_reduction_cannot_change_semantic_class_or_omit_accepted_witness
--tb=short`: 1 failed, 1 passed in 0.31s. The failure remains the exact witness
membership HOLD. Cold implementation-plan review also found the independent
generic join in `execution_next_turn_preparation.py`; the repair must establish
the source-aware original proof at both readiness and next-Turn consumers.
Read-only SQL inspection of the saved fixture still returns tenant head 28;
its persisted transfer journal has one row. No continuation runner is live.
The source-aware repair is in progress, with generic validation unchanged.

The owner-local original continuation join now validates the canonical basis,
closure/outcome/batch, original terminal/opening, separate accepted RESOLVE
witness, and current APPEND manifest in their distinct body/carrier domains.
Both readiness and next-Turn preparation use it; generic witness membership and
durable producer bytes remain unchanged. Tests include exact-source mutations,
native-shaped aliases and a real-producer compatible APPEND successor preserving
the immutable accepted witness. The structural next-Turn fixture now uses these
actual producer anchors rather than invented closure/witness references.

Command: `uv run pytest -q
tests/capabilities/agent_loop/test_original_continuation_witness.py
tests/capabilities/agent_loop/test_recovery_accounting.py
tests/capabilities/agent_loop/test_execution_next_turn_preparation.py
tests/capabilities/agent_loop/test_original_semantic_reduction_preparation.py
tests/capabilities/agent_loop/test_original_call_resolution_preparation.py
tests/capabilities/agent_loop/test_execution_recovery_preparation.py
tests/capabilities/agent_loop/test_execution_recovery_contracts.py
tests/composition/test_r8_open_verification.py --tb=short`
— **110 passed in 1.66s**. Scoped Ruff and `git diff --check` passed.
Cold implementation red-team found no confirmed P0/P1 in this scoped repair;
its independent compatible-append test passed in 0.36s. This is structural
evidence, not selected native authority or successful mounted publication.

Saved continuation process **90670** is live, last observed startup, using the
same head-28/one-transfer fixture. It began before the final tuple-order-only
source edit (final helper blob `93547c685a1ea1886a13abe9e2622f04b1e1f715`), so it
is diagnostic evidence rather than a final frozen-source integration run.
Poll this exact process; no readiness/next-Turn/two-seal verdict is claimed yet.
Handoff 42 remains open for mounted readiness and the rest of that lifecycle.

Process **90670 terminated exit 1 before runtime yielded**: startup rejected
`runtime source drift detected; restart from a stable tree`. The runner began
before the final source edit; this was a root scheduling mistake, and the
startup guard correctly rejected mixed source identities. It is not a
continuation-join failure. Source is now committed as `b12d81a`; read-only SQL
and transfer-journal checks still require head 28 and one transfer. A fresh
stable-source run is required; never poll/restart terminal handle 90670.
Fresh process **23827** is live on committed `b12d81a`, last output
`SAVED_CROSS_SEAL_STARTUP`. `git diff --quiet HEAD -- src` succeeded before
launch. Do not edit source while it is running; poll this exact handle.

Process 23827 has now printed `SAVED_RESUME_AUTHENTICATED_TARGETS`: startup and
the exact saved resumed Run guards passed. Readiness is still pending.

A second owner-boundary regression is now RED:
`uv run pytest -q
tests/capabilities/agent_loop/test_resumed_next_turn_preparation.py --tb=short`
— 1 failed in 0.32s, specifically `next Turn requires an active sealed
non-scheduler Run`. Its suspension and resume are produced by the real owners
and both return PREPARED; the selected-readiness structural bridge passes before
that rejection. This test is cancellation-only with empty original obligations,
not mounted selection or original-obligation continuation evidence. Product
source is still unchanged while the live saved run proceeds.

Prepared the resumed next-Turn repair in isolated worktree
`/private/tmp/chiplog-r14-resumed-next-turn-b12d81a`, leaving the live runtime's
`src/chiplog` unchanged. Its exact suffix proof binds canonical Run preimages,
baseline/pair body-versus-physical references, producer suspension/resume deltas,
frozen originals and current closure identities. The old sealed route and fresh
original continuation proof remain. Root command, run from that worktree:
`PYTHONPATH=/private/tmp/chiplog-r14-resumed-next-turn-b12d81a/src:/private/tmp/chiplog-r14-resumed-next-turn-b12d81a
/Users/thunderbird/Projects/chiplog/.venv/bin/python -m pytest -q
tests/capabilities/agent_loop/test_resumed_next_turn_preparation.py
tests/capabilities/agent_loop/test_execution_next_turn_preparation.py --tb=short`
— **45 passed in 2.76s**; scoped Ruff and diff checks passed. Cold scoped
implementation review found no confirmed P0/P1. Positive producer evidence is
cancellation-only; nonempty accepted recovery and mounted publication remain
OPEN. This candidate has not been transferred to the main worktree.

Natural-run low-volume sample: `/usr/bin/sample 84209 1 10 -file
/private/tmp/r14-readiness-23827-native-sample.txt`, exit 0. In the main active
90-sample stack, 57 samples enter the stdlib JSON encoder, 13 UTF-8 encoding,
10 Pydantic and 8 SHA-256. This one-second native sample establishes active
serialization/hashing at that instant, not an exact Python call site, repeat
count, whole-run profile or SQLite/IPC time attribution. A cold narrow swarm is
selecting a discriminating count-test/debugger acquisition; no global canonical
serializer change has been made from this sample.

Process **23827 terminated exit 1** before readiness publication. Accounting
preparation succeeded; continuation returned `INTEGRITY_FAULT: original effects
carrier differs`. The purported native-shaped test had invented `schema:sha`
as the effects carrier ID. Actual `_row(record)` uses `record.record.head`;
inventory adds `record:sha(raw)` and raw fingerprint separately from the embedded
body marker. Replacing the test projection with the real writer made it RED
(0.85s), then the corrected carrier formula passed. Wrong schema-digest ID,
foreign ID and fingerprint remain negative cases.

The authenticated historical resume cut then exposed a second real shape:
opening and terminal each have a physical and a logical alias of one carrier.
The new alias test was RED in 0.69s. The join now selects the full required
logical reference alongside its existing physical reference; duplicate logical
sources and selected-byte/subject substitution still reject. Outer native
validation remains responsible for the complete inventory and alias consistency.
Actual R16 history/issuance still independently recomputes the outcome marker.

The reviewed next-Turn candidate has now been transferred to the main worktree.
Root ran `uv run python /private/tmp/r14-saved-original-join-check.py`, exit 0:
36 authenticated journal entries, one retained resume at source cut 27;
accounting 1, closure 1 and reduction 1. Appending its exact selected resume
Run/source to that historical cut lets the new suffix recognizer accept lineage
10 with one retained original obligation and return the sealed predecessor.
Head 28, one transfer, transfer bytes and journal sidecars remain unchanged.
This is genuine nonempty historical structural evidence, not authentication of
a current cut, selected readiness or successful mounted next-Turn publication.

Readiness pure command derivation now runs before fresh actor issuance and is
bound using exact invocation-local immutable evidence bytes. Binding still
validates the closed receipt and tenant. Public `command()` and all replay/
restart paths still independently reconstruct owner results; live cut/actor/
worker/session/commitment guards remain. The red count test observed one extra
accounting and continuation preparation after issuance. Its green expectation
is now **2/2 before issuance** (exchange plus local validation), **0/0 after**.
Binding adds zero preparations; independent public reconstruction adds one each.
One-sided corruption, a separately valid foreign exchange, foreign tenant and
invalid/missing receipt reject. Existing stale final scan tests use a valid
closed receipt and still reject both changed-cut and expired-actor cases.

Command: `uv run pytest -q
tests/capabilities/agent_loop/test_original_continuation_witness.py
tests/capabilities/agent_loop/test_resumed_next_turn_preparation.py
tests/capabilities/agent_loop/test_execution_next_turn_preparation.py
tests/capabilities/agent_loop/test_recovery_accounting.py
tests/composition/test_r14_readiness_artifacts.py
tests/composition/test_r14_recovery_pending_prefix.py --tb=short`
— **117 passed in 5.31s**. Scoped Ruff/diff checks and cold bounded reviews passed
without confirmed P0/P1. Full-cut decision recollection remains expensive; this
does not establish production-deadline passage. A new stable-source mounted
continuation is required; 23827 is terminal and must not be polled as live.

### Native JSON decoder compatibility and exact repeat count (2026-10-16)

The pending-row decoder already uses `orjson` since `06b597a`; Pydantic JSON
validation and canonical serialization are separate paths. A fresh MCP debugger
probe of `test_public_accept_seeds_exact_history_cache_and_main_commitment_change_misses`
observed exactly 20 decoder calls (logpoint counters 0 through 19) for 20 journal
entries. At the post-loop breakpoint, `(len(decoded_rows), len(expected_entries),
decoded_rows == [payload for _, _, payload in expected_entries])` was
`(20, 20, True)`. Parent-only debugpy attachment avoided child-owner debugger
startup interference; product deadlines were not changed. The test passed in
43.67s under the debugger; this is call-count evidence, not latency evidence.

The decoder compatibility check exposed silent oversized-integer conversion:
`1208925819614629174706177` became a float. The new numeric value/type regression
was RED with 12 failed and 12 passed cases. The decoder now iteratively checks
its result for floats and reparses float-bearing values with stdlib, preserving
exact oversized integers and float behavior. Integer/string-only records retain
the native path. No canonical bytes, validation or authentication are changed.

Read-only benchmark command:
`.venv/bin/python /private/tmp/r14-json-compat-benchmark.py`.
Three largest payloads from the authenticated saved 36-entry journal were
78,746,139, 55,155,708 and 25,271,460 bytes. Median calling-thread CPU over three
passes: stdlib 0.30189s, native decoder 0.19950s, candidate float guard 0.20345s.
A conservative digit-regex guard took 0.35426s and was rejected. These are local
parse-only measurements on exact saved bytes, not end-to-end R14 speedups.

Command: `uv run pytest -q tests/composition/test_call_history_witness_mounted.py
-k 'pending_json_decoder or r8_registers_native_json or public_accept_seeds_exact'`
— **27 passed, 17 deselected in 23.09s** after the fix. Scoped Ruff and diff checks
passed. Full R14 and the repeated full-cut readiness guard remain OPEN.

### Readiness custom-source writer baseline (2026-10-16)

Command: `uv run pytest -q
tests/composition/test_r14_readiness_guard_collection.py --tb=short`
— **2 passed in 1.39s** on the main checkout. The fixture uses genuine pure
owner preparations and closed wire evidence, inert source/actor ports, and the
actual SQLiteMaterializer/EventAppender. It grants no native source authority;
its in-memory decision list and stubbed finish do not prove durable recovery.

For this custom source, two full collections are required: initial collection
and final decision collection. Both writer guards run once; the positive case
commits both exact records and advances head 1 to 2. The negative case advances
only a synthetic actor clock during the second collection, gets exactly
`STALE:authority-after-cut`, and verifies complete SQL rollback: no candidate
records/publication/decision, head 1 and unchanged physical commitment. This is
not a measurement of real collection latency or a red test for native reuse.

The proposed one-collection contract remains unimplemented and must first be
tested on an actually eligible native V5 resumed-originals path. A custom-source
test expecting one collection would incorrectly weaken the required fallback.
Root also ran `.venv/bin/python /private/tmp/r14-writer-snapshot-visibility.py`:
separate workspace read connections see the committed preimage in both writer
phases, while the writer connection sees its candidate postimage at decision.
Thus postimage visibility is not a reason to remove the final full cut.

### Saved cross-seal final-guard failure and live physical CAS (2026-10-16)

Command: `.venv/bin/python /private/tmp/r14_saved_cross_seal_continue.py
/private/tmp/r14-cross-seal-open-resumed-prefix-20261016/test_accepted_open_recovery_re0/native-v23-prepare.sqlite`.
Process 32005 / execution handle 38487 is **terminal, exit 1**. It reached
`SAVED_RESUME_AUTHENTICATED_TARGETS` and failed in the actual writer's readiness
decision guard with `STALE:authority-after-cut`. It never returned readiness,
started next Turn or selected the second seal. Main source bytes stayed at
`c821421` throughout; intervening commits changed tests/docs only.

Read-only SQL after exit found head 28 and zero readiness publications. Custody
still has one transfer, SHA256
`71b48c233854dfd66cb804845b3e3cc0fee935ffff1277c641432da0d7fe6db9`.
The after-cut actor check failed; the swallowed inner exception was not captured,
so its exact cause is not claimed. A one-second native sample at 10:12:20 +0500
showed the main event loop waiting and asyncio_0 doing JSON encoding, consistent
with writer work. It does not identify an exact Python caller or whole-run cost.
External nonblocking Python stack sampling was unavailable without root on macOS.

Separately, the live readiness submit now binds
`expected_preimage_commitment=cut.materialization_commitment`. Both full cut
collections and all live guards remain. The public/durable command stays
unchanged: only the transient command passed to the appender has this CAS field.
The actual-submit assertion was RED in all three cases (missing preimage).
After the one-field fix, real SQLite rejects same-head physical tamper before
either guard, preserving exactly the injected row and post-tamper commitment,
with no candidate/publication/decision. Positive publication retains both
guards and two collections; synthetic actor-expiry still rolls back. SQL REPLAY
retains existing short-circuit behavior and does not claim fresh CAS validation.

Command: `uv run pytest -q tests/composition/test_r14_readiness_guard_collection.py
tests/composition/test_r14_readiness_artifacts.py
tests/composition/test_r14_recovery_pending_prefix.py --tb=short`
— **23 passed in 3.57s** on main after exact transfer; scoped Ruff/diff passed.
The first tamper fixture needed correction to read its commitment inside a real
transaction; that fixture error is not a product failure. Native input-witness
reuse still has open dependency/eligibility and fallback/replay obligations;
no second cut has been removed. Full cross-seal and R14 remain OPEN.

### Actual resumed-native input acquisition and isolated reuse candidate (2026-10-16)

Command: `.venv/bin/python -m debugpy --listen 127.0.0.1:5690
--configure-subProcess false --wait-for-client
/private/tmp/r14_native_readiness_inputs_probe.py`, attached through MCP debugger.
Execution 99484 / PID 72056 is terminal, exit 0; the debugger session is closed.
The probe used the unchanged saved head-28 workspace and HEAD4452ebb source.
One conditional native-entry logpoint was reached. At the post-collection
breakpoint the actual frame evaluated to
`(False, True, True, True, 28, 'ACTIVE', 1, 34)` for absent-cut, available-before,
journal/config equality, verified-trust equality, head, Run state, original count,
and distinct audited event/target pairs. Final assertions additionally proved
one V5 profile, response, turn, original closure and reduction, with a suspension
baseline. This is source acquisition, not readiness publication.

The report `/private/tmp/r14-native-readiness-inputs-probe-report.json` records
the same SQL commitment/head, zero readiness publications and one custody transfer
before startup and after acquisition; the script also asserted equality after
runtime close. It does not claim all sidecars were unchanged. A manual debugger
pause inside collection showed
`_collect_held -> _selected_accepted_recovery_inputs -> capture_request ->
read_selected_original_call_resolution -> _read_call_history ->
validate_selected_command -> prepare_original_call_resolution -> reference`.
Thus an actual full collection re-enters original-resolution preparation; this
is not a measured whole-run cost attribution.

Root and a cold, no-parent-context `gpt-6.1-sol` low swarm-red-team inspected the
15 unmapped audit rows. Journal lock files coordinate authenticated readers;
the gate journal/SQL are freshly authenticated by final `_require_no_pending`;
commitment pathname/descriptor reads remain live anchor checks; the read-only
SQL URI denotes the authority database covered by writer preimage CAS; in-memory
databases derive historical copies. Checkpoint operation families remain excluded
by the closed journal/owner allowlists. No P0/P1 omitted dependency was established
for this exact subset. Open-event auditing is discovery, not exhaustive proof of
stat/native/unreached dependencies, and this source audit is not mutation evidence.

The invocation-local reuse candidate was developed in the isolated checkout
`/private/tmp/chiplog-r14-readiness-witness-4452ebb` and transferred byte-for-byte
to main after the actual acquisition and source dependency audit. Unavailable or changed
auxiliary inputs permanently disable reuse for that call and restore the original
full decision collector; all live guards and CAS remain. Root read the cold plan
and fallback reviews. Initial missing-behavior tests were red (5 failures), then
the candidate's expanded witness/custom-writer controls passed 13 tests in 1.47s.
Additional real-writer controls explicitly fabricate eligibility and input pairs
only to exercise guard wiring: the unchanged-input control was RED against main
source (collect 2 instead of 1), then the candidate passed 24 witness/writer tests
in 3.29s. Cases cover unavailable before/after capture, changed after/admission/
decision inputs, permanent invalidation despite restored inputs, semantic-equal
fallback acceptance, changed-cut rollback and actor expiry during fallback.
Those fabricated controls grant no native authority. A fresh native
cross-seal test with exact one-collector/two-guard assertions ran under
ordinary product clocks (execution 57173). It is now terminal: **1 failed in
2482.66s**, after SQL readiness publication at sequence 29 following resume28.
`_finish -> validate_selected` refused the selected cut/exact publication at
`r14_recovery_publication.py:431`. The post-publication count assertions were
not reached, so neither exact native invocation counts nor resumed workflow
completion are established. No next Turn/second seal or R14 completion is claimed. Main now retains the full decision collection
for every unsupported, unavailable or invalidated witness; only the closed native
resumed-original subset with unchanged inputs may reuse its initial cut.

After exact transfer, command `.venv/bin/pytest -q
tests/composition/test_r14_readiness_guard_collection.py
tests/composition/test_r14_readiness_input_witness.py
tests/composition/test_r14_readiness_artifacts.py
tests/composition/test_r14_recovery_publication.py --tb=short`
passed **40 tests in 62.45s** on main. Scoped Ruff and diff checks passed.

After native57173 failed, root preserved the full 43-file head-29 bundle at
`/private/tmp/r14-readiness-postcommit-preserved-head29` before diagnostic reopen.
The runner used the exact frozen isolated sources and original custody/manifest,
with no explicit SEND/publication or product-clock patch. Startup refused earlier
than the original readiness predicate: historical semantic-reduction validation
called `_authenticate_append_evidence_suffix` and rejected its pending publication.
The SQL head and authority commitment stayed unchanged; SQLite WAL/SHM and the
derivative gate cache changed, so universal sidecar immutability is not claimed.

MCP debugger then reached both the precondition and exact rejection line under
`current_sequence == 29`. The observed stack was startup ->
`validate_selected_executions -> _read_call_history:778 ->
_authenticate_append_evidence_suffix:299`. Scalars were base26/end26/current29,
one pending readiness29, predecessor equal to anchor, resulting equal to actual,
and physical `inspect_publication == COMPLETE`; both adjacency predicates were
false. This diagnoses a historical-interval refusal during reopen. It does not
yet prove the original `_finish` refusal has the same inner cause. Both debugger
sessions were closed after terminal exit1. The original refusal remains an open mounted requirement.

The bounded real-SQL control first failed specifically at that adjacency guard
(**1 failed in 1.04s**, after correcting fixture setup errors). Root selected a
separate structural bridge from the historical end to the current anchor. A cold
design audit rejected bare adjacency deletion because an empty historical interval
would accept substituted equal commitments without proving linkage. The bridge
retains exact pending head/predecessor/result/COMPLETE checks, requires unique
contiguous selected steps and exact physical membership, and grants no semantic
evidence authority. The original interval semantic validators remain unchanged.
Root read the narrow implementation review: 0 demonstrated P0/P1.

Command `.venv/bin/pytest -q tests/composition/test_r14_resume_evidence_interval.py
tests/composition/test_r14_original_call_resolution_source.py
tests/composition/test_r14_loop_semantic_reduction_publication.py --tb=short`
passed **45 tests in 1.80s**. The control suite includes the late pending positive,
old adjacent/base/current-anchor branches, owner bridge, gap/duplicate/fork,
forged empty commitment, foreign headers, unknown loop operation, changed/orphan/
rival physical membership and exact pending negatives. It uses real SQLite and
commitments but synthetic journal accessors and unrelated witness stubs: these
checks are not native authority proof.

A new frozen candidate checkout
`/private/tmp/chiplog-r14-pending-bridge-0846538` progressed through the old
interval guard during actual saved-bundle startup. MCP then reached
`validate_selected:430` via `_read_call_history:1265`, with actual
`(command_equal, authenticated, through_sequence, loop_cut_is_none, cut_head)`
**`(True, False, None, False, 28)`**. Thus the bridge clears one observed barrier,
while native historical readiness authentication still refuses. No successful
reopen, finish, next Turn, second seal or R14 completion follows from this slice.

Further MCP acquisition isolated `_authenticate_v3_accepted_cut:440`. For
`chiplog.execution.accepted-open-terminal-disposition.v1`, owner, physical SHA256
and selected record bytes matched; only selected-decision comparison failed.
The source used decision head as its subject, while journal membership used the
command identity. Their full revisions were equal. A bounded scan found exactly
ten original/reduction schemas with this representation, all with equal revisions
and source subject equal to decision head. Root read their actual producer paths.

The closed alias repair preserves producer/retained bytes and existing physical
membership checks, accepts only those ten schemas under `agent_loop`, and still
requires the complete decision revision and subject=head. Unknown-schema,
cross-owner, changed head/fingerprint and foreign subject aliases fail. Tests
using the actual source constructor and actual original-resolver anchor projection
were RED (**11 failed, 5 passed in 1.51s**). After repair, command
`.venv/bin/pytest -q tests/composition/test_r14_native_recovery_cut.py
tests/composition/test_r14_original_obligation_sources.py
tests/composition/test_r14_original_call_resolution_publication.py
-k 'not selected_native_cut_reopens' --tb=short` passed **66 tests in 1.35s**;
two mounted scenarios were deselected. Scoped Ruff passed.

In the next actual saved-bundle probe, the final readiness predicate became
**`(True, True, None, False, 28)`**. The process nevertheless terminated exit1:
the subsequent deferred OPEN causal-source fold reached the effects reader,
whose later-suffix callback refused the still-pending physical readiness delta.
SQL head29 and commitment stayed equal before/after; WAL/SHM and derivative gate
cache changed. Thus native selected readiness authentication now passes this
probe, while startup completion, finish and the two-seal scenario remain OPEN.

The effects callback now dispatches exactly one pending readiness to the existing
exact recovery-prefix verifier. This is local to deferred OPEN source validation;
the shared pending wrapper and its second native-accounting consumer are unchanged.
Cold review caught the initial incorrect sole-consumer assumption; root narrowed
the repair accordingly and read the implementation review (no demonstrated P0/P1).
The verifier's False is returned directly, with no generic-success fallback.
It opens historical prefix access only; canonical readiness authentication and
complete effects membership remain separate checks.

Real-SQL routing controls were RED (**2 failed in 1.25s**) on the old callback:
valid pending returned False, while a damaged result reached a generic True.
After repair, prefix/historical-suffix/native-fast-path suites passed **182 tests
in 3.88s**. The combined nine-file scope (native recovery cut, original source
projections/publication, pending prefix, historical suffix, native fast path,
resume interval, resolution source and semantic reduction) passed **293 tests,
2 deselected in 5.97s**; the two omitted tests are mounted lifecycle scenarios.
Scoped Ruff and diff checks passed. The new exact saved-bundle probe is still
running; its logpoint has shown readiness command/authentication True/True.
There is no terminal startup/reopen/continuation result for this candidate yet.

### Further saved-bundle startup barriers and exact repeat count

The preceding probe subsequently terminated at V3 request reconstruction. Actual
MCP stopped in `r16_effects:501` with a present but False later-suffix callback,
then in `prepare_sealed_accounting_v3:745` with accepted outcome None for the same
original call and cut18. `136bde8` added the exact readiness-prefix route to this
separate callback. Real-SQL routing/corruption controls were RED (4 failed in
1.45s); after repair, actual MCP received the outcome at that same cut. This
remained prefix-read permission, not complete selected-cut authority.

The next probe refused the exact-history early pending gate. `0bcb6f7` explicitly
routes readiness through the existing exact prefix verifier, then retains full
selected validation. The real ABSENT/COMPLETE test was RED (2 failed, 2 passed in
1.35s) after correcting noncanonical fixture JSON; that setup failure is not RED
evidence. Actual MCP then observed authenticated=True, lifecycle-deferred=False,
exact-history=True, one pending readiness and actual different from anchor.
Startup progressed past V3/OPEN validation but terminated in resolver-policy
startup: its old COMPLETE publication was refused while C29 was physical and C28
remained anchored. SQL head29/commitment/transfers stayed unchanged; WAL/SHM and
the derivative gate cache changed. Full startup still did not pass.

During that acquisition, a breakpoint found a second sequential exact-history
read from the real OPEN builder. `_current_cut` already had local history but
forwarded its original None argument. `9e18a33` forwards the local tuple only on
the existing held historical selected path. The real caller plus real builder
test was RED (4 failed, 6 passed in 1.36s), including observed count 2 instead of
1; after repair it requires exactly one read, or zero for a supplied tuple.
Initial/final SQL, anchor and identity checks remain. Its checkpoint passed 15
tests in 1.15s. This is an exact count result, not a measured native wall-time gain.

The current startup repair keeps all validation before recovery writes. A cold
design audit found that simply removing old equality guards would admit other
pending families; the implemented wrapper instead requires one exact pending
readiness and no owner/gate pending before generic target-to-current proof.
Semantic reduction separately proves its interval only through the independently
anchored predecessor, excluding pending readiness. Generic suffix and recovery
startup retain full readiness source authentication. No lifecycle allowlist or
startup write reordering is introduced.

The new real-SQL seam was RED (5 failed, 17 passed in 2.79s): two COMPLETE
reduction cases and three old-family caller barriers. The earlier fixture
frontier/sequence mismatch was corrected before this RED. Four semantic cases
exercise ABSENT/COMPLETE and an intermediate resume, using real retained reduction
and readiness producers, interval/prefix proof and physical catalog; resolution,
witness and resume-envelope authority are explicit controls. The real startup
caller rejects structurally valid but source-unauthenticated readiness before any
recovery call. Damage controls preserve SQL bytes, selected journal and anchor.
Caller routing controls force generic success but still reject other registered,
unknown, multiple, owner/gate and bad-prefix states; generic failure and the old
anchored path are also checked. These do not prove full native source provenance.

Command `.venv/bin/pytest -q tests/composition/test_r14_readiness_startup_suffix.py
tests/composition/test_r14_recovery_pending_prefix.py
tests/composition/test_r14_historical_suffix.py
tests/composition/test_r14_resume_evidence_interval.py
tests/composition/test_r14_loop_semantic_reduction_publication.py
tests/composition/test_r14_original_call_resolution_publication.py
tests/composition/test_r14_native_selected_accounting_fast_path.py --tb=short`
passed **299 tests in 10.91s**. Ruff and diff checks passed. The new saved-bundle
MCP acquisition (execution43577) is running on frozen repaired sources with no
explicit SEND/publication or clock patch; there is no terminal native startup
result yet. Fresh full two-seal production-clock verification, durable version
admission and all remaining roadmap requirements stay OPEN.

The acquisition43577 subsequently terminated with exit1 at the same resolver-policy
startup barrier. It did not reach either startup-success breakpoint. Its report
keeps SQL head29, authority commitment and custody transfers unchanged; derivative
gate cache and SQLite sidecars changed. The MCP session is closed. The generic
suffix predicate is still refusing this native history; the preceding local
startup tests do not prove that predicate on the saved bundle.

That run also exposed another sequential history acquisition, this time from
effects Run reconstruction during the historical OPEN fold. `_current_cut` had
history but passed no effects snapshot for pending readiness. The new route adds
only readiness to the existing no-Run OPEN cases, preserving selected-source
suffix verification and exact physical Run/frontier checks. Command
`.venv/bin/pytest -q tests/composition/test_r14_native_selected_accounting_fast_path.py
-k 'open_historical_cut_selects or effects_run_snapshot_reuse' --tb=short` was
RED: **1 failed, 12 passed in 1.49s**. After repair, the complete fast-path file
passed **97 tests in 2.51s**. Real SQLite Run reconstruction asserts exactly zero
history acquisitions with a supplied snapshot and one without it, and rejects
missing Run, changed bytes and stale frontier. This is a local count improvement;
there is no new native wall-time or full startup PASS.

The next diagnostic acquisition runs only policy startup in a labelled diagnostic
process against the same head29 bundle, then aborts before R14 recovery writes.
It bypasses earlier startup validators only to locate the rejected suffix seam;
its result cannot establish ordinary startup success. The frozen candidate includes
the effects forwarding change. Breakpoints observe the readiness-prefix rejection
and generic suffix exception handler. All roadmap completion claims remain OPEN.

That diagnostic reached the generic exception handler, not the readiness-prefix
rejection. `sys.exception()` was `OwnerJournalIntegrityError`; its traceback was
generic suffix1130 → semantic reduction startup427 → R14 runtime587. The old
reduction was marked and physically COMPLETE, but the generic materialized helper
required current actual C29 to equal anchor C28 before the startup validator could
prove the pending-readiness interval. The diagnostic terminated with exit1 and its
MCP session closed; SQL head29, commitment and custody transfers were unchanged.
Only the derivative gate cache and SQLite sidecars changed.

The startup reduction lookup now reads the exact marker using
`_loop_decision_record(..., entry)`. Its physical inspection, historical interval,
readiness-prefix and complete catalog checks remain independent. Generic
`_loop_decision_materialized` and recovery/writer call sites are unchanged. Tests
now bind real pending-marker parsing, exact decision lookup, R14 physical inspection
and materialized lookup; persistence authentication, resolution/witness/resume
authority remain explicitly controlled. The upgraded positive test was RED:
**2 failed, 2 passed in 1.84s**, both COMPLETE-readiness cases throwing the same
error. A preceding wrong class import was a setup error, not RED evidence.

Command `.venv/bin/pytest -q tests/composition/test_r14_readiness_startup_suffix.py
tests/composition/test_r14_loop_semantic_reduction_publication.py
tests/composition/test_r14_recovery_pending_prefix.py
tests/composition/test_r14_historical_suffix.py
tests/composition/test_r14_resume_evidence_interval.py --tb=short` passed
**209 tests in 10.23s**. Own ABSENT/COMPLETE and marked/unmarked crash windows,
orphan/duplicate/unknown markers and a journal decision changed after selection
are checked. The existing real startup caller still rejects synthetic source
authority before recovery. An added assertion requires the generic R14 helper to
retain its stricter C29/C28 rejection; the startup file then passed **48 tests in
6.49s**. Cold bounded design audit found no P0/P1, with no claim of independent
external verification. Ordinary native startup is being observed separately on
frozen sources (execution5896); no successful terminal result is recorded yet.

Execution5896 subsequently finished with **exit0**. MCP reached the ordinary
runner's STARTUP_PASSED breakpoint after `__aenter__`; pending was empty and the
authority anchor was C29. The generic suffix exception breakpoint did not fire.
The same process continued through explicit full selected-readiness validation
and closed successfully; its MCP session is closed. The before/after report
`/private/tmp/r14-after-marker-repair-probe-report.json` preserves SQL head29,
authority commitment and custody-transfer digest. Recovery advanced the anchor
C28→C29 and appended the materialized marker; journal/head, anchor, broker,
read-ledger, gate cache and SQLite sidecars changed. This is saved-bundle startup
and readiness evidence, not universal file invariance or fresh two-seal completion.

A new detached worktree at `2e7c27f25fba522083c8f16835347b2d26b0667c`,
`/private/tmp/chiplog-r14-cross-seal-2e7c27f`, freezes source, tests and config.
The exact input SHA256 manifest is
`/private/tmp/r14-fresh-cross-seal-2e7c27f-inputs.json`; chiplog imports from that
worktree's src. Collection found exactly one requested test. No inherited pytest
options/plugins were set; installed plugins are asyncio and anyio, with no timeout
plugin. The fresh production-clock command is the original interpreter's
`python -m pytest -q
tests/composition/test_r14_native_selected_accounting_source.py::test_accepted_open_recovery_reopens_across_seals_and_rejects_each_foreign_locator
--basetemp=/private/tmp/r14-fresh-cross-seal-2e7c27f`, run in that worktree with its
src on PYTHONPATH. Execution6529 terminated with exit1: one failure in4554.71s.
Stdout is `/private/tmp/r14-fresh-cross-seal-2e7c27f.out`. All1236 frozen input
hashes were rechecked after termination. Product clocks/TTLs were unchanged.
Its fixture uses the pre-gate V1 genesis and cannot establish durable-version
admission.

The first OPEN recovery, resolver resolution, semantic reduction, same-Run resume,
and next-Turn publication passed. The second capture failed in
`selected_prior_turn_context`: the projection required a direct-terminal
disposition for an original RECOVERY_REQUIRED call with a selected recovered
outcome. Second seal, reopen, and foreign-locator rejection were not reached.
The complete45-file failure bundle is preserved separately at
`/private/tmp/r14-fresh-cross-seal-2e7c27f-preserved-failure`; streaming SHA256
confirmed each copy matches its source. Neither bundle was reopened for repair.

The repair adds the recovered-OPEN projection variant, reusing the existing exact
original-continuation proof join. It preserves the original OPEN terminal and
selected `RecoveredCallOutcomeRecord`, including canonical recovered status.
The compressed fixture `tests/composition/fixtures/r14_resolved_open_next_turn_v1.json.xz`
contains the actual failed next-Turn publication, not synthesized proof. Its
compressed SHA256 is ad7f65de88ea2d739679918d59a4872f508ee1a958c1194ea8f5a538409c053b;
decompressed SHA256 is451f8e8fecb82e960bdb093168ef3ac2e8e69e6c8f362ca8660eb90bf3869901.
It proves retained projection behavior, not live source authority or startup admission.

The actual retained input reproduced the failure before repair. MCP session
ca73e531-1d68-4139-865f-71339d403aef observed zero direct-terminal matches,
two legitimate physical/logical OPEN aliases, and one recovered outcome.
After repair, the isolated probe reached the projected-result breakpoint with
one result, original RECOVERY_REQUIRED terminal and R14_EFFECT_CONFIRMED outcome;
the old failure breakpoint was not reached. The process terminated exit0 and
the debugger session was closed. Report:
`/private/tmp/r14-resolved-open-context-probe-result.json`.
The native two-seal test now additionally checks the actual rendered prior-Turn
result envelope; that complete scenario has not yet been rerun after this repair.

Checkpoint `2e7c27f` adds only existing-lock acquisition without provisioning;
33 lock/process/thread/fork checks and 18 neighboring consumer tests passed.
Three actual R7 startup admission tests remain RED in the owned worktree
file `test_r14_startup_version_admission.py`, outside that primitive checkpoint.
They show constructors/runtime admission and broker/read-ledger writes, plus
recreation of an absent lock. Handoff54 preserves the missing mandatory
authenticated CURRENT/tuple/initializer/content/recovery implementation. Cadence
retro `de5ecfe` records these evidence boundaries; all remaining R14 stays OPEN.

### Repaired prior-Turn projection checkpoint and fresh rerun

Checkpoint `4acbc4f` preserves the recovered-OPEN projection, actual retained
fixture and native rendered-envelope assertion. The checkpoint command selected
`tests/composition/test_r14_resolved_open_prior_context.py` plus the three owner
files `test_execution_next_turn_preparation.py`, `test_resumed_next_turn_preparation.py`
and `test_original_continuation_witness.py`:88 passed in26.25s. The new13 cases
exercise the real retained parser/projection, not current live admission.

The complete native selector is now running once at frozen `4acbc4f` in
`/private/tmp/chiplog-r14-cross-seal-4acbc4f`, exec handle55741. The before-input
manifest covers1368 tracked source/test/config/harness files:
`/private/tmp/r14-fresh-cross-seal-4acbc4f-inputs.json`. Import path was verified
inside that worktree; collection found exactly one test. Command:

```sh
PYTEST_ADDOPTS='' PYTEST_PLUGINS='' PYTHONPATH=/private/tmp/chiplog-r14-cross-seal-4acbc4f/src /Users/thunderbird/Projects/chiplog/.venv/bin/python -m pytest -p no:timeout -q tests/composition/test_r14_native_selected_accounting_source.py::test_accepted_open_recovery_reopens_across_seals_and_rejects_each_foreign_locator --basetemp=/private/tmp/r14-fresh-cross-seal-4acbc4f
```

Stdout is `/private/tmp/r14-fresh-cross-seal-4acbc4f.out`; receipt is
`/private/tmp/r14-fresh-cross-seal-4acbc4f-receipt.md`. Production clocks/TTLs are
unchanged, timeout plugin disabled. Frozen inputs must remain unchanged until
authoritative terminal, then all hashes must be rechecked. Observation expiry
does not authorize restart. No terminal verdict yet; V1 genesis still excludes
version-admission evidence even if this lifecycle passes.

At `4acbc4f`, `.venv/bin/pytest -q tests/composition/test_r14_startup_version_admission.py --tb=short`
reconfirmed3 failures in2.31s (stdout `/private/tmp/r14-startup-version-admission-4acbc4f-red.out`).
Authentic V1 startup entered journal/materializer/supervisor and modified broker
and read-ledger SQL instead of typed refusal; missing-lock startup also recreated
the lock; fresh public startup yielded without INITIALIZE. These are executable
admission failures, not merely missing shape coverage. The owned RED file remains
outside the projection checkpoint and has no xfail or weakened expectation.
