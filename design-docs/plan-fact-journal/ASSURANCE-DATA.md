# Plan / Fact / Journal — data assurance

> Status: proposed and not operationally adopted. Authority and provenance are defined in [README.md](README.md).

## Adoption-assurance annex B — deletion and external disclosure

P1-03 is required for every adopted deployment that stores controlled data. Missing
or unknown deletion/retention policy, dependency coverage, or recovery frontier
yields `HOLD_ADOPTION`. P1-04 is required only when external disclosure is included
in the adopted workload envelope; otherwise the capability must be absent and
default-deny, not partially implemented. The P1 numbering records triage severity,
not optionality.

### P1-03 — deletion reachability and derived data

`DeletionRequest` is a stable identity whose append-only `DeletionRequestRevision`
family is the authority. `current_deletion_revision_id` is a rebuildable cache and
never a mutable source of truth: every state transition appends one revision and
CASes the exact greatest committed predecessor. Revision state is only
`PENDING | PROPAGATING | COMPLETE | HELD`; `DELETION_PENDING` is the derived
deployment/outcome class for any request not safely complete, including unknown or
conflicting policy, and is never written as a second mutable request status. Each
scope has a monotone epoch
high-water mark allocated by the separately protected deletion control plane.
The request ledger contains only an unlinkable per-deletion token. Original
`scope_kind/scope_id` routing exists solely in a scope-keyed encrypted
`DeletionRoutingEnvelope` while propagation is open. After every required verified
destruction receipt, the envelope advances `SHRED_PENDING→SHRED_CONFIRMED`; only
then may the request publish `COMPLETE` and prove that request, acknowledgement, and
audit joins cannot recover the scope; epoch enforcement uses the opaque token and
frontier root without recreating the subject join.
Command IDs, precise store IDs/watermarks, policy/registry/hold heads, recovery scan
IDs, and detailed proofs likewise live only in a scope-keyed encrypted
`DeletionOperationalSidecar` while propagation is open. Completion validation reads
and CASes those exact heads before shredding. The survivor revision/acknowledgement
records retain only unlinkable token, epoch, coarse store/proof class, acknowledgement
bit, and non-semantic ordering ordinal; any exceptional survivor needs an explicit
field-matrix proof of non-unique, non-joinable cohort membership.
Key destruction uses a recoverable external protocol, not a fictitious database/KMS
transaction. After all producers are fenced, each envelope advances
`OPEN→SHRED_PENDING`; reads/writes stay denied. One idempotent
`KeyDestructionOperation` binds key head, deletion token/epoch, and request head.
Retries query or complete that same KMS/HSM operation. Only a verified irreversible
destruction receipt permits `SHRED_CONFIRMED`, and `COMPLETE` becomes visible only
after every required receipt and store acknowledgement exists. A crash after actual
destruction but before database completion recovers from the receipt without
decrypting routing metadata. Lost, delayed, forged, or replica-stale receipts hold;
KMS restore cannot reactivate a head covered by a current destruction receipt and
deletion frontier.
Legal revision edges are `PENDING→PROPAGATING|HELD`,
`PROPAGATING→HELD|COMPLETE`, and `HELD→PROPAGATING`; resuming a hold must revalidate
policy, the frozen dependency registry, fences, and acknowledgements before
propagation continues. `COMPLETE` is terminal for that deletion request and epoch.
Later deletion allocates a new request at a strictly higher scope epoch rather than
appending a regressive successor.
Task-scope deletion reaches its revisions, occurrences, claims, artifacts,
relations, attempts, effects, projections, disclosure ledgers/debits/envelopes,
encrypted response references, caches, exports, participation confirmations, and
receipt projections; safety-minimal command
and effect receipts survive only under the field-minimization rule below. Every
create/read/derive/dispatch transaction CAS-checks every applicable scope watermark;
a stale or unknown epoch rejects writes and suppresses/quarantines reads. Late
payload cannot attach beneath a deleted identity.

Every controlled store registers deletion dependencies, handler, propagation SLA,
proof, and epoch: plan/claim payloads, artifacts, indexes, embeddings, caches,
projections, replicas, outboxes, retries, jobs, model/session contexts,
personalization, audits, backups, and recovery stores. A request freezes the
dependency-registry generation. Each acknowledgement covers a watermark after all
earlier writers, jobs, leases, and outboxes are drained or fenced. Derivatives carry
source scope/epoch; stale post-fence output is rejected or quarantined. A new handler
joins every open deletion before registry activation. A stored `COMPLETE` revision
requires all frozen-generation acknowledgements and zero unfenced producers;
otherwise the derived outcome remains `DELETION_PENDING` over a stored
`PENDING`, `PROPAGATING`, or `HELD` head.

