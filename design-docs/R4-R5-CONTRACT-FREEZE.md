# R4/R5 parallel contract freeze

This decision freezes the only shared seam required to implement R4 and R5 from
the same predecessor. The executable exact sets are in
`chiplog.architecture.r4_r5_freeze`; a lane may implement them but may not rename,
add, remove, or reinterpret an entry. Changes require a new integration decision
before either lane continues.

## R5 operation decision

The first operation is `CREATE_INTENTION_LINE`, not `CREATE_ROOT`. It is the
smallest direct-principal creation admitted by the adopted Plan rules:
`VISION.md` makes `IntentionLine` the stable root and includes its initial
allocation in the canonical `PlanningCommand`/`PlanningCommittedResult` path
(lines 133–171), while `HEXAGONAL-CODE-LAYOUT.md` admits a direct authenticated
principal command when the principal-authored typed act binds its complete result
and current heads (Interpretation, proposal, and authorization).

The command requires no predecessor because it allocates a new identity. It binds
one authenticated direct-principal act and atomically allocates, in ordinal order,
one `IntentionLine` and its initial `IntentionLineRevision`. The initial revision
contains the principal-authored purpose, `activity=ACTIVE`, and
`personal_outcome=OPEN`. The planning owner constructs and stores a separate
`AuthorizationEvidence` from the admitted trust fact and one
`PlanningCommittedResult` for the exact command and manifest. Identical replay
returns that result; any changed field, authority/trust head, allocation, ordinal,
or canonical byte conflicts. No proposal is involved; payload identity fields are
not authority.

## R4 to R5 trust handoff

R4 exposes initial contour authentication separately from commit-time
`TrustReferenceRevalidation`. The latter binds the complete immutable reference,
exact `CREATE_INTENTION_LINE` operation, and allocation subject. Its successful
branch is the same immutable `TrustReference` frozen in code. Its other exhaustive branches are `DENIED`,
`STALE`, and `INDETERMINATE`; none may be converted to a planning context. The
reference binds tenant, principal, contour, credential/session/source heads,
current deployment trust and materialization heads, and a monotonic freshness
sequence. CLI uses the `CLI` contour; Telegram additionally requires a current
broker-issued `TransportOriginWitness`; durable evidence uses the `EVIDENCE`
contour and its authenticated source head.

R5 owns `PlanningTrustReference`, `InvocationContext`, and the outbound
`TrustRevalidationPort`; they contain only R1 and inert scalar values and never
import an R4 type. R6's bridge constructs them separately from command payload by
translating the successful R4 reference and adding only the planning permission
scope. Inside the committing transaction the R5 port sends the exact reference,
operation, and subject through that bridge; it translates to R4
`TrustReferenceRevalidation` and maps the result to R5's closed decision. Mismatch,
revocation, expiry, stale head,
unavailable state, or ambiguous observation returns a typed no-write outcome.
R4 owns authentication facts, credentials, sessions, witnesses, tenant/principal
registries, deployment bindings, and trust/materialization heads. R5 alone creates
and stores `AuthorizationEvidence`, `PlanningRevision`, and
`PlanningCommittedResult`.

The R6 contract fixture must substitute, one at a time, tenant, principal,
contour, credential/session/source head, trust/materialization head, freshness,
permission scope, peer credential, and payload-carried context; every substitution
must produce no authoritative write. That fixture belongs to R6 and is not
evidence that either isolated lane is already integrated.

## Ownership and convergence barrier

R4 owns `chiplog.capabilities.deployment_trust` plus the mechanical driven
implementations in `chiplog.adapters.driven.deployment_trust`: an independently
durable `TenantDecisionJournalPort` provider outside the SQLite backup domain and
a `TrustMaterializationPort` provider over the R3 substrate. The capability never
imports either implementation, and journal `DECIDED_COMMIT` precedes idempotent
SQLite materialization. R5 owns
`chiplog.capabilities.planning` and `chiplog.capabilities.projections`. Neither
lane imports the other, changes `chiplog.platform`, or edits this freeze. Both use
only frozen R1 values at their public boundary. The projection is a separately
owned derivative sink and cannot construct or authorize a planning command.

The complete authoritative variant list is frozen as stable record types in
`R4_R5_RECORDS`; variants share one owner-local schema per capability without
sharing their durable discriminator. Integration registers the exact packages, capabilities, exports and signature
digests, record type/schema IDs, owner commit boundaries, surfaces, and derivative
sink frozen in code. Before R4/R5 convergence is accepted, mutation tests remove
or alter one entry in every frozen family and observe rejection; the full R0–R5
stage-0 suite then runs. Until that barrier passes, neither lane is production or
evaluation evidence.

All R4/R5 inbound and outbound interfaces are `typing.Protocol` contracts.
Requests, decisions, references, commands, records, and results are immutable
value types; concrete adapters and test doubles satisfy ports structurally and
are never imported by a capability.
