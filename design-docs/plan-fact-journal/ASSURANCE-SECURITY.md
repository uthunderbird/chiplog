# Plan / Fact / Journal — security assurance

> Status: proposed and not operationally adopted. Authority and provenance are defined in [README.md](README.md).

## Adoption-assurance annex A — platform security

The following proposed P0 interface is required for safe adoption but is orthogonal
to the Plan/Fact ontology.

### P0-11 — security boundary, authentication, audit, and trusted ingress

Server-side request admission derives actor and security domain only from an
unexpired `AuthenticationContext` bound to the canonical request digest, nonce,
session/credential head, intended service/audience, and channel where supported.
Context issuance is append-only, audited, and atomically signs/MACs a canonical
digest of every field with a currently authorized authenticator key. Admission
verifies issuer, issuer-authority, authentication-policy and signing-key heads,
signature/MAC, exact request bytes, mandatory channel-binding result, and all
credential/session/principal heads. Unsigned, mutable, imported, field-spliced,
unknown-issuer, or auth-strength-inflated contexts reject.
Caller-supplied actor/domain fields are never authority. Grant issue/revoke, deletion
override, duplicate-risk repeat, recovery promotion, policy/evaluator/approver/key
change, operational approval, bridge creation, and break-glass actions require recent
step-up authentication; validation and commit/dispatch CAS-check that its strength
and freshness remain valid.

Admission atomically inserts a unique replay guard on
`(domain, authenticated subject, audience, nonce)` and binds its request digest.
Nonce reuse with another digest is rejected. Identical reuse returns a prior result
only for an explicitly idempotent operation whose committed envelope digest matches;
otherwise it is rejected. A fresh-record or external-effect action cannot reserve a
second guard. Guard transition and command/envelope commit share one transaction;
aborted reservations have one explicit CAS retry path and never authorize work.

Bridge creation, amendment, revocation, and expiry append
`CrossDomainBridgeRevision` successors under exact bridge-head CAS and consume two
independent endpoint approvals: one issued inside the source domain and one inside
the target domain, each bound to direction, operations, objects/fields, purpose,
audience, quota, expiry, and counterparty. Activation/amendment is invisible until
both current approvals commit through one atomic or recoverable coordinator result.
Only the greatest committed `ACTIVE`
head contributes. Every cross-domain read, write, disclosure, queue delivery, or
effect CAS-checks the exact bridge revision, both endpoint approval heads, and every referenced authority-family
head at its own visibility/dispatch linearization point. If revocation, expiry, scope
change, or authority revocation commits first, the protected operation rejects; if
the operation linearizes first, its bounded result remains auditable and is not
retroactively reclassified.

A replay guard remains authoritative until the bound context is expired beyond the
profile's maximum clock skew, every admitted operation is terminal beyond the maximum
in-flight lifetime, and a verified audit checkpoint covers the guard/result. Only
then may one atomic compaction remove or cryptoshred its request/result payload and
guard row. Replay after that point still fails because the context, session, or
credential is no longer admissible. The profile bounds context lifetime, skew, and
in-flight lifetime; if terminality/checkpoint cannot be proved, compaction is held.
Thus live replay protection is complete and retained guards are time/concurrency-
bounded rather than permanent.

Credential/session/principal families are append-only, bounded-lived, and advance
monotone revocation epochs under exact family-head CAS. Only the greatest committed
effective `CredentialRevision`, `SessionRevision`, and `PrincipalSecurityRevision`
head contributes. Those three heads must resolve to the same authenticated principal
and security domain named by the context. Admission, command commit, dispatch,
provider ingress, evidence sealing, approval, and recovery promotion recheck the
exact current credential, session, and principal-security family heads. `REVOKED`, `EXPIRED`, or
`COMPROMISED` fences queued work, sessions, tokens, evaluator signatures, and
unverified callbacks; dependent secrets rotate and unverifiable input quarantines.
Only an effect already past its recorded dispatch linearization point survives a
later revocation. Emergency recovery is scope/time-bounded and dual-controlled.

