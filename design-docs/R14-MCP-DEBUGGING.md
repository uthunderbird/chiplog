# R14 MCP debugger investigation — 2026-10-15

## User-required protocol for subsequent debugger probes

Updated 2026-10-15. Before launching or continuing a hypothesis probe, record
the ideal behavior and, for **each** planned breakpoint: whether it should be
reached, its condition where applicable, expected local values, expected immediate
next steps, and the expected stack frames. Use multiple discriminating breakpoints
and conditional breakpoints where they distinguish the relevant branches.
Name the observation that would falsify the hypothesis. Record actual stops,
values, steps and stack separately from these prior expectations. Strongly relax
or disable debugger timeouts; do not confuse a tool yield with target completion.

## Recovered starting point

The long session is
`~/.codex/sessions/2026/09/29/rollout-2026-09-29T17-21-09-01a0ed1c-976d-77e0-a05b-d24775dcf3f4.jsonl`.
Its last visible work concerned second-Turn writer admission in
`test_v3_accounts_two_sequential_turn_seals_after_reopen_and_cross_order_replay`.
Admission expiry prevented terminal, V3 replay and reopen assertions.

The user requested relaxed deadlines during debugging. The full diagnostic
launch uses a temporary `sitecustomize.py` that scales `time.monotonic_ns()`
by 1/10000 consistently in the parent and spawned owners. Production clocks
and deadline checks are unchanged. Diagnostic wall-span fields and profiler
tables are not performance evidence under this configuration.

## Observations collected before this log was introduced

- In a fresh mounted fixture, the outer history reader had a verified H1 Prepare
  tuple; its nested accounting decoder received no tuple. Retained Prepare
  SHA-256 and issuance reference matched across both frames.
- After restarting with the propagation change, both frames held the same
  `verified_h1_prepares` object. A real mounted test passed with the exact key,
  validated normally on separate Prepare-bytes and issuance-key misses, and
  rejected a substituted scope tuple: `1 passed in 88.67s` using
  `./.venv/bin/python -m pytest -xq tests/composition/test_r14_next_turn_publication.py::test_historical_accounting_uses_exact_read_local_prepare_and_rejects_bad_scope`.
- The relaxed full diagnostic selected the second call for the second distinct
  seal. This does not prove a pass with production deadlines.
- Another stop showed `accounting_envelope` rebuilding `accounting_command` for
  the same 26 MB retained evidence. One shared artifact construction preserves
  the command fingerprint and envelope bytes. The isolated saved-input probe
  is separate from mounted performance evidence.

## Probe 1 — recovery source at reopen

Recorded before observing the reopen breakpoint.

**Ideal behavior:** reopen receives the same registered native recovery source
as the first runtime. Selected readiness is authenticated from its retained
historical cut, both V3 selections replay unchanged in either order, and replay
does not advance the tenant head.

**Hypothesis:** the test omits `recovery_cut_source` on reopen and consequently
falls back to `HoldRecoveryCutSource`, which rejects selected readiness.

**Expected observation if the hypothesis is true:** the breakpoint on
`return HoldRecoveryCutSource()` is reached during reopen; `port` is absent and
the stack leads from readiness validation into the reopened runtime.

**Falsifier:** reopen supplies a native source and never reaches this fallback,
or the fallback is reached for a different runtime/path.

**Observed:** reopen remains unreached; Probe 2 found an earlier functional refusal.

The first relaxed run did not reach reopen: it selected the second call and
closed dispatch, then rejected direct-terminal publication because the complete
history contained two execution fanouts (`1 failed in 894.07s`). This is a
functional rejection, independent of the relaxed authority deadlines.

## Probe 2 — direct-terminal selection in a two-seal history

Recorded before reproducing the cut selection on the newly created fixture.

**Ideal behavior:** a target-qualified terminal read/publication selects exactly
one authenticated fanout containing its `original_call_id`. The other selected
seal remains authenticated history, not a reason to reject the target. The
selected original Prepare and response seal must belong to that target; missing
or ambiguous target membership must fail closed.

**Hypothesis:** `_current_cut` incorrectly requires one fanout globally and then
uses `executions[0]`, instead of selecting one by original-call membership.

**Expected observation:** two distinct authenticated fanouts, exactly one
containing the second original call, followed by the global cardinality refusal.

**Falsifier:** no unique target fanout exists, or the two fanouts cannot both
authenticate from the current fixture.

**Observed:** the fresh fixture reopened successfully with its native source.
At `_current_cut` there were two authenticated fanouts and exactly one contained
`call:995b7e2de7b1726bfba8c1e1a82787ac07466014022faa131a22807e21e8b48d`.
The global cardinality predicate rejects before examining that unique target.

