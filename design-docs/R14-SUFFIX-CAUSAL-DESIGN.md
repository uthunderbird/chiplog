# R14.6 causal native-terminal suffix design

Status: proposed local implementation design; runtime and tests were not changed by this investigation. Code evidence was read on 2026-10-15. No mounted result is claimed. Same-model analytical perspectives and a cold plan critique are not independent verification.

## Problem, scope, and assumptions

The native terminal suffix must admit the already registered accepted/cancelled R14.6 lifecycle while proving both exact physical history and each publication's causal source. Reconstructing an OPEN command authenticates its retained owner exchange against that exchange's own request; it does not establish that the request's cut was ever the selected accepted/effects source.

Scope is native direct terminals, accepted OPEN accounting/recovery, existing cancelled accounting, per-seal V3 and their four registered R16 operations. Resolver lifecycle remains under its existing bounded verifier. No new durable journal, generalized publication plugin framework, concurrency, or unrelated milestone changes.

Success requires a nonrecursive local implementation seam, exact full-current physical coverage, a distinct source prefix for every semantic join, successful mounted valid interleavings, and mounted rejection of coherent but causally false retained cuts. Merely passing the three missing-family positive tests is insufficient.

Assumptions checked against code: the R16 reader returns a complete current effects materialization and independent journal; native source facts already carry selected Run lineage, seals, inventory, acceptances and cancellations. Remaining engineering assumption: those facts can be captured with per-publication sequence provenance during the existing outer reader, without a second history read. That is a proposed seam, not an implemented property.

## Evidence

- `r14_historical_suffix.py`: the physical native verifier currently rebuilds direct/V3 commands only, checks contiguous sequences, exact publication metadata and physical batches, and removes batches from an in-memory snapshot to reproduce predecessor commitments. The semantic function separately validates R16 issuance and allowed intent membership. It has no OPEN cut source join.
- `r14_accepted_open_accounting_records.py`: both OPEN command builders replay the pure owner preparation, validate actor budget and request/reply owner exchange, and derive exact records and command identity. All of that can succeed for a internally consistent false source cut.
- `r14_loop_history.py`: OPEN branches reconstruct those commands and append retained recovery facts. They do not authenticate the OPEN cut against actual selected acceptance and effects history. Deferred V3 validation has stronger source joins. Existing cancelled accounting calls `decode_selected_native_accounting`, which reconstructs its selected source against prior fanout/cancellation/Run records and exact source manifest; it is not equivalent to decoding only retained bytes.
- `r14_native_selected_accounting_source.py`: `_accepted_open_cut_mismatches` already enumerates acceptance, initialized/seal, Run bytes and selected outcome locator checks; `prepare_sealed_accounting_v3` invokes it. Its Run check currently uses the supplied source's final lineage member, so reusing it for an earlier OPEN requires a source-specific Run prefix.
- `r16_effects.py::_read_materialized_effects_with_history`: checks exact owner publication manifests, per-record bytes, complete effects ID equality, journal stability and database identity; its later-suffix callback runs only when actual differs from independent anchored commitment. The callback therefore cannot be the only place that validates retained OPEN semantics.
- `r14_native_selected_accounting_source.py`: already computes `latest_at_cut` using row sequence and checks CLOSED locators against historical membership. Final ordered-source collection still receives full verified history, so any new OPEN helper must explicitly supply the historical prefix to ordered-source construction.

## Chosen seam

Keep the physical suffix function free of source collectors. Extend its finite loop-family branches with the existing OPEN builders and cancelled accounting's owner-specific envelope validation. Preserve all current physical checks. The physical result is necessary input for a later semantic check; it must never authorize a publication on its own.

Add one internal held-gate semantic entry point, conceptually:

```python
validate_native_terminal_suffix_sources_held(
    runtime,
    *,
    source_facts,             # same-Run facts just authenticated by caller
    selected_loop_entries,    # exact selected bytes + sequence + predecessor
    run_source_prefixes,      # captured prefix facts, keyed by publication sequence
    effects_cut,             # COMPLETE CURRENT cut, never the filtered prefix
    effects_journal,         # exact independent journal paired with cut
    verified_effects,        # verify_dispatch_history(effects_cut, journal)
    target_original_call_id,
) -> None
```

This is an internal function, not a new exported trusted DTO. Inputs must originate from the current held acquisition, not caller-provided public facts. Require tenant/database identity, current commitment/frontier, selected journal/loop bytes and no-pending checks before and after the fold. If a public reader receives facts rather than creating them, it must authenticate their source first. A frozen dataclass alone is not provenance.

