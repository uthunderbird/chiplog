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

## Probe 5 — pending OPEN accounting source at startup

Recorded before breakpoint observations. The real mounted after-COMMIT fault
passed the initial exact outer read, then failed during reopen at
`_authenticate_selected_open_source`; the preceding two failures were historical
scope/ingress prefix gates and have their own narrow repair in the isolated tree.

Ideal behavior: startup reconstructs the exact selected call's historical OPEN
cut while source facts carry the current full physical commitment. Independent
anchor may still equal the predecessor; source/history proof must justify the
pending suffix before replay. No second external emission is allowed.

Session `1df5f33e-3a3a-4661-afd6-52f242318b51` launches the fresh mounted
`test_selected_open_pending_batch_recovers_after_reopen[after_commit-accounting]`
in `/private/tmp/chiplog-r14-debug-checkpoint`, with diagnostic clock and owners
excluded from subprocess debugging. No target execution deadline is introduced.

- Breakpoint 1: `_current_cut`, `if facts is None`, condition
  `expected_source_cut is not None`. Expected reached during startup, facts
  present and carrying current physical commitment; next builds the cut.
  Expected stack: startup → OPEN validator → source helper → `_current_cut`.
  Missing facts falsifies the expectation and localizes the earlier acquisition.
- Breakpoint 2: `_current_cut`, `if cut is None`, same condition. Expected
  reached, cut present with retained historical source stamp and requested call;
  next returns `(facts, cut)`. Same expected stack. Missing cut localizes its
  builder rather than facts acquisition.