The first target-qualified fix reached the correct second seal but still failed:
the original Prepare projection was unavailable. This was not a successful cut.

## Probe 3 — original Prepare for a second Turn

The ideal behavior and hypothesis were recorded in commentary before this stop.

**Ideal behavior:** select the unique Prepare whose manifest names the selected
seal's Turn. Earlier Turns' Prepares must still pass every existing integrity
check, but do not count as rival Prepares for the target Turn.

**Hypothesis:** `_select_prepare_from_runs` counts all Turns' Prepares as rivals.

**Expected:** two authenticated Prepare matches, with distinct `/turn/1` and
`/turn/2` IDs, while the target seal names `/turn/2`.

**Observed:** exactly that pair reached the final `len(matches) != 1` predicate.
The helper now takes a required target Turn ID and filters only after each
Prepare's complete validation. H1's separate single-Turn candidate gate is kept.

## Next verification expectation

After restarting with both target selectors corrected, the same fixture should
yield a nonempty closed cut for the second call and second response seal. A
missing original projection or closed source would falsify completion of this
repair and require another localized probe.

**Observed after restart:** the Prepare selector returned one `/turn/2` match
while the physical Run still contained both Turns. `_current_cut` then returned
the second call's second-seal cut with outcome `CONFIRMED` and obligation `CLOSED`.
The exact same-fixture cut test passed under MCP: `1 passed in 473.73s`.

## Probe 4 — complete two-seal functional witness

Recorded before launching the corrected original selector.

**Ideal behavior:** second-call acceptance, closed dispatch, direct terminal and
second-seal V3 publication all succeed. Each seal keeps its exact members; a
first-seal projection cannot supply the second call. Both V3 records replay in
both orders with no tenant-head advance, including after reopen with the native
recovery source supplied explicitly.

**Expected:** the original two-seal pytest selector passes with the diagnostic
clock configuration. Any rejection or failed assertion names the next concrete
divergence; success does not certify ordinary production deadline performance.

**Observed:** the full selector failed after both V3 publications returned, at
`second_v3.request.selected_response`: the public return value is
`NativeSealedAccountingV3Record`, whose seal binding is
`selected_response_fingerprint`, not a retained owner request. Command:
`PYTHONPATH=/private/tmp/chiplog-r14-debug-clock:/Users/thunderbird/Projects/chiplog/src CHIPLOG_DEBUG_CLOCK_ORIGIN_NS=0 ./.venv/bin/python -m pytest -xq tests/composition/test_r14_native_selected_accounting_source.py::test_v3_accounts_two_sequential_turn_seals_after_reopen_and_cross_order_replay --basetemp=/private/tmp/chiplog-r14-two-seal-fixed`.
Result: `1 failed in 1321.62s`. The two assertions now check the actual record's
exact selected seal fingerprint. Replay/reopen assertions were not reached and
remain unverified by this run. A fresh corrected-selector run is required.

**Corrected selector result:** the fresh run using
`--basetemp=/private/tmp/chiplog-r14-two-seal-record-fixed` and the same diagnostic
clock environment passed: `1 passed in 1899.00s (0:31:38)`. Both V3 bindings,
cross-order replay and final reopen/no-head-advance assertions were reached.
Log: `/private/tmp/chiplog-r14-two-seal-record-fixed.log`. This run was launched
before the subsequent same-Run suffix-intent/consumption repairs; its first seal
is cancelled. It does not certify accepted cross-seal interrupted recovery or
production deadlines.

## Checkpoint

`98027ea` commits the separate verified performance slice. The checkpoint gate
ran ten selected tests successfully and checked R8 structure/import boundaries.
It does not commit or certify the still-in-progress two-seal correctness repair.

`cae04bb` subsequently commits the call/seal-scoped terminal and Turn-scoped
Prepare selection with their focused negative tests. The isolated staged
checkpoint ran `10 passed in 0.98s`; the commit gate repeated them with
`10 passed in 0.87s` and verified R8 structure/import. This remains a checkpoint,
not completion evidence for the full two-seal reopen/replay scenario.

## Focused verification limits

The quick selector/ambiguity tests passed (`16 passed, 1 deselected`) with
`PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/composition/test_h1_selected_prepare.py -k 'not postseal_selector' tests/composition/test_r14_accepted_direct_terminal_history_reuse.py`.

The full pair of files reported `1 failed, 16 passed in 14.08s`: the older H1
postseal mounted test returned `EXECUTION_DRIVER_REJECTED_V1` during
`advance_execution`, before its selector assertions. The unchanged test also
failed with that same assertion on checkpoint `98027ea` in the isolated
worktree (`1 failed in 14.00s`). This establishes a baseline failure, not a
passing H1 mounted regression. The separate H1 single-Turn candidate gate and
its fast negative test remain unchanged.
