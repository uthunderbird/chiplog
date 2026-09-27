# H1 completion issuance V2 freeze

This is a tests-first interface contract for the separate installed
[postseal finalization operation](R14-R17-H1-POSTSEAL-RECOVERY.md#separate-installed-finalization),
not a claim of implemented finalization. V1 remains immutable historical wire.
V2 separates original selected ISSUE/CURRENT evidence from final invocation
evidence. It changes only `WorkerAuthentication.applicability_bytes` and its
schema/fingerprint, not `CompleteDeliveryBatchV2`, its owner slots, physical
members, operation or stable completion identity.

## Closed value and canonical encoding

All DTOs below use the existing strict, frozen `DispatchObservationDTO` codec:
unknown fields fail; bytes use its base64 JSON encoding; canonical JSON is
UTF-8, sorted keys, `ensure_ascii=False`, separators `(',', ':')`. Require exact
canonical byte equality on decode. `Identity` is a nonempty string; `Digest`
is 64 lowercase hexadecimal characters. All listed fields are required.

| `H1CompletionIssuanceV2` field | Exact type / meaning |
| --- | --- |
| `schema_id` | Literal `chiplog.composition.h1-completion-issuance.v2` |
| `assembly` | `PrepareH1CompleteAcceptanceAssemblyV1` |
| `capture` | `H1CompletionCaptureV1`, describing the final invocation only |
| `owner_exchanges` | Tuple of four `H1CompletionOwnerExchangeV1`, roles exactly `completion`, `conversation`, `effects`, `terminal_work` |
| `scope_issue_exchange` | `H1CompletionOwnerExchangeV1`, role `scope_issue`; original selected ISSUE |
| `scope_current_exchange` | `H1CompletionOwnerExchangeV1`, role `scope_current`; original selected CURRENT |
| `final_current_exchange` | `H1CompletionOwnerExchangeV1`, role `scope_current`; distinct postterminal CURRENT |
| `recovery` | `H1CompletionRecoveryRefV1` below |
| `terminal_admission` | `H1CompletionTerminalAdmissionWitnessV1` below |

`H1CompletionRecoveryRefV1` has exactly `root_id: Digest`,
`journal_instance_id: Identity`, `completed_chain_head: Identity`.
The head identifies the authenticated recovery prefix through the selected
ROOT's terminal-work result. Other roots may interleave in the global journal;
the reader validates the full journal and selects the exact referenced prefix.

`H1CompletionTerminalAdmissionWitnessV1` has exactly
`terminal_call_fingerprint: Digest` and
`preterminal_current_exchange: H1CompletionOwnerExchangeV1` (role
`scope_current`). Its binding is the complete enclosing V2 candidate, including
assembly, capture, historical scope exchanges, final CURRENT and recovery ref;
it is not a separately transferable receipt. Derive the terminal fingerprint
with the existing `_terminal_call_identity` algorithm over the actual fourth
call, including its route, budget and owner frame.

The authentication schema equals the inner schema exactly; applicability
fingerprint is `sha256(canonical applicability bytes).hexdigest()`.
Keep `_h1_identity(assembly)` unchanged: V1/V2 and retries share one original
completion command identity. Existing invocation fingerprint validation remains
mandatory for the final capture; the whole applicability digest additionally
binds all V2 evidence. No self-referential batch digest is added.

## Producer and durable trust boundary

Only installed B/enrollment can register the nonserializable one-use marker,
after verifying the ROOT/chain and retaining the actual four live exchanges,
admitted terminal clearance, final P CURRENT, mounted installation and owned
continuation. A public DTO, copied marker, durable result or caller locator
cannot issue authority. The writer consumes the marker once before reentrant
work, compares the complete candidate with its private enrollment record,
derives invocation/read-manifest inputs independently and rechecks the complete
source cut at writer admission without owner IPC. Failure burns the marker.

The admission witness is a **historical writer-accepted claim**. Selection in
the authenticated owner journal durably attests that the trusted installed
writer checked its actual private admitted-clearance record and the complete
candidate. Existing in-memory clearance state and recovery stage records do
not independently prove the admission event. Fingerprints and timestamps alone
cannot repair that gap. An independently durable admission-event proof would
require a separately authenticated event; this contract does not claim one.
Before selected writer acceptance, serialized V2 alone grants no authority.

## Required joins

1. Reopen the enrolled recovery journal by installed authority, never a caller
   path. Independently derive every ROOT field from authenticated native V2
   history and compare root ID, journal instance and complete ROOT. Validate
   authority-cut and P-scope residual siblings. Reconstruct all four pinned
   semantic inputs and complete results from independent sources/predecessors;
   compare them with assembly and actual exchanges. Effects pins inner bytes
   while its replay uses a fresh, separately validated outer route.
2. Original ISSUE/CURRENT join selected H0/R17 origin, native P/E anchor and
   retained accepted wire pairs, exact historical trust prefixes, scope record,
   source anchor, revision and lineage. Do not require their snapshot/session
   to equal final capture, and do not substitute any fresh CURRENT for them.
3. Four replay exchanges use final capture's enrolled sessions and exact
   request/result bytes, routes and correlations. Preterminal CURRENT joins
   the same selected scope and recovery source and is checked by P at terminal
   admission before the fourth send. Final CURRENT is a distinct actual call
   after the fourth exact success. Both fresh CURRENT pairs use their own
   authenticated trust prefixes; final CURRENT's snapshot joins final capture.
   Their scope/reference, tenant, database, original Run and selected seal must
   agree with reconstructed sources. Verify CURRENT candidate/result semantics
   against each prefix; never require final result bytes to equal historical
   CURRENT result bytes merely because the scope is unchanged.
4. Fresh exchange timing belongs to final capture's clock epoch: preterminal
   return precedes terminal send, terminal return precedes final CURRENT send,
   and each return follows its send. Cross-epoch timestamp comparisons are
   invalid and hold issuance. Historical exchange timestamps are not compared
   with final-invocation timestamps. The private issuer establishes actual
   causal order; serialized timing is its retained claim. P rechecks source
   currentness after final CURRENT's await and at final writer admission.
5. Reproduce the unchanged complete batch from assembly. Independently derive
   its expected manifest; equality with a caller-supplied capture is insufficient.
   Selected verification joins exact authenticated owner request, stable command
   identity, request digest, physical decision digest, predecessor commitment,
   tenant sequence, delivery binding and exact physical command/readback.
   Historical reconstruction uses the relevant as-of cuts, not today's inventory
   or live provider state. Final CURRENT need not remain current after selection.

## Dispatch, pending and replay

Dispatch explicitly on outer applicability schema and require matching inner
schema. V1 retains every existing V1 constraint. V2 uses only the contract above.
Missing, unknown, mixed or malformed versions fail closed; never coerce V1 into
V2 because their command identity matches. Update codec, historical exact-type
checks, `r14_loop_history._completion_v2_schema_dispatch`, owner journal
`_validate_v2_binding` and `select`, and delivery-evidence root decoding together.
Both supported versions require the existing closed H1 delivery binding.

Locate selected or unresolved publication before opening B. Selected/pending
recovery authenticates the retained original version, complete evidence and
original owner decision, reconciles predecessor/resulting materialization and
reads back the exact command. Missing dependencies, corrupt evidence, unknown
versions and ambiguous outcomes hold; no downgrade or current-owner regeneration.
It neither remints issuance nor consumes a marker again. Loss of response after
admission requires reconciliation: return exact selected result when proven,
otherwise existing pending/uncertain behavior. Only authenticated absence permits
a new attempt with a fresh marker. A terminal receipt follows exact selection
and physical readback, and asserts neither SEND permission nor delivery closure.

## Tests before production changes

First demonstrate missing behavior as RED, then prove:

- Fresh-generation recovery with historical ISSUE/CURRENT and distinct fresh
  postterminal CURRENT succeeds with unchanged physical batch; V1 still replays.
- Substituted ROOT/prefix/run/seal, pinned inner bytes, live results, sessions,
  scope wires, manifest or invocation fail even with recomputed public digests.
- Missing/stale terminal admission, transferable witness, premature or reused
  CURRENT, cross-epoch timing and source changes across await/writer admission
  fail before selection; a copied/public marker never authorizes publication.
- Missing/unknown/mixed applicability versions fail through every dispatch
  surface, including selected journal, delivery binding and pending recovery.
- Selection-before-readback and uncertain response recover exact retained bytes
  without B IPC, fresh CURRENT, reissuance or second marker consumption; corrupt
  or missing dependencies hold. Concurrent attempts cannot select twice.

Observe authenticated journal bytes, exact physical members, actual broker
frames and owner-call counts. DTO roundtrips alone do not prove these outcomes.
