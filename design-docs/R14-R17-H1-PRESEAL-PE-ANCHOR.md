# H1 V2 preseal P/E anchor for recovery

Status: CONTRACT FROZEN; implementation and mounted witnesses open. This
refines [post-seal recovery](R14-R17-H1-POSTSEAL-RECOVERY.md) at the crash cut
where the V2 seal is durable but no B preparation or E evidence was issued.

## Required boundary

The original driver initialization, V3 Prepare, prepared Run, physical V2 seal,
and seal-bounded lineage are already selected from authenticated native history.
They do not retain P's accepted scope, policy, custody and source signature or
E's member provenance, disclosure, narrowing and worker route identity. Those
facts are currently held by live, process-local capabilities. The post-seal E
selection closure requires a completed request and cannot be made before the
seal. A recovery input cannot be reconstructed by accepting a saved DTO, using
the new process's worker session, or substituting that later closure.

Before the V2 seal decision is appended, installed P/E owners authenticate and
capture the missing facts while their sources are live. Capture is seal-agnostic
and identity-owned. The seal writer binds that capture to its independently
computed command, selected seal and owner-as-of snapshot under the shared
authority gate. The canonical, versioned anchor is retained as a sibling of
the V2 decision's native fields in the **same authenticated DECIDED entry**.
The anchor is evidence of historical facts, never a fresh execution, terminal
or publication capability. No new physical seal member or public wire
operation is authorized by this contract.

The journal authenticates the complete decision. The physical seal fingerprint
does not authenticate the sibling anchor. DECIDED can precede SQLite
materialization: pending recovery must materialize the original retained
command and anchor, and may not recapture or replace P/E facts. The historical
reader refuses unresolved PENDING decisions and requires exact materialization.

## Capture and retained fields

An installed private owner exposes `capture(preflight)` followed by a
synchronous, no-IPC `recheck_and_bind(capture, preflight, command,
retained_v2, owner_asof)` at decision admission. Capture performs any owner
IPC before the SQLite transaction, checks the source cut before and after, and
cannot be copied, deserialized or supplied by a caller. Recheck consumes the
receipt once and joins the exact enrolled runtime, database, Prepare, Run,
owner-as-of and publication command. A failed or stale capture denies the seal;
it does not create a partial anchor.

The no-IPC recheck is valid only if P's authoritative scope, policy, custody,
source-version and supervisor-session mutations share the installed runtime's
gate through decision append. The installed CURRENT owner is a stateless
projection of the supplied authenticated snapshot (`selector_generation=0` is
not a pin); the enrolled trust, resource and supervisor mutation APIs use the
common gate. Custody's gate binding drains mutations admitted before bind, and
its close does not release the lifetime pair lock while an installed runtime
remains bound. Focused lifecycle tests cover these obligations. The anchor
issuer remains ineligible until the decision owner and writer replay are
mounted. Final P replay must recompute CURRENT and every source join under one
uninterrupted gate hold in the **DECIDED writer**, then
append the decision before releasing it. This assertion applies only to those
installed, enrolled mutation APIs; an unbound writer cannot use this issuer.
An unavailable/restarted session or changed source denies the seal.

The earlier publication admission guard and the DECIDED writer are separate
gate holds. A check in the guard alone cannot establish continuity. Native,
P and E replay must precede consumption of the one-use H1 preflight in the
writer's gate hold; after consumption, `_issued` can no longer authenticate
the preseal native cut. No `await` or gate release may occur between that
ordered recheck and `_append_decision`.

The anchor records only facts not independently recoverable from the native
prefix: P accepted scope and policy references plus canonical bytes, recipient,
custody generation and digest, source-signature digest, and the accepted
owner-exchange commitment; E's ordered complete member occurrence vector with
provenance, disclosure and narrowing; and E worker runtime instance, route
generation and original session. It binds the selected native identifiers,
principal, tenant, database genesis, command and physical seal locator without
duplicating native payload bytes. Every residual field that cannot be derived
from the bounded native cut must be explicit and canonical. An omitted field
is a contract error, not permission to take its current value.

Existing E exact-head IDs include the E journal instance. A compact anchor
with no E journal entry cannot claim those IDs. Historical P/E projections
use a new versioned anchor-derived identity domain: hash the canonical tuple
of domain, deployment, database, genesis, tenant, selected decision, role and
member ordinal (when applicable). No nonexistent E journal locator or old
exact-head ID may be inferred. A decoded anchor by itself grants no old or
current E capability.

The selected decision ID is supplied by the installed historical reader after
it authenticates the containing journal entry. It cannot be embedded in the
anchor: that ID hashes the entire DECIDED entry, including the anchor. Worker
route generation remains the exact string issued by the original broker
session. Disclosure uses the actual `UNRESTRICTED`, `DENY_ALL` and
`ENDPOINT_RESTRICTED` vocabulary; endpoint narrowing may be stricter than the
original label and must be checked as a valid narrowing, not forced equal.

## Canonical historical projection references