- Breakpoint 3: helper's `accepted/open selected source does not authenticate
  current cut` raise. Expected unreached in ideal behavior. If reached, inspect
  current presence, captured/final physical commitment and captured/current
  independent anchor before raising. Expected stack: startup → validator → helper.
  A present current with unchanged matching commitments falsifies the simple
  source-unavailable hypothesis.

Observations follow separately after these expectations.

Observed at breakpoint 1: exact expected stack reached during startup accounting
validation, `facts is None`, source sequence 18 and current snapshot head 19;
sole pending operation is accepted-open-accounting. Breakpoint 2 was not reached.
Breakpoint 3 then confirmed `current is None`, unchanged physical commitment and
unchanged independent anchor, with physical commitment ahead of anchor. This
localizes failure before cut construction rather than a moving-cut race.

Before restart, the next three conditional breakpoints were recorded in commentary:
after acceptance acquisition (expected present, then effects read; stack includes
outcome collector → acceptance collector), base collector exception return
(expected unreached, otherwise inspect current exception and acceptance stack),
and outcome collector exception return (expected unreached, otherwise inspect
effects/suffix failure and outcome stack). Conditions restrict them to selected
historical reads. Sources remain unchanged during the debugger run.

Observed on restart: acceptance breakpoint reached with nonempty accepted facts
for the exact call and physical commitment ahead of anchor. Base collector's
exception breakpoint was not reached. Outcome exception breakpoint then reached
with `EffectsIntegrityError` caused by `ValueError("physical authority differs
from independent commitment")`, from the effects reader. Callback was set,
`native_suffix_authenticated=False`, the only pending operation was OPEN
accounting, and the fixture contained zero direct terminals. This disproved the
acceptance-history hypothesis and isolated missing OPEN prefix support in the
effects suffix callback. Session was closed after observations. The isolated
repair adds only that existing prefix proof; no accepted-facts/history reuse was
added. The pending-OPEN boundary test failed false before the callback repair.

## Probe 6 — fixed pending OPEN startup source

Expectations were recorded before launching session
`0e6c75d6-ef25-49d2-a893-7a6d9d28010a`. The script reopens the exact unchanged
after-anchor red fixture at its original path; no files or identity bindings are
repinned. Runtime startup must authenticate it before replay. It uses the frozen
`dd0fabc` candidate, the same diagnostic clock and no new target deadline.

- Historical `_current_cut` facts breakpoint: expected reached with nonempty
  current physical facts, then cut construction. Expected stack: startup →
  accounting validator → helper → `_current_cut`. Condition is a retained
  historical source cut. Missing facts falsifies the repair for this fixture.
- Cut breakpoint: same condition and stack, expected nonempty cut retaining
  original source sequence, then pair return. Missing cut localizes a remaining
  builder refusal.
- Helper rejection breakpoint: expected unreached. If reached, inspect source
  presence and before/after physical/anchor values; stack is startup → validator
  → helper. The final script asserts no pending and one original transfer.

Observations follow after the run.

Observed at facts breakpoint: expected startup stack, facts present, exact call,
source sequence 18/current sequence 19, and the already authenticated physical
snapshot was injected. At cut breakpoint: cut present and exactly equal to the
retained historical cut; facts still report current sequence 19 and cut sequence
18. Both successful breakpoints were then removed; helper rejection stayed armed
while startup/replay and the final script assertions continued. Completion is
recorded separately after observing exit.

The post-proof script did not complete: its unguarded `asyncio.run()` recursed
through multiprocessing bootstrap and owner startup failed. After adding the
main guard, source facts/cut were again valid (now pending already repaired and
special snapshot unnecessary), but owner attestation rejected two extra loaded
policy modules: deployment_trust `_j7_grant_process` and `_r7_process`. All
identity, capability and authority-empty flags matched; only module closure
differed. Top-level project imports in the standalone main module were inherited
by spawned owners. They were moved inside guarded `main`; no attestation allowlist
or authority check was changed. These are diagnostic-helper failures, not passing
full debugger-script outcomes. Both debugger sessions were closed. A corrected
ordinary script run exited successfully and reported one original transfer.
This reopened the already repaired fixture; the six fresh pytest witnesses
remain the evidence for recovery from pending state. Source proof at both planned breakpoints remains
observed; the six complete fresh pytest fault witnesses are separate evidence.

For the guarded debugger attempt only, parent multiprocessing Event waits were
temporarily raised to at least one hour through evaluation, in addition to the
diagnostic monotonic_ns scaling. This patch lived only in that closed process;
production sources and budgets were untouched.

### SUSPENSION prefix probe

Before setting breakpoints in session `427b1bce-9d47-4012-afa7-678c35f052ac`:
reopen the exact failed SUSPENSION fixture, with guarded project imports and the
diagnostic clock; no new target deadline.

- Break at `cut = evidence.request.cut` in the prefix helper, conditioned on
  `operation == 'agent_loop.execution-suspension.v1'`. Expected reached with a
  canonical suspension command and retained request, before shared header proof.
  Expected stack: startup history validation → native scope historical read →
  recovery prefix dispatcher → OPEN prefix helper. Next: header proof passes,
  COMPLETE final batch proof returns true. Missing breakpoint or wrong cut fields
  falsifies this acquisition hypothesis.
- Break at the dispatcher's exception return (condition: sole pending suspension).
  Expected unreached in a healthy fixture. If reached, inspect the active exception
  and helper inputs before continuing; expected stack is the same prefix dispatcher
  under startup history validation. Any caught canonical/header exception explains
  the persistent scope refusal and falsifies successful prefix authentication.

Observed: the first breakpoint was reached with exactly the expected startup
stack. Canonical request cut is `ExecutionRecoveryCut`, source sequence 19,
predecessor matches the source commitment, but retained physical command equality
is false. A restart with the same breakpoints isolated the sole differing field:
`expected_preimage_commitment` is None on `_publication(entry)` and the source
commitment on `_command(evidence)`. The exception-return breakpoint was not hit;
the header check returned false normally and startup raised the same scope error.
The debugger session was closed.

The SUSP branch now normalizes that physical-only command field exactly as the
existing suspension selected validator does; predecessor/cut and physical prefix
checks remain enforced separately. The SQLite unit matrix now creates the same
asymmetric representations, and passed 82 tests. Cold delta review found no P0/P1.
Fresh three-boundary mounted rerun and exact failed-fixture reopen are pending.

The first complete reopen exposed two further boundaries: selected-only startup
did not defer pending SUSP to the existing outer lifecycle fold, and preservation
of a suspended OPEN cut invoked the ordinary no-pending suspension reader. The
ABSENT case additionally required passing the separately validated physical Run
projection to the historical effects source. All three repairs retained the
complete semantic snapshot and exact outer fold. The original ABSENT fixture
then reopened successfully with one original transfer (exit 0).

Before breakpoints for fixed-source session
`f0a675d1-3845-4432-8418-5703124651e1`, reopening the original COMPLETE fixture:

- At prefix `return valid`, conditioned on SUSP operation, expected reached with
  `valid is True`, physical head 20/source 19 and independent anchor at source.
  Stack: startup → historical scope → recovery prefix → prefix proof. Next the
  native scope read should continue. False proof or a differing command beyond
  its normalized preimage falsifies the fix.
- At suspension-preservation `return bool`, conditioned on a pending SUSP,
  expected reached with a validated selected suspension, exact predecessor and
  suspended Run refs, and retained original obligation in the baseline. Stack:
  startup → OPEN recovery validator → cut preservation. Next return True, replay
  pending batch, and finish with no pending/one transfer. Missing selected DTO,
  mismatched refs or missing obligation falsifies the preservation repair.

Observed at the first breakpoint: `valid=True`, physical head 20/source head 19,
actual=resulting, anchor=predecessor, normalized canonical command equals the
journal command; stack matched the prediction. The successful breakpoint was
removed. At the second breakpoint: `SelectedExecutionSuspension`, exact
predecessor and suspended Run refs, original obligation present, one pending;
all bool operands were true and stack matched startup's OPEN recovery validator.
After removing that breakpoint and continuing, the script printed one original
transfer and exited 0. Session was closed. This is the original COMPLETE pending
fixture; the earlier ordinary exit-0 reopen covered original ABSENT pending.
The fresh three-boundary mounted run has passed its first (before-COMMIT) case;
the remaining two are still running on the unchanged isolated candidate.