The smallest prefix capture is the existing `ExecutionRecoveryHistory` shape that `_read_call_history` already constructs before deferred V3, augmented locally with the authenticated selected preparation context it needs. Capture it for marked OPEN/accounting/direct sources before consuming that publication, keyed by its `command.expected_head + 1`. A shorter implementation can capture only source-bound Run/acceptance/cancellation facts and sequence metadata rather than duplicate a whole history tuple; it must preserve exact ordering and completeness. Do not recover the bound by searching DTO values with no publication provenance.

Outer reader flow: authenticate all loop envelopes and prefix facts once, acquire full R16 cut/journal once using supplied authenticated execution history, verify complete dispatch history once, then run the causal fold. Deferred V3 and direct-terminal projection consume that same result. Do not invoke `collect()`, `read_execution_recovery_history()`, `build_accepted_open_recovery_cut()`, terminal/V3 source validators, or `_read_call_history()` from the fold. Reuse pure projection/helpers by passing captured facts and verified effects explicitly.

The direct historical reader must invoke the semantic entry point after full R16 acquisition even when `actual == anchored`, and even when resolver-prefix authentication accepted a commitment mismatch. Physical callback outcome selects a physical proof route, not whether causal checks are necessary. Validate all marked admitted publications before filtering to the requested terminal/member.

## Prefix and dependency contract

For a selected source publication at tenant sequence `s`, its source bound is `h = command.expected_head = s - 1`. Require its retained source sequence to equal `h`, and retained materialization commitment to equal the selected entry's predecessor. The publication itself and every later publication are excluded from its source prefix.

Effects prefix includes only fully authenticated effect batches with sequence `<= h`; keep publication ordinal and complete batch record IDs unchanged. Select the last member of the exact intent at that prefix, not the last current member. Preserve the full current cut as the physical proof and filter only semantic inputs. The retained selected outcome can be earlier than `h`, but must be the source selected according to that operation's OPEN policy; no later outcome can supply a missing cause. An outcome at `h` is allowed; an outcome at `s` is self-dependency and rejects.

Run, seal, initialized, acceptance and cancellation prefix must likewise contain only prerequisites committed `<= h`. Later acceptances or a later Run head cannot validate an earlier OPEN. The fold runs in ascending tenant sequence; a semantic reference to an unvalidated later terminal/accounting rejects. V3 consumes only already validated per-call branches from its own prefix. No single global A/B member ordering is imposed.

Complete current selected-loop physical membership and complete effects membership are prerequisites. Individually authentic facts with a missing tail cannot establish the prefix. A mismatch, missing prefix, ambiguous member, duplicate terminal branch, forward dependency, unknown family or invalid provenance raises a concrete integrity error/HOLD; no partial suffix is returned.

## Family checks

| Family | Envelope and source requirements |
| --- | --- |
| Direct terminal | Existing direct owner command validation and complete three-record batch; unique `(response_id, original_call_id)`; exact selected CLOSED locator, acceptance/effect/seal and historical closure join using its own bound. |
| OPEN accounting | Existing `accepted_open_accounting_command`; exact retained-cut fingerprint, command/source sequence and predecessor; selected same-Run seal/initialized/acceptance manifest and exact effect intent; selected OPEN outcome, member decision, bytes/fingerprint/sequence/ordinal/batch at source prefix; exact prefix Run bytes/head. Accounting alone does not create a recovery frontier. |
| OPEN recovery | Existing `accepted_open_recovery_command`, all OPEN source checks above; exact opening/terminal/accounting relationships from `_accepted_open_call_frontier`; unique terminal branch for that call. This atomic publication creates its own opening: do not invent a requirement that an earlier standalone OPEN accounting publication must exist. |
| Cancelled accounting | Existing retained owner-specific cancelled envelope and source manifest validation; actual prior native seal and initialized record, exact selected cancellation terminal/result, absence of acceptance for that call at its source prefix, exact preparation binding/Run lineage and source stamp. Keep its legacy singleton contract; do not relax `decode_selected_native_accounting` to fit multi-member V3. |
| V3 | Existing owner envelope/retained result validation; at its prefix, exact seal manifest and each ordered member's validated terminal frontier. All members must qualify; a later terminal cannot repair an earlier V3. |
| authorize/commit_send | Existing issuance validation plus exact authenticated acceptance effect intent and same-Run seal membership, committed before issuance; no allowlist of effect IDs without causal acceptance. |
| record_evidence/reconcile | Existing outcome issuance validation plus exact prior selected send and its acceptance/effect/seal lineage; repeated admissible evidence while OPEN remains valid. |

