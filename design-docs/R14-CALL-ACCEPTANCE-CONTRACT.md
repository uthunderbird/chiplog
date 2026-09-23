# R14 call initialization and acceptance boundary

Status: contracts plus pure acceptance/cancellation preparation and isolated IPC.
Runtime publication, cancellation ordering and dispatch evidence remain HOLD. This document does not replace the full R14
requirements in R14-CONTRACT-FREEZE.md.

## Context and scope

The current R13 loop seals `ToolOutcome(state=INITIALIZED)` inside a Run and
immediately terminalizes its proposal tools. It has no durable original-call
subject or acceptance-aware publication/cancellation. The new preparation producers
remain separate from that live loop. Recovery frontier
DTOs describe observations of these states; they do not produce them.

Prepare owner-local immutable contracts for complete response fan-out,
consequential acceptance and cancellation before acceptance. Agent-loop owns the
call lifecycle and execution intent. Effects owns the immutable external intent
and its later revisions. Composition authenticates observations, translates inert
references and publishes the complete cross-owner batch through the existing sole
writer. No DTO constructor grants authority. Existing proposal-only tools retain
their behavior until a separately verified canonical integration is implemented.

Sources: `agent_loop/contracts.py`, `recovery_contracts.py`,
`recovery_frontier_contracts.py`, `recovery_domain.py`, `domain.py`,
`effects/contracts.py::AcceptEffectCommand`, `platform/_owner_publication_contracts.py::CallEffectBatch`,
R14-CONTRACT-FREEZE.md, and project-architecture/NORMATIVE.md's atomic sealed-call
fan-out and durable acceptance requirements.

The R14 worktree started clean at `718f101`. R15/R16/R17 have separate reviewed
staged work; this change must not modify their files or indexes. The contract stage
introduced this document, public DTOs and shape tests. The following implementation
stage adds pure producers, subprocess routes and manifest v5, and switches the
canonical R14 factory to v5. Legacy v4 remains explicitly supported. Existing
recovery/Run DTOs remain unchanged to preserve the scheduler compiler inputs.

## Initial contract-stage plan and assumptions

1. Freeze original identity, acyclic record references and complete response fan-out.
2. Define exact initialized-predecessor acceptance and pre-accept cancellation
   requests, owner output records and preparation-only results.
3. Validate the public surface with consumer serialization/shape checks. Admit only
   the reviewed new source in the implementation catalog and rerun preflight.
4. Stop this skill before implementing owner transitions or runtime publication.

Assumptions: existing shared gate, independent decision journal and sole appender
remain the publication mechanism. Current source observations are authenticated
and re-enumerated by composition, not inferred from these data structures. All
sealed calls remain in the fan-out, including proposal and read-only classes.
Read-only calls bind the existing `ReadOnlyRetryLineage` in the original sealed
call input and therefore in its initialized record. Its original call ID, bounded
attempt budget and reducer/version cannot be allocated or reset at first retry.
Other classes carry typed NOT_APPLICABLE. This shape does not implement read-only
proof validation, attempts or execution.
The first consequential route supports exactly one immutable external intent,
possibly an inseparable bundle; unsupported independent-intent multiplicity is
rejected rather than truncated. The existing singular recovery observation and
CallEffectBatch must be explicitly revised before broadening that route.

## Identity and reference domains

An original call key contains tenant, original Run and Turn, selected captured
response subject and head, original ordinal, and the model's call label. Current
execution Run identity is a separate admission observation and never rekeys the
original call. The identity preimage is the canonical tagged key, including its
`chiplog.call.identity.v1` domain; the subject ID is `call:` plus its SHA-256.

All new lifecycle record bodies use `RecoveryDTO` tagged canonical JSON. A record
reference is the stable subject ID plus `Present(head, fingerprint)` of the exact
record body. Record bodies contain no digest of themselves. Wire version and kind
are part of each preimage. Cross-owner external intent references mean the exact
immutable intent, not the physical effects acceptance row.

Reference order is acyclic:

- Existing captured response precedes each initialized record. Initialization
  binds that capture, not the new response-seal record's head.
- The response-seal record binds the complete ordered initialized references.
  The response seal and all initialized records are one atomic publication.
  A response with zero calls has an empty initialized manifest and a seal-only
  lifecycle batch; only the owner can validate that emptiness against the response.
- Acceptance names a stable execution-intent ID and commits the complete acceptance
  binding: immutable external intent content, tool/schema/policy, five semantic
  components, initialized predecessor and current authority/Run/fence cut. Execution
  intent binds the exact accepted head and the same external intent. The semantic
  manifest is exactly accepted, execution, external intent, in that order.
- A cancellation terminal names a stable NOT_EXECUTED result ID. That result binds
  the terminal head and the identical initialized predecessor.
- Prepared envelopes bind all resulting record bytes. A separate physical batch
  manifest includes the effects-owned record and any registered Run companion;
  it is never substituted for the owner semantic manifest.

Record fingerprint is SHA-256 of its canonical typed body. Its `Present.head` is
`record:` plus that digest, and its `Present.fingerprint` is the digest itself.
Stable accepted/execution/terminal/result IDs are the respective prefixes
`accepted:`, `execution:`, `cancelled:`, `not-executed:` plus SHA-256 of the complete
original typed preparation request. The response-seal ID uses `response-seal:`
plus its fan-out request digest. These definitions contain no resulting record
head. Replay uses original request bytes; a new command/cut is not replay.

