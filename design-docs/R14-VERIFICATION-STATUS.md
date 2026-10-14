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
| Three accepted members with distinct dispositions | `test_v3_reads_three_accepted_members_across_delayed_dispatch_and_replay`; owner `test_v3_accounts_three_accepted_members_with_distinct_existing_outcomes` | Mounted candidate currently expects SUCCEEDED / FAILED_DEFINITE / SUCCEEDED. Owner-only test covers RECOVERY_REQUIRED as third distinct disposition. Mounted distinct-disposition witness remains insufficient. |
| Dispatch after another member terminalizes | Three-member mounted selector closes first before accepting second | Candidate covers delayed dispatch; execution result on current tree still needed. |
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