Prefer factoring the existing field checks from `_accepted_open_cut_mismatches` and OPEN frontier construction rather than duplicating their model. The suspension exception remains an explicitly authenticated transition, never a generic waiver for different Run bytes. A new per-publication prefix should normally select the exact earlier Run and eliminate need to compare against a later current Run.

## Mounted discriminators

1. Direct A followed by standalone OPEN accounting B, atomic OPEN recovery B, and legacy singleton cancelled accounting C, each at the physical-ahead/anchor interruption boundary: reopen/read/replay A and all relevant V3; no tenant head advance. Test each registered family separately.
2. Three accepted members with B dispatch delayed until A terminal, repeated evidence for B, and an accepted member on a second seal: retain distinct terminal source prefixes, reopen/replay each terminal and per-seal V3. Do not infer this result from a cancelled first seal.
3. For both OPEN operations, substitute a different same-Run member's selected outcome/decision or acceptance/effect/seal binding and regenerate the cut fingerprint, owner reply, command and selected publication consistently. Mount the exact coherent physical bytes. Direct historical A read must HOLD with actual equal to anchor as well as at the interrupted boundary. A broken fingerprint test alone does not distinguish semantic authentication from envelope reconstruction.
4. Source cut references a real outcome committed after its expected-head bound, or a real acceptance/seal/Run successor only available later: coherently regenerate all retained/physical bytes. HOLD. Conversely, later legitimate CLOSED evidence must not invalidate an earlier OPEN source prefix.
5. Change retained OPEN materialization commitment or source sequence while preserving canonical cut and exchange: reject selected predecessor/sequence mismatch. Keep tests distinct from source-locator mismatch.
6. OPEN recovery absent an earlier standalone accounting: succeeds when its selected acceptance and OPEN effects cause exist. Recovery with no admissible OPEN cause or with another call's opening: HOLD.
7. V3 placed before one terminal and then followed by that terminal: HOLD. Valid cancelled accounting must still prove singleton selected source, live cancellation prerequisites and exact manifest.
8. Omitted sequence, changed predecessor/resulting commitment, extra/orphan physical record, changed batch order, unknown loop/owner family and cross-seal accepted evidence substitution: HOLD with no effect send or head movement.
9. Instrument/replace the ordinary history reader so reentry fails after outer acquisition; all mounted positives above still succeed. This proves nonrecursion at the seam, not just helper unit tests.

## Swarm execution record and limits

Phase 1 framed causal authentication rather than family allowlisting. Preset: Diagnosis/Engineering; high grounding/rigor, narrow branching budget (at most three), design-only closure. Phase 2 used three internal applied perspectives: physical-integrity critic, prefix/dependency critic, and reuse/composition evangelist. These are reasoning lenses, not independent agents or claimed named-expert testimony. Moderator owns route selection; Executor owns code reads. A separate no-parent-context agent critiqued only the plan; its findings required explicit boundary, dependency order and completeness contracts above.

Iteration E1: Moderator gap was whether canonical OPEN commands authenticate external sources. Executor read suffix, OPEN records/publication and loop-history branches. Route "allowlist-only" moved candidate -> rejected: pure preparation authenticates the supplied cut against itself, while OPEN reader only reconstructs envelope. Open item: identify reusable source join.

Iteration A: integrity critic required exact source stamps as well as row matching; prefix critic required `s-1` bounds and rejected final Run lineage as historical source; reuse evangelist proposed captured reader facts plus verified current R16 history. Moderator compressed overlap into one bounded-fold route (candidate -> needs evidence). No expert-to-expert unresolved question was generated; open item became whether full-current effects completeness and existing checks support this seam.

Iteration E2: Executor read `_accepted_open_cut_mismatches`, V3 projection, cancelled decoder and R16 full materialization. Bounded-fold route moved needs evidence -> supported proposal: full effects ID equality and paired journal already exist; V3 checks identify reusable joins; cancelled decoder demonstrates why owner-specific source checks must remain. Evidence debt: prefix capture is proposed, not implemented; mounted discriminators remain unexecuted by this task.

Iteration H: pre-mortem perspectives identified possible coherent OPEN substitutions, contamination by later Run/evidence, and hidden recursion from a reused collector. These are covered by explicit guards and mounted discriminators, not declared experimentally eliminated. Readiness: enough source evidence for a concrete implementation seam; no competing route remains useful. Convergent design close, with unverified implementation/testing explicitly bounded. Root must inspect the seam and validate mounted outcomes before calling the repair complete.

This artifact deliberately makes no performance guarantee. Existing singleton OPEN writers/readers and cancellation decoder may impose additional mounted constraints; this investigation does not authorize widening them. Full effect history verification alone is not a proof that every live issuance-source validator is recursion-free: audit the proposed helper's callees before reusing them in the fold.