`source_request_fingerprint` is SHA-256 of the complete typed request's canonical
bytes. `proposal_fingerprint` is SHA-256 of a canonical `RecoveryDTO`-ordered JSON
object with `kind` equal to the prepared result kind and the other prepared result
fields unchanged, excluding only `proposal_fingerprint` itself. It commits the
whole owner proposal, not the cross-owner physical batch. Final composition must
separately verify physical membership, canonical bytes and total serialized size.

The external intent reference may be computed before publication; it grants no
published authority. The combined batch must contain the effects-owned acceptance
record whose snapshot contains precisely that immutable intent. The seal-to-call
check must independently match all keys, ordinals and initialized record bodies to
the same selected captured response and its complete ordered calls.

## Authority and state boundaries

Both consequential acceptance and cancellation consume the same original
initialized head. Their current observation identifies the ACTIVE execution Run,
complete selected response/call inventory, exact tool/schema/policy, authority and
applicability sources, physical tenant cut and actual `RunExecutionFence`.
Registered operation semantics decide which sources are required; missing sources
are HOLD, not empty permission. Owner preparation cannot win the CAS.

Acceptance output is not a terminal result and does not authorize provider I/O.
Cancellation-before-accept output is terminal plus typed NOT_EXECUTED. An already
accepted call cannot be rewritten into that branch. Later Run continuation,
terminalization and recovery readers must join the authoritative lifecycle streams
before integration can be released; existing embedded ToolOutcome is insufficient.

The cut carries both a `CallInventorySnapshot` of observed predecessor state and
its exact content reference. Inventory ordering is lexical by original call ID;
response fan-out ordering remains original response ordinal. Duplicate original
identities are invalid. The inventory body binds tenant and tenant commit sequence;
its reference uses the same `record:` plus canonical-body SHA-256 convention and
subject `call-inventory:` plus tenant identity. An empty predecessor inventory is
valid. No newly prepared output belongs in it, avoiding self-reference.

For acceptance/cancellation, the owner must match the original call, initialized
record and reference, require the observed existing `AcceptanceBranch` to be
INITIALIZED with that same initialized head, and require terminal ABSENT. This
uses existing recovery observation types without defining another recovery reducer.
Hash equality validates supplied bytes only. Composition still independently proves
complete physical history, currentness and the winning CAS; a supplied empty
inventory never establishes absence. Full original preparation request bytes must
be retained in the selected owner command, not replaced by its fingerprint.

## Guarantee and evidence map

All durable behavioral rows below remain HOLD. Public shape tests prove only that
the contract can carry the required information and reject malformed wire shapes.
Pure producer tests additionally check consistency of supplied observations; real
IPC tests prove transport and manifest admission, not authoritative publication.

| Claim | Owned decision/data | Independent observable | Forbidden substitute | Boundary fixture | Planned evidence |
|---|---|---|---|---|---|
| Complete fan-out | selected response and every ordered initialization | journal selection plus exact SQL membership | caller subset or count alone | omit/add/reorder/duplicate; N/N+1 bytes and calls | canonical fan-out histories |
| Original identity | domain-tagged original key | same original subject after restart/successor | bare model label or current Run rekey | same label across responses/Runs; physical/logical mismatch | identity and recovery histories |
| Exactly one acceptance branch | initialized predecessor and branch CAS | one complete winning journal/SQL batch | prepared result or boolean accepted flag | both cancellation/acceptance serial orders; lost reply | canonical acceptance races |
| Complete consequential intent | accepted/execution/external-intent manifest | matching owner bytes in one physical batch | omitted effect or a synthetic owner record | missing/substituted/reordered companion; changed intent | CallEffectBatch histories |
| Current authority | registered source enumeration and actual fence | source mutation blocks writer selection | constructed DTO, cached permission, model text | stale Run/session/lease/authority after IPC | writer-boundary races |
| Honest cancellation | terminal plus NOT_EXECUTED tied to initialized head | no accepted/execution/effect records or dispatch | cancellation after acceptance described as never executed | acceptance wins then late cancel | cancellation histories |
| Exact replay | independently selected original batch | identical bytes, no new preparation or effect | regenerated current request or renewed old authority | expiry/restart/lost materialization acknowledgment | recovery/replay histories |

Mutation families: omission, addition/unknown values, substitution/alias,
duplicate/reorder, stale/race, N/N+1 bounds, and logical/physical identity mismatch
all apply. No family is dismissed as N/A. Fan-out bounds include complete order,
referential closure and serialized physical batch size, not only call count.

## Remaining integration obligations

Pure consequential acceptance and pre-accept cancellation producers are implemented
and exposed through isolated runtime manifest v5 routes. Manifest v4 remains supported.
Their tests check supplied snapshot consistency, deterministic proposals, malformed
inputs and real subprocess transport; they do not establish publication authority.
Scheduler execution fences return UNSUPPORTED until their authority validation is wired.

Add actual durable fan-out to the canonical loop without converting proposal tools
into execution; authenticate
the consequential tool's mandate; implement cancellation competition and exact
cross-owner batching; teach all history/continuation/recovery readers the new
streams; prove the closed worker registry covers every new writer. No shape test,
source catalog update or preparation result satisfies those obligations. Read-only
retry, evidence/closure, successor and post-terminal work remain required R14 work.
