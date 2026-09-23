# R16 v2 mandate boundary

Status: public inert wire candidate; no runtime SEND authority, reducer route or
deployment entitlement is added. V1 files and interpretation are unchanged.
Consumer module: `chiplog.capabilities.effects.dispatch_v2_contracts`.

## Ownership and chronology

The newly registered consequential tool first produces a sealed initialized call.
That request grants no permission. Application preview and explicit adoption bind
the original call, exact initialized head, tool/schema/policy, payload, recipient,
scope and horizon. Acceptance atomically publishes the existing complete acceptance
manifest, embedding the mandate and original acquisition in its sole external
intent. No extra physical adoption companion or pre-initialization grant is implied.
This follows `project-architecture/NORMATIVE.md:439`.

`InitializedCallOrigin` retains stable original Run/call IDs, the historical
initialization Run head, exact initialized call head and sealed argument bytes.
The initialized head's subject must equal `original_call_id`; it is a revision of
that subject, not a second call identity. `initialization_run_head` is historical
evidence, not the current ACTIVE Run used at acceptance/SEND. Current Run/fence
checks are independent and may not rewrite this origin or renew the mandate.
`PlanEffectOrigin` is a distinct branch for atomic planning revision publication;
it cannot masquerade as call acceptance or create a second intent from the same
adoption. Neither origin borrows the old publication-only registry's SEND rights.

Effects owns `DispatchMandateV2` and `ExternalActionIntentV2`. Mandate scope includes
every original authority/constraint/dependency/evidence/applicability reference,
normative conflict generation, exact recipient and payload, member order, semantic
binding and immutable horizon. `DispatchAcquisitionV2` separately retains the exact
display/adoption/ingress, authenticated invocation, typed precursor evaluation
request/result and original source inventory. Both precursor schemas forbid an
embedded resulting intent/publication. `preexisting_authority_basis` names an
authority source that already existed before this mandate; it never names this
mandate's adoption. No mandate field references its later adoption/acquisition.

The exact construction graph is: preexisting source heads and initialized origin
→ mandate bytes → precursor request (mandate SHA256, source heads, policy)
→ precursor result (request SHA256, same mandate SHA256, policy/evaluation refs)
→ display and explicit adoption → authenticated acquisition with original source
inventory → intent body → intent fingerprint. The source inventory is captured
after adoption, so its original-adoption source can reference that adoption without
creating a cycle. Precursor evaluation is not acquisition or SEND authorization.

Mandate and precursor digests are SHA256 of their canonical bytes. Display head
fingerprint is SHA256 of the retained exact display bytes. The intent fingerprint
is SHA256 of `b"chiplog.effects.intent.v2\\x00"` (a terminal NUL byte) followed by
canonical JSON of the complete intent excluding only its `fingerprint` field;
canonical JSON uses sorted keys, UTF-8, no ASCII escaping and compact separators.
Full intent bytes include the resulting fingerprint. Adoption retains the exact
mandate bytes separately; owner admission must verify equality, not trust duplicate
fields. The public consumer constructs this full graph without self placeholders.

Composition must authenticate acquisition and interpretation from independent
selected/runtime sources, not hashes or caller-provided values. The writer retains
owner bytes unchanged. It must verify the adoption's mandate bytes equal the exact
mandate serialization, display content binds those bytes and origin, payload SHA
matches, references are exact, collections are complete/ordered/unique as required,
and fingerprint derivation follows the domain above. These are upcoming owner/private-issuer requirements, not shape-test
guarantees. Empty constraint/assertion lists require registered narrow-policy proof.

## Fresh observation and operation boundary

`AuthorizeDispatchV2` and `CommitFirstSendV2` bind exact intent, attempt, immutable
mandate, all semantic versions and current fence. First send additionally names the
latest authorization and ordinal zero; later child allocation is deliberately a
separate pending all-child-proof contract. These are commands, not grants.

`CurrentDispatchInputsV2` carries an independently acquired full observation,
supported interpreter and fresh clock/lease. Observation query fingerprint must
bind the exact command; its source inventory includes every required group even
when unavailable. The private issuer must reject unavailable sources, reproduce
the cut under the writer gate, validate horizon and lease separately, and never
renew the original mandate. Unknown cross-epoch continuity remains unavailable.

