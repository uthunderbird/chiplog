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
prefix helper is tested but not yet wired into the causal fold; it grants no
authority and does not by itself fix the OPEN-family composition guarantee.