Every accepted or high-impact denied security action appends a domain-separated hash-
chained `SecurityAuditEntry` containing only an opaque envelope ID, randomized
commitment, coarse non-semantic event class/time bucket, and signature metadata.
Canonical semantic command/event bytes, authentication, authorization/policy heads,
result data, operator detail, and the actionable protected-linearization ID exist
only in the scope-keyed encrypted sidecar. The durable obligation uses a fresh opaque
randomized linearization token that cannot join back to the action after shredding.
Checkpoint and anchor advancement requires a threshold under a versioned witness
policy of administratively and key-custody-diverse nonrollback receipts with monotone
witness sequences and predecessor binding. Empty/under-quorum receipt sets are
invalid. Authoritative current-head discovery queries those witnesses rather than
local backup state; disagreement, equivocation, or unavailability marks the domain
`COMPROMISED` and blocks promotion/pruning. Restore/promotion requires that witnessed
current checkpoint and continuity proof. Gaps, forks, truncation,
rollback, signature/key-custody failure, or unverifiable rotation marks the domain
compromised and blocks promotion. Redaction follows the deletion field matrix while
preserving non-reconstructive continuity commitments. This is tamper evidence, not a
claim of legal nonrepudiation.

Audit completeness is coupled to every protected linearization point. If the action
and audit ledger share one transaction, its `SecurityAuditEntry` commits atomically.
Otherwise the same transaction creates a uniquely keyed durable
`SecurityAuditObligation`; the obligation is the sole visibility/dispatch authority,
and the action, provider outbox lease, bridge change, deletion override, promotion,
or high-impact denial remains non-visible/non-dispatchable until an idempotent relay
records the exact chained entry and atomically marks the obligation `RECORDED`.
Crashes before either commit leave neither; crashes after obligation commit recover
the same entry from its stored opaque envelope ID, randomized commitment, and
idempotency key. Missing/unavailable audit storage yields
`HOLD`, never unaudited success. Obligation/entry mismatch, duplication, or skipped
sequence marks the domain compromised.

Repeated low-level denials under abuse are never silently dropped: each contributes
exactly once to a fixed-duration, fixed-class `SecurityDenialBucket` with a keyed
request-digest accumulator, authenticated count, previous-bucket hash, overflow
state, and signature. Bucket classes/cardinality are bounded per domain; overflow
closes the bucket, trips admission/circuit breaking, and opens one successor bucket
or a coarser predeclared overflow class. High-impact, first-instance, class-change,
and operator denials remain exact entries. Checkpoints commit both exact entries and
bucket roots, preserving integrity and totals without unbounded per-request rows.

Authenticated application ingress uses one serializable admission transaction to
CAS the domain/class/window state, allocate monotone `ingress_seq`, create a uniquely
keyed bounded `SecurityDenialLease`, and increment `in_flight_count`. No sequence
exists without a durable lease containing the keyed request digest. Bucket updates
CAS the exact family head, cover one contiguous non-overlapping sequence range,
atomically update count/accumulator, mark covered leases `ACCOUNTED`, and decrement
the in-flight count; retrying an identical range returns the existing revision, while
gaps/overlaps are rejected. Expired reserved leases are deterministically accounted
from their durable digest before reuse/closure. Policy fixes a
maximum events per bucket and maximum successor buckets per time window. At the
limit, one admission-state CAS changes `OPEN→FENCING`, records
`fence_seq=next_ingress_seq-1`, and refuses new leases. The final bucket becomes
`SATURATED` only after every reserved token through the fence is accounted and
`in_flight_count=0`; then state becomes `CLOSED` until the next window. Thus
concurrent pre-fence actions drain and no post-fence application actions exist to
count. Earlier network-level drops are outside the authenticated action ledger and
use separately bounded DDoS telemetry. Lease cardinality is bounded by the declared
concurrency limit; overflow creates exactly one successor under head CAS.