Derived edges declare `REDACT`, `INVALIDATE`, `RECOMPUTE`, `MINIMIZE`, or
`QUARANTINE`. Missing/failed handling quarantines. Tombstones, receipts, logs, and
minimized derivatives cannot reconstruct deleted payload or enable a new purpose.

Deletion preserves append order through a redaction event while destroying payload
keys or erasing fields irreversibly. A versioned field matrix may retain only opaque
IDs/ordinals and content-minimal non-joinable receipt fields; values, source
snapshots, request fingerprints, relation endpoints, and reconstructive metadata are
destroyed or unlinkably tokenized. Projection over a redacted dependency returns
`DELETED/UNAVAILABLE` and never consults replicas or sibling records. Reconciliation
must prove surviving receipts cannot be joined to reconstruct the subject.

For core history, the only unconditional survivors are the redaction event ID,
deletion epoch, non-semantic ordering placeholder, policy/handler proof class, and
controlled-store acknowledgement state. Original task/occurrence/claim IDs,
timestamps, actors, relation endpoints, and source/payload digests survive only when
the field matrix proves them non-reconstructive; otherwise they are destroyed or
replaced by unlinkable per-deletion tokens. A survivor can prove “a slot was
redacted under policy” but not recover what the fact, plan, person, time, or relation
was. This redaction is not a semantic correction and never substitutes new history.

Before append, immutable audit material is split: the hash chain receives only a
domain-separated randomized commitment, opaque envelope ID, coarse non-semantic
event class, and coarse time bucket. Subject IDs, semantic bytes/digests, auth and
policy joins, and result data live only in a scope-keyed encrypted sidecar. Deletion
cryptoshreds that sidecar key and appends a redaction receipt bound to the audit
ordinal. Raw or stable semantic/result digests are forbidden in immutable entries or
obligations; the field matrix must demonstrate offline dictionary and cross-ledger
join resistance after shredding.

The deletion ledger/frontier is protected from backup rollback. Each domain has one
exact-CAS `DeletionFrontier` head with monotone sequence, previous hash, per-scope
epoch root, frozen registry generation, signing-key continuity, and independent
non-rollback witness receipts. Publication atomically advances the authoritative
head before detail can be pruned; crash/retry either leaves the prior head current or
returns the identical successor. Before restored data is queryable or replayable,
recovery obtains the externally witnessed current head rather than trusting the
backup's head, verifies gapless continuity/key history, scans every
store and queued event, erases/quarantines stale payload and derivatives, regenerates
indexes, and records per-store recovery acknowledgements. Missing, older,
unverifiable, or incompletely acknowledged frontiers reject promotion.

Every controlled data class requires a versioned `RetentionPolicyRevision`. Holds
are append-only revision families aggregated by one exact `RetentionHoldSetRevision`.
Every deletion transition and acknowledgement records the exact policy and hold-set
heads. `COMPLETE` is one CAS transaction over deletion head, scope epoch, registry
generation, policy head, hold-set head, and all acknowledgements; any concurrent
hold/policy change forces `HELD` or revalidation. Post-`COMPLETE` holds cannot
resurrect erased payload. Unknown or
conflicting policy yields `DELETION_PENDING`; holds are scoped/content-minimal and
their release resumes propagation. Deletion override is explicit. Permanent dedupe
retains only a non-reconstructive request token and terminal result class—not request
fingerprints or semantic/auth inputs capable of recovering deleted content.

### P1-04 — external privacy contour

External availability is default-deny. Authenticated access uses non-transferable,
principal-bound grants; anonymous/public access has no per-subject availability
unless an independently adopted public schema explicitly enumerates it. All
anonymous traffic shares one public disclosure cohort. Purpose and downstream
retention are contractual labels unless a named mechanism proves enforcement;
bearer capabilities or self-declared purpose are never proof. Queries cannot join
private planning/claim relations.