History verification must consume every selected effects record including resolved
and terminal rivals. Only authenticated exact own initial publication and registered
own pre-send successors can be exempted from normative generation, after checking
original command/output/predecessor/mandate/interpreter at their original cut.
The complete current inventory still contains those own records. No public exemption
list or permission flag exists.

`DispatchBoundaryFailureV2` names denial, stale/conflict, unavailable-source and
unavailable-original-interpreter cases. Runtime ports, v2 attempt/store records,
selected history decoding, durable ticket issue/consume, evidence/reconciliation,
denying-v2 integration, delivery/recovery purposes and migration remain subsequent
contract/implementation barriers. The file does not claim the full R16 wire is done.

## Evidence and remaining guarantees

| Claim | Owned data → independent observable | Forbidden substitute → fixture | Status |
|---|---|---|---|
| Closed original mandate | required fields/origin → public validation | missing/unknown fields, any-future-call alias | shape tested |
| Exact bytes retained | original binary fields → serialization roundtrip | UTF-8 reconstruction or current compiler output | shape tested |
| No DTO grants permission | separate mandate/acquisition/current observation → future source verifier and sink | caller send flag, cloned observations, missing source | flag rejection tested; runtime HOLD |
| Exact call acceptance | initialized origin + embedded adoption → selected full acceptance batch | pre-accept dispatch, cross-call adoption reuse | HOLD |
| Immutable horizon and fresh lease | original horizon + separately acquired clock → writer expiry/race observer | deadline renewal, unknown epoch comparison | HOLD |
| Complete conflict generation | all selected records/original cuts → independent journal/materialization | latest-only history or identity-only own exemption | HOLD |

All mutation families remain required for runtime: omission, unknown addition,
substitution/alias, duplicate/reorder, stale/race, N/N+1 bounds and logical/physical
identity mismatch. Shape checks do not discharge provenance, fingerprint correctness,
semantic equality, completeness, custody, clock or operation admission.

## Bounded executable continuation

The explicit profile-10 offline runtime now adopts a v2 PlanEffect preview,
authorizes it, commits its first transmission, and selects a separate closed
`broker_dispatch` consumption before giving a private one-shot permit to the
hermetic provider. Stable v1 and profile-7 readers retain their interpretation.
This is not the full R16 completion: R14 consequential-call acceptance, R17 delivery,
terminal evidence/reconciliation, retry/recovery intents and real providers remain
outside this slice. The HOLD rows above describe the earlier boundary-only evidence;
runtime witnesses are in `tests/composition/test_dispatch_runtime.py`.

The complete selected history carries every original command/result and original
selected cut. Its separate `effects_history` source is an ordered inventory of all
member metadata (including kind, cut and semantics) plus exact command/result hashes.
The complete-history fingerprint independently hashes full members. This avoids
recursively embedding the preparation and duplicating full members inside the source;
the historical verifier still joins the original selected preparation and replays
its complete earlier prefix. No IPC size bound is widened.

The final writer recaptures every source under the shared authority gate. SEND
selection reserves one use of the independent stable grant identity by counting all
historical SEND records, including resolved work when a registered interpreter exists.
Unknown historical grant interpretation denies. Latest unresolved work separately
blocks fresh adoption; a new preview cannot absorb an old SEND into its baseline.

Consumption selects exactly one immutable child/ticket. Runtime assembly independently
retains its original resource object, which independently retains its constructor-created
provider. Current capture and the immediate leaf boundary require both mutable aliases
to match those original objects; equal provider contracts cannot substitute a different
leaf. The last check does not require an ACTIVE grant after an already selected SEND.
Replays and startup recovery never mint permits. Losing execution after consumption leaves an unresolved outcome;
it is not evidence that no external effect occurred. Resource keys and clock epoch
are held independently in memory: reopening a runtime with the retained resource
object is supported; full OS-process restart recovery is not implemented. The first
witness retains roughly 90 MB of journal data, a material bounded-slice limitation.