Ordinary clock rollover uses the same fence protocol. At the boundary, the old state
head records its final `fence_seq` and stops old-window allocation. A next-window
state may open only after that fence is durable, cannot absorb old leases, and only
while the profile's maximum simultaneous `FENCING` windows is not exceeded. Every
lease lifetime is bounded by the same profile and expired leases are deterministically
accounted; therefore each old window drains, signs/checkpoints its final bucket root,
and becomes immutable `CLOSED` within the declared bound. If the fencing-window cap
or drain SLO is reached, domain/class admission closes until recovery. Retained old
states/leases are bounded by fencing-window cap × concurrency limit.

After a signed bucket root is included in a verified audit checkpoint, one crash-safe
compaction transaction creates a signed `SecurityDenialRangeCommitment` for its
contiguous accounted sequence range, advances the admission state's accounted high-
water mark, and removes or cryptographically shreds only `ACCOUNTED` lease rows and
their request digests. `RESERVED` leases are never compacted. Retry/replay at or below
the high-water mark resolves against a signed denial receipt token rather than
recreating a lease. Its signature covers domain, class, window, sequence, keyed
request digest, and bucket family; verification also requires that the committed
range contains the sequence and the checkpoint covers the signing-key version.
Missing, forged, or mismatched tokens are not duplicate proof and enter as new
denials when admission is open. A crash before atomic compaction leaves leases; a
crash after it leaves the commitment—never neither.

The security retention policy sets a maximum denial-replay horizon and a fixed count
of recent checkpoint epochs. Closed-window
range commitments within it are bounded by fixed bucket/successor limits. Older
commitments are folded into one signed checkpoint-epoch summary root, then detailed
ranges and keyed digests are pruned or cryptoshredded under the deletion field
matrix. When the epoch count reaches its bound, older summary roots fold recursively
into one signed cumulative anchor `(first_epoch, last_epoch, aggregate_count,
ordered_root)`; the prior detailed summaries are pruned after independent checkpoint
receipts confirm the anchor. Only that anchor plus the fixed recent epochs remain in
controlled storage. The summary preserves continuity and aggregate counts, not per-request replay
claims. An older token receives the same versioned external denial class as other
invalid tokens and, when
admission is open, counts as a new action. Retained rows are therefore bounded by
classes × configured windows plus checkpoint epochs, not elapsed windows or volume.

Anchor folding is one domain-serialized transaction: it CAS-targets the exact current
anchor and exact contiguous summary-head/frontier. The successor is cumulative: it
inherits `first_epoch=prior.first_epoch`, sets `last_epoch` to the last new summary,
and computes `aggregate_count=prior.aggregate_count + Σ(new summary counts)` using a
declared bounded unsigned representation; pre-check overflow yields `HOLD` and a
versioned wider-format migration, never wraparound. Its domain-separated
`ordered_epoch_root` commits the complete signed prior cumulative anchor fields/root
followed by every exact new summary in increasing epoch order, including epoch range,
count, and root. Construction is non-circular: first canonicalize the unsigned
successor body (all cumulative fields, no certificate digest/signature) and hash it
as `chiplog-audit-anchor-body-v1`; auditors certify that body digest; hash the
canonical certificate as `chiplog-audit-fold-cert-v1`; then sign the final anchor as
`chiplog-audit-anchor-final-v1 || anchor_body_digest || certificate_digest`. The body
does not contain the certificate digest, and the certificate does not reference the
final-anchor digest. The transaction then atomically advances
`SecurityDomain.current_audit_epoch_anchor_id`. Its idempotency key is the exact prior
anchor plus epoch range; overlaps, gaps, forks, and duplicate successors reject or
return the same result. Detailed epochs and the prior anchor are pruned only after
independent receipts attest that exact signed cumulative successor. A current-anchor-
only verifier validates the retained fold-transition certificate rather than merely
trusting the successor assertion. Before pruning, each independent auditor obtains
the exact prior anchor and contiguous input summaries, recomputes range, additive
count, and ordered root, and signs a semantic attestation over the prior digest,
input frontier/digest, recomputed values, and output anchor body digest. The certificate
must satisfy the versioned quorum threshold and administrative/key-custody diversity;
ordinary application/database operators cannot form it. The output anchor commits
the certificate digest. Its self-contained, bound `AuditTrustEvidenceBundle` retains the
exact quorum-policy snapshot/digest, historical auditor-authority head inclusion and
continuity proofs, signing-key rotation/revocation histories, signed administrative-
domain and custody attributes, validity-at-attestation proofs, and the prior
published-checkpoint inclusion proof. Canonical verification rejects missing fields,
invalid chains, duplicate auditors, or identities that fail policy independence.
Restore verifies the bundle, signatures, authority/key status at attestation time,
quorum/current trust policy, exact body binding, and chain to
the latest published checkpoint. Missing, stale, under-quorum, non-diverse, or
mismatched certificates mark the domain compromised and block promotion. Thus the
retained anchor plus certificate proves that a quorum satisfying the retained trust
policy attested to the bound transition. It does not let a later verifier recompute
transition semantics from pruned inputs; assurance depends on quorum validity,
independence, key custody, and correct pre-pruning verification. Here “evidence” is a
policy-bound trust record, not a cryptographic proof of computation. The current
anchor, certificate, and trust bundle are one
inseparable recovery unit and may be pruned only after a successor's valid bundle
attests and commits their exact digests. At most one fold may await
receipts; reaching the storage bound before acknowledgement holds further security-
relevant admission/promotion rather than creating another anchor.