General external visitors never receive per-subject calendar blocks. When
availability is necessary, a principal-bound audience receives only fixed-bucket,
purpose-specific coarse availability with rare/unique patterns suppressed,
unlinkable pseudonyms rotated per audience/epoch, and byte-identical repeated output
within one release epoch. A visitor may receive bounded details of one event only
through a greatest-current `ACTIVE` `ParticipationConfirmation` bound to the exact
occurrence revision, authenticated participant, confirming authority/source,
allowed-detail scope, purpose/audience, validity interval, and deletion epoch.
Detail disclosure CASes that head with grant/schema/debit heads; withdrawal,
revocation, expiry, dispute, or ambiguity denies. Its bounded schema still forbids
titles, full participant lists, stable identifiers, and fine time unless a separate
necessity policy explicitly allows the individual field. Cross-audience/epoch
linkage uncertainty denies disclosure.

One atomic `DisclosureLedger` is keyed by protected subject and correlation domain
and spans every capability, identity, endpoint, schema version, cache/export, and
rolling horizon. Requests use fixed non-sliding buckets and fixed coarse values;
arbitrary windows, counts, and existence queries are forbidden. The ledger debits
before response. Anonymous requests debit the single public cohort. Coalition
detection is supplementary only: uncertain linkage/accounting, Sybil ambiguity,
cache/export bypass, or budget exhaustion denies disclosure. `consumed_budget_cache`
and `current_debit_id` are rebuildable pointers only. Every allowed response
atomically CASes the exact prior debit head, appends one immutable
`DisclosureDebit`, checks the rolling-horizon total and release epoch, and commits
the receipt before returning; concurrent requests cannot spend the same remainder.

Every disclosure grant and schema is an immutable revision family. Issue, amend,
revoke, and expire commands CAS the exact current family head; only the greatest
committed `ACTIVE` grant and schema revision are effective. A disclosure transaction
records the schema-administration `issuer_authority_head_id` in both the schema
revision and the typed authority-mutation audit, and CAS-checks that authority with
the exact schema predecessor/result. A response transaction CAS-checks the exact
active grant and schema heads together with the ledger debit head. If revocation,
expiry, schema replacement, or a competing debit wins first, no response or
externally distinguishable receipt is emitted.

`DisclosureRequestEnvelope` is unique by domain and disclosure request ID; its
canonical fingerprint covers every semantic/auth input and policy version. It,
exactly one debit, one internal response digest/reference, and one unique opaque
receipt token commit atomically before bytes become returnable. The debit's
`request_envelope_id` resolves back to that envelope and the IDs/heads must match.
Every byte-return path—including identical retry, cache hit, export, response
reference, and receipt-mediated retrieval—freshly authenticates and checks/CASes the
current grant and schema family heads, issuer-authority head, source deletion
watermark/epoch, and schema TTL. Cached bytes are non-authoritative. Only while all
heads remain effective may an identical retry return the same committed bytes
without another debit; stale/revoked/expired/deleted/unknown state returns the common
denial class and no bytes. Changed-payload key reuse rejects. The external receipt projection never
exposes the debit or envelope linkage.

Every disclosure derivative stores immutable source scope token, deletion epoch,
and deletion handler/policy binding. Completion destroys encrypted response
references and redacts or unlinkably tokenizes response digests/fingerprints unless
the deletion field matrix proves them non-reconstructive; retries and exports compare
the current source watermark before use.

Every projection uses a versioned `ExternalDisclosureSchema` allow-listing fields,
precision, TTL, audience, purpose, and correlation scope. Exact provider IDs, fine
times, stable source IDs, titles, participants, private mandates, and tombstone
existence are forbidden absent a bounded audit/dispute schema proving necessity.

Exact disclosure audit remains internal under access control. External receipts
contain only an opaque per-request token and coarse terminal class—never subject,
query/output hashes, precise time, capability/audience correlation IDs, denial
reason, or deletion/tombstone distinction. Unauthorized, nonexistent, and deleted
receipt lookups use one versioned externally observable response class and debit the
same correlation-domain ledger. Stronger indistinguishability requires the adopted
threat model and status/body/size/timing/cache/rate/repetition conformance tests.
Receipt storage binds the opaque token to the original authenticated principal,
audience, and grant family. Every lookup requires fresh authentication and current
receipt-access authority; possession of the token is never authority. Shared,
revoked, deleted, nonexistent, and unauthorized tokens take the same response and
budget path, with revocation/deletion fenced before lookup linearization.

Deletion cannot recall prior human/model consumption, irreversible effects, or
uncontrolled copies. A content-minimal inventory and receipt state these residuals
without payload. `COMPLETE` requires all controlled-store acknowledgements.

<!-- Copied payload ends above; see MIGRATION-MANIFEST.md. -->