This section freezes the historical `ExactHead` derivation; it does not change
the retained anchor schema. Let `C(value)` be UTF-8 JSON with sorted object
keys, `ensure_ascii=False`, separators `(',', ':')`, and no floats, nonfinite
numbers or duplicate keys. Let `H(bytes)` be lowercase hexadecimal SHA-256.
`A` is the decoded, canonical anchor and `D` is the authenticated containing
DECIDED entry ID supplied by the historical reader.

The base identity is exactly
`h1_preseal_pe_anchor_projection_identity(A, selected_decision_id=D,
role=role, member_index=i)`: hash `C` of the ordered array
`[PROJECTION_IDENTITY_DOMAIN, ANCHOR_SCHEMA_ID, 1, deployment_id, database_id,
database_genesis_digest, tenant_id, D, role, i]`. The domain is
`chiplog.h1.preseal-pe-anchor-projection.v1`; the schema is
`chiplog.execution.h1-preseal-pe-anchor.v1`. Enrollment values come from
`A.binding`. `i` is the zero-based occurrence index for `E_MEMBER` and JSON
`null` for `P` and `E_WORKER`.

Each projection payload has exactly these keys:

```json
{
  "schema_id": "chiplog.execution.h1-historical-pe-projection.v1",
  "anchor_digest": "H(A.canonical_bytes())",
  "selected_decision_id": "D",
  "role": "role",
  "member_index": null,
  "facet": "facet",
  "facts": {}
}
```

The quoted expressions above denote substitutions, not literal field values;
`member_index` is `i`. Only the following role/facet pairs are valid. `facts`
is the exact indicated JSON object, preserving every field and array order:

| Role | Facet | Facts |
| --- | --- | --- |
| `P` | `accepted_scope` | `A.p` |
| `E_MEMBER` | `provenance` | `A.e_members[i].provenance` |
| `E_MEMBER` | `disclosure` | `A.e_members[i].disclosure` |
| `E_MEMBER` | `narrowing` | `A.e_members[i].narrowing` |
| `E_WORKER` | `worker` | `A.e_worker` |

For payload `V`, set `fingerprint = H(C(V))`,
`head = 'record:' + fingerprint`, and
`identity = base_identity + ':' + facet`. The full-anchor digest binds each
facet to the complete accepted scope, native binding and ordered member
vector, without duplicating native payload bytes. The P projection is evidence
of accepted scope; `DeliveryObservation.policy` and recipient still use the
exact retained policy reference and recipient, not this projection reference.
The three E member references populate the corresponding historical-envelope
fields; narrowing is a one-element tuple. The worker reference populates
`DeliveryObservation.worker_fence`.

These heads identify deterministic historical evidence payloads. They do not
assert that a physical `record:` row or E journal entry exists, and do not
recreate an old E identity or grant a live owner capability. The reader must
authenticate and join the anchor before issuing any of these references;
computing hashes from caller-supplied bytes is insufficient.

Historical validation must reject `DENY_ALL` narrowed to an allowed endpoint,
even though the RECORD codec alone permits that combination. An
`UNRESTRICTED` original label may narrow to the selected endpoint; an
`ENDPOINT_RESTRICTED` original label must contain that endpoint. The retained
narrowing must be exactly `ENDPOINT_RESTRICTED` with the singleton selected
endpoint. Native original labels and retained disclosure labels must agree.

## Historical read and use

### Versioned V2 authority cut

The direct V3 Prepare → V2 seal route additionally retains
`h1_v2_authority_cut_v1` as a JSON object sibling of `h1_preseal_pe_anchor` in
the same authenticated DECIDED entry. Its strict descriptor has exactly these
fields: `kind` = `H1_V2_AUTHORITY_CUT_V1`, `version` = `1`, `tenant_id`,
`operation_kind`, `operation_id`, `request_fingerprint`, `expected_head`,
`commit_sequence`, `selected_response_seal`, `envelope_sha256`,
`database_binding`, `predecessor`, `resulting`, and `reference`.
`selected_response_seal` is the exact native `CallSubjectHead`;
`database_binding` has exactly `canonical_path`, `st_dev`, and `st_ino`.
`reference` is an `AuthorityCheckpointRefV1` for the canonical, complete
authority snapshot captured after the seal's writes inside the writer
transaction. It is a post-image, despite the storage type's preimage naming.

The descriptor's command fields equal the independently reconstructed V2
physical command, and `commit_sequence = expected_head + 1`.
`envelope_sha256` is lowercase SHA-256 of the existing canonical V2 physical
envelope bytes; rebuilding its command must equal the DECIDED command,
including every ordered record and fence field. The native seal locator must
match that envelope's seal member. Database identity must equal the admitted
runtime bundle identity. `predecessor` and `resulting` equal the containing
decision's independently anchored commitments; the snapshot reference's
digest equals `resulting`. Its format, algorithm, authority surface, length,
canonical bytes, and digest must all verify. The containing decision ID is
supplied after journal authentication, not embedded in this self-hashing
descriptor. The P/E anchor and authority cut must join the same native seal,
command, tenant and database.