Provider callbacks/receipts enter only through authenticated transport plus
signature/MAC verification over canonical bytes, with exact domain, provider account,
audience, effect, request fingerprint, schema, delivery ID, key version, and freshness
binding. Durable delivery-ID replay heads reject duplicates. Reordering/conflict,
unknown schema/key, stale delivery, payload mismatch, or account/domain mismatch
becomes `AMBIGUOUS`/quarantined, never confirmed. High-impact confirmation performs
an independently authenticated provider read when available. Caller-controlled
provider/resource IDs or callback targets are not trusted.

Security-sensitive roles are separated: artifact deployment, policy/predicate
authoring, evaluation, approval, key custody, production operation, and audit
administration. No principal may author→evaluate→approve→promote the same change.
Independent dual control governs security policy, keys, provider configuration,
bridges, break-glass, and audit infrastructure. Source, dependencies, policy/schema,
migration, build provenance, and runtime artifacts are content-addressed and signed;
promotion verifies the exact independently approved `SupplyChainAttestation` and
rejects rollback/untrusted provenance.

The attestation binds a canonical deployment/configuration manifest covering
infrastructure and identity policy, provider and audit trust roots/accounts, bridge
configuration, feature flags, runtime image/arguments, network policy, and versioned
secret/key references. Promotion compares this digest with independently measured
intended and deployed state and records the measurement in its audit. Any later
drift fences affected capabilities and triggers rollback or `HOLD`; sequence alone
never proves configuration content.

Supply-chain attestations form a predecessor-linked family under exact head CAS.
`release_seq` and `configuration_epoch` increase monotonically; the domain and
operational profile both bind the current attestation head. Promotion CAS-checks
those heads and rejects a lower/equal reused sequence, stale configuration epoch,
fork, or predecessor mismatch even when the artifact is otherwise validly signed.

Each domain has quotas and fair scheduling for request bodies/fan-out, graph and
disposition depth, recurrence/projection work, disclosure accounting, dedupe/audit
growth, webhook ingress, and held/ambiguous queues. Circuit breakers, cost budgets,
queue caps, admission control, and backpressure isolate noisy neighbors. Degrade mode
is read-only or held and never skips authentication, authorization, domain isolation,
audit, privacy, deletion fencing, or non-idempotent dispatch safety.

<!-- Copied payload ends above; see MIGRATION-MANIFEST.md. -->
