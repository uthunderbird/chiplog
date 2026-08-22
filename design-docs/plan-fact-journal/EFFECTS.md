# Plan / Fact / Journal — external effects

> Status: proposed. Authority and provenance are defined in [README.md](README.md).

### P0-05 — invocation compatibility and receipt levels

Invocation stores its scheduled task and occurrence revisions and the exact current
task and occurrence revisions used at invocation. A versioned
`AuthorityCompatibilityPolicyRevision` treats
any change to time, target, audience, scope, reliance, status, mandate, or
precondition as incompatible. Attempt acceptance records the exact active policy
revision in both attempt and command audit; acceptance and dispatch CAS the policy
family head together with every protected head. Amendment/revocation advances that
family under issuer-authority and predecessor CAS. Compatibility validation and
`ATTEMPT_ACCEPTED` commit atomically. Stale, revoked, expired, incompatible, or missing-policy attempts
remain held.

The attempt's `current_occurrence_revision_id` is an immutable snapshot captured in
the atomic acceptance transaction, not a live pointer. Every occurrence transition
audit requires both typed `validated_against_occurrence_revision_id` and
`resulting_occurrence_revision_id`; an occurrence revision without those bindings is
invalid.

The levels are distinct: `ATTEMPT_ACCEPTED`, `TOOL_EXECUTION_CONFIRMED`, and
`REAL_WORLD_OUTCOME_OBSERVED`. None implies the next. Each journal claim remains
non-normative; a response requires a later validated command and revision.

Attempt and effect state changes append revisions under predecessor CAS and the
canonical command `commit_seq`; scalar current pointers are caches.

| Mode | Permitted attempt edges |
|---|---|
| Both | `HELD→ATTEMPT_ACCEPTED`; `TOOL_EXECUTION_CONFIRMED→REAL_WORLD_OUTCOME_OBSERVED`; any post-dispatch state may enter `FAILED` only with an exact failure receipt. |
| Idempotent | `ATTEMPT_ACCEPTED→DISPATCHING_IDEMPOTENT→TOOL_EXECUTION_CONFIRMED`; dispatch may enter `AMBIGUOUS`, from which retry is allowed only inside the verified provider contract/window. |
| Non-idempotent | `ATTEMPT_ACCEPTED→DISPATCHING_NONIDEMPOTENT→TOOL_EXECUTION_CONFIRMED`; dispatch may enter terminal-for-automation `AMBIGUOUS`. |

Effect edges are `CREATED→DISPATCHING→PROVIDER_ACCEPTED→PROVIDER_CONFIRMED`, with
`FAILED` or `AMBIGUOUS` post-dispatch branches. Compensation is a new effect, never a
state of the old effect. Any unlisted edge is rejected. For non-idempotent effects,
the paired dispatch transition is the irrevocable linearization point and forbids
automated resend or takeover on every successor branch.

`AMBIGUOUS` is terminal for dispatch automation, not for evidence. Only an
authenticated matching `ProviderIngressEnvelope` or authorized reconciliation read
may CAS effect `AMBIGUOUS→PROVIDER_ACCEPTED|PROVIDER_CONFIRMED|FAILED` and attempt
`AMBIGUOUS→TOOL_EXECUTION_CONFIRMED|FAILED`. Those resolution edges atomically bind
the receipt/reconciliation evidence and never authorize a provider call, resend, or
takeover.

Immediately before any provider call, a dispatch-authorization transaction
serializes against the current task/occurrence revisions, grant-family heads,
delegation/acceptance heads, and deletion epoch; revalidates every protected
dimension; and CAS-appends paired mode-specific `DISPATCHING_*` attempt / `DISPATCHING`
effect revisions plus a durable outbox lease. That commit is the dispatch authority
linearization point. If cancellation, replanning, revocation, withdrawal, or deletion
wins first, dispatch is held. If dispatch wins first, later changes cannot revoke the
already-authorized effect and the race remains visible in UI/audit.

The lease is the revisioned `ExternalDispatchOutbox`, unique by domain and
`effect_id`. Each claim CASes the exact head and allocates a strictly higher fencing
token. A worker must CAS `CLAIMED→SEND_STARTED` with its current unexpired token
immediately before the provider call; a stale token cannot start sending. A crash or
expiry in `AUTHORIZED` or `CLAIMED` may be reclaimed with a higher token because no
provider call was authorized. Provider callbacks and receipts use one idempotent
`ProviderIngressEnvelope`, unique by domain and authenticated provider
receipt/effect binding. One transaction CAS-checks the exact outbox, effect, and
attempt heads and atomically commits the receipt, `ACKNOWLEDGED` outbox successor,
corresponding effect/attempt successors, and any claim append. If the selected stores
cannot share a transaction, the durable envelope is sole authority and every record
is an idempotent derivative; none of the transition is projection-visible until the
complete envelope result exists. Retry returns the identical terminal result, never
a second semantic transition.

### P0-06 — external-effect safety

Every effect has immutable `effect_id`. A provider is treated as idempotent only
within a verified, versioned contract naming the key scope, payload-mismatch
behavior, and guaranteed retention window. Its key binds provider, resource, effect,
and an immutable canonical request fingerprint covering operation, target, payload,
authority, and attempt. A retry must reuse the exact key and match that fingerprint
byte-for-byte. After provider-key expiry, or when dedupe state is unknown, automatic
retry is held; safety is claimed only inside the verified contract and retention
window.

For non-idempotent providers, durable `DISPATCHING_NONIDEMPOTENT` is the irrevocable
authorization linearization point. After ambiguity there is no takeover, automated
resend, or reuse by another worker. Manual repetition without proof of nonoccurrence
is a new separately authorized effect whose command acknowledges duplicate risk.
Compensation is also a new authorized effect, never historical mutation.
For the outbox specifically, non-idempotent `SEND_STARTED` is never re-leased: lease
expiry, worker loss, or missing acknowledgement appends terminal-for-automation
`AMBIGUOUS`. This deliberately accepts a possible authorized-but-unsent effect when
a crash follows `SEND_STARTED` but precedes the socket write, preserving the stronger
at-most-one-call boundary. Idempotent work may be reclaimed after `SEND_STARTED`
only with the identical key/fingerprint and inside the verified provider dedupe
window; otherwise it also holds as `AMBIGUOUS`.

<!-- Copied payload ends above; see MIGRATION-MANIFEST.md. -->
