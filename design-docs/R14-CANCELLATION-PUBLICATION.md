# R14 authenticated pre-accept cancellation

Status: implemented for the registered hermetic CLI profile. Atomic publication,
restart recovery and canonical-loop continuation have runtime witnesses. This does
not close R14.1 or replace the acceptance and recovery requirements in
R14-CONTRACT-FREEZE.md.

## Scope and sources

The existing canonical fanout initializes original call subjects. The owner-local
`prepare_pre_accept_cancellation` can prepare an exact cancellation terminal and
typed NOT_EXECUTED result, but cannot authenticate the request or publish it.
`r14_loop_history` admits selected cancellation alongside legacy Run and captured
fanout envelopes. `inventory_from_history` joins the cancellation terminal with
its Run companion without deriving a duplicate proposal terminal.

Sources: `call_acceptance_contracts.py`, `call_acceptance_preparation.py`,
`r14_fanout.py`, `r14_fanout_records.py`, `r14_loop_history.py`,
`r7_planning.py::_observed_trust_call`, and NORMATIVE's exact acceptance versus
cancellation and exhaustive call accounting requirements.

## Plan and assumptions

1. Freeze the public submission and complete retained evidence; validate the public
   wire without claiming authority or runtime behavior.
2. Implement the closed retained-envelope interpreter and selected-history fold,
   including semantic validation before pending physical recovery.
3. Acquire actual registered hermetic authentication; invoke the isolated owner
   outside the authority gate; recheck all sources and exact heads at the sole
   writer; select and materialize the complete batch.
4. Integrate the canonical loop's post-fanout cancellation race and add actual
   positive, rival, stale, omission and crash/restart witnesses.

Assumptions: the existing hermetic CLI assembly is the only applicable profile;
this boundary makes no general Unix-peer authentication claim. Current caller
authentication is required even for replay. No Run-wide cancellation is introduced.
Consequential acceptance remains required follow-on work with the separately
versioned R16 mandate; existing no-send semantics are not promoted.

## Public submission and policy

`CancelCallSubmission` contains act ID, original call ID, exact initialized
reference and exact current Run reference. It supplies no observed cut, permission,
timestamp, authenticated-act reference or prepared result. The current registered
principal may cancel its own ACTIVE non-scheduler original Run's still-initialized
call under `HermeticCancellationPolicy`. A terminal or accepted call cannot win.

Composition obtains its own actual deployment-trust exchange and validates the
subject against the Run. It retains the original verified trust snapshot, source
identities, request/response and authenticated reference. Those bytes establish
historical provenance only when authenticated by independent journal selection;
constructing equal DTOs or matching hashes grants nothing.

The retained act binds the complete submission, closed policy, authenticated
reference and trust-evidence fingerprint. Its subject is `cancellation-act:` plus
SHA-256 of its canonical bytes; its head is `record:` plus that digest. The act
reference addresses retained journal content, not an invented prior trust record.
The request's `cancellation_act` must equal that exact reference.

Publication identity is `cancel-call:` plus SHA-256 of a canonical RecoveryDTO
identity object containing `kind="CALL_CANCELLATION_IDENTITY_V1"`, tenant and act
ID. Original submitted bytes are checked on replay; a reused act ID with changed
heads or subject conflicts. Successful replay returns the original committed Run
companion, even if the current Run has advanced.

## Complete publication and readers

The physical envelope has exactly three ordered members: owner-authored Run
ToolTerminal companion, CancelledBeforeAccept terminal, and NOT_EXECUTED result.
The companion references the same original call and carries the canonical typed
result bytes; it does not terminate the Run. The envelope binds the full retained
preparation fingerprint, exact tenant predecessor and deletion fence. The total
serialized envelope, including payload encoding and fingerprint, must fit the
1 MiB limit. Any missing, additional or changed member rejects the whole batch.

The new decoder explicitly permits this operation's act-based publication identity
to differ from the companion Run head. Legacy and fanout identity rules stay exact.
It validates the retained act, original source exchange, owner request/result,
initialized predecessor, complete prior inventory and Run edge against earlier
selected history at one authenticated cut. The inventory preserves the selected
terminal/result references and does not create a second legacy terminal from the
Run companion. Pending selections undergo the same semantic checks before SQL
materialization. Recovery never reconstructs the act from current trust or calls
the current owner to regenerate historical bytes.

The sole writer rechecks the current Run, original initialized subject, principal,
worker session, authority sources, physical database identity and independent
anchor. The owner exchange occurs outside the gate; no provider I/O is involved.
Ordinary proposal terminalization and cancellation compete on the same exact cut.
After cancellation wins, the resumed loop observes that terminal and does not
terminalize it again. An ordinary terminal winner makes cancellation conflict.

## Guarantee-to-evidence map

Evidence is bounded to this hermetic pre-accept cancellation slice. Consumer
shape validation alone establishes no runtime authority. The runtime suite is
`tests/composition/test_cancellation_publication.py`; consumer shapes are in
`tests/composition/test_cancellation_publication_contracts.py`.

| Claim | Owned data | Independent observable | Forbidden substitute | Boundary fixture | Evidence |
|---|---|---|---|---|---|
| Authorized act | actual trust exchange, principal, request and closed policy | writer source mutation prevents selection | caller reference or authenticated boolean | wrong principal, changed trust/session, stale Run | unregistered peer, substituted owner reply and stale-worker runtime tests |
| One branch | original initialized subject and exact Run/inventory CAS | selected journal and physical records show one winner | prepared result or embedded outcome alone | both ordinary terminal/cancel orders; later accept/cancel orders | ordinary-terminal winner and cancellation-inside-real-step witnesses; consequential acceptance race remains open |
| Complete cancellation | terminal, NOT_EXECUTED and Run companion | exact three-member physical set/order/bytes | count/digest without membership | omit/add/duplicate/reorder/substitute; size N/N+1 | strict envelope reconstruction, changed-act rehash rejection and serialized N/N+1 witness |
| Stable replay | original submitted act and selected owner bytes | restart preserves identities and bytes | refreshed permission or regenerated proposal | before/after decision, SQL and reply; changed request | immediate/later/restart replay and before/after SQL commit fault witnesses |
| Honest continuation | authoritative selected lifecycle | future Turn/fanout sees actual selected terminal | duplicate legacy terminal or stale embedded pending state | cancellation during real loop pause, restart | real-step cancellation followed by a successful next Turn |
| Recovery before exposure | closed retained decoder | malformed pending decision causes zero SQL writes | recover first, validate later | missing/substituted act or result before restart | malformed pending act rejected while recovery submission is forbidden |

Omission, addition/unknown, substitution/alias, duplicate/reorder, stale/race,
N/N+1 and physical/logical identity mismatch all apply. No family is waived.
The remaining full R14 map includes consequential/read-only acceptance, typed
unknown/recovery results, evidence reduction, resume/successor, worker fencing,
post-terminal streams and no-exposure model recovery.