Only the predecessor commitment is retained: no predecessor snapshot bytes
or reference are required. The immutable blob contains the exact writer
post-image; DECIDED contains its reference, not inline snapshot bytes.
Reconstructing either image from six native facts, fabricated rows or a later
database state is forbidden. The extension adds no physical record: V2 seal
membership remains exactly Run, seal and frontier, with unchanged V2 retained
seal and physical envelope types.

The installed writer activates the admitted checkpoint bundle before this
route. Under the existing authority gate and SQLite transaction it checks
admission and expected head, applies the three records/publication/head
writes, captures the complete post-image and computes `resulting`, then
stages and durably verifies the immutable blob before DECIDED append. The
existing `authority_checkpoint_guard(resulting, exact_bytes)` supplies these
post-write bytes. The DECIDED writer performs the native/P/E ordered recheck
and one-use consumption described above and appends both siblings in that
same entry. SQLite commit and selected-decision completion follow. No IPC or
gate release is introduced between writer recheck and decision append.

Before pending materialization or replay, recovery authenticates the complete
decision and validates the descriptor, blob and exact selected membership.
It replays the original command with the recorded predecessor/resulting and
unchanged siblings; it must not recapture P/E or stage a replacement cut from
current rows. The original database may still be at the predecessor when the
blob already represents the selected post-image. Recovery accepts only the
existing authenticated predecessor/resulting reconciliation, verifies the
materialized result, and finishes the decision before historical selection.
A crash before DECIDED may leave an unselected blob; it grants no authority.
A missing, corrupt or mismatched selected blob holds recovery.

Historical readers explicitly dispatch on this new extension version. They
authenticate native V2 selection first, resolve the bound snapshot, require
the selected tenant head and exact publication/three-member membership, and
perform the full historical inventory and owner-as-of reconciliation over
that cut. Other rows in the complete snapshot remain subject to inventory
validation; they are not additional seal members. The V2 native ROOT remains
independently derived. Readers must not relabel the retained seal as V3 or
accept V3's `h1_historical_checkpoint` field as this extension. Existing V2
checkpoint rejection paths require explicit version-aware implementation;
this contract does not claim that they already accept the new descriptor.

Legacy V2 without `h1_v2_authority_cut_v1` remains unsupported for full-cut
historical completion reconstruction, even if its native ROOT is readable.
Malformed, unknown-version or inconsistent present descriptors are integrity
failures. No migration may manufacture the missing historical post-image.

An installed historical reader finds the anchor only through the independently
selected V2 decision. It checks canonical bytes, journal authentication,
enrollment, binding, full native cut, member order and endpoint rules, P
scope/policy/custody joins, and the worker's original route identity. It
reconstructs the exact `DeliveryObservation` and `NonSchedulerFence` through
P/E-owned historical projections. Their old worker session need not be live;
the projections are evidence for a pure preparation request only. The B
coordinator then builds the complete canonical COMPLETION `STAGE_INPUT`, writes
and reads it back before owner IPC, and compares all fields again on restart.

The reconstructed request requires the full historical
`FirstPathCompletionCutV2`, including its selected admitted input, bounded
materialization commitment, complete sources and frontier. The six native
facts exposed by `H1RecoveryStageSource` are insufficient. Reconstruction must
use an extension-aware full historical first-path source path (the existing
`H1FirstPathSources._read_selected_cut(..., historical=True)` followed by
`_read_v2_source(..., historical=True)` path currently resolves V3 cuts only),
preserving its independent inventory
and owner-as-of checks. It must never substitute the current database head,
current inventory or current worker session. `delivery.source_frontier` is
that historical cut's tenant commit sequence. `fence.runtime_generation` is
the exact retained `e_worker.owner_route_generation` string, and its worker
session is the retained original session joined to the selected Run. The
completion command ID remains `h1-first-path-completion:` followed by SHA-256
of the full source's canonical bytes.

Missing anchor on a legacy V2 seal means historical P/E is unsupported unless
another independent immutable source proves every missing field. Malformed or
inconsistent present anchor is an integrity failure. Later unrelated committed
decisions do not change the selected historical cut. Fresh broker admission,
terminal P currentness, one-use B authorization and selected publication remain
separate checks.

## Acceptance witnesses

Tests first establish that an installed V3 Prepare followed by a direct V2
seal currently leaves no E member/worker evidence. The implementation must
then prove: capture before seal; seal and anchor in one authenticated decision;
no anchor from wrong issuer or changed P/custody/worker state; no duplicate or
incomplete member vector; exact replay of the same decision; crash before
DECIDED, after DECIDED but before materialization, and after materialization;
restart with ROOT and `STAGE_INPUT` absent; later unrelated publication;
tampered anchor rejection; copied/cross-runtime capability rejection; and
complete canonical request equality across restart. Physical V2 seal members
remain exactly Run, seal and frontier.

Authority-cut witnesses additionally cover exact post-image staging before
DECIDED, wrong predecessor/resulting/database/envelope/seal bindings, absent
or corrupt blobs, malformed and unknown extension versions, legacy V2 without
the extension, and pending replay on both sides of SQLite commit. After a
later unrelated publication, the full historical cut and complete canonical
COMPLETION input must remain byte-identical. These witnesses remain open.
