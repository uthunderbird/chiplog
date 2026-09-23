# R16 dispatch authority preparation

Status: inert observation boundary implemented. No SEND authority issuer, v2
initial-intent path, provider dispatch or v1 migration is implemented here.
The existing publication-only profile retains `send_capability=false`.

The consumer surface is `effects.dispatch_authority_contracts`:
`DispatchObservationQuery`, `DispatchAuthorityObservation`,
`DispatchAuthorityObservationPort`, the required `DispatchSourceInventory`, and
original selected history members. Constructing any of these authenticates nothing.
There is deliberately no grant, ticket, send method or caller-selected exemption.

## Why a new boundary is necessary

`domain.require_current_authority` compares the entire original AuthorityBinding.
`r16_effects_inputs.build_effect_request` retains the original invocation, storage
cut, read state, sessions and deadlines; `capture_sources` requires the original
planning predecessor. PlanEffect publication advances that predecessor itself.
Copying old bytes cannot prove currentness; silently replacing their values or
deadlines changes the accepted authority.

Newly adopted versions must separate immutable normative mandate and original
acquisition evidence from independently acquired current observations. V1 keeps
its original interpretation, five-part semantics, replay and expiry behavior.
This observation module does not yet define or enable the complete new intent.

## Concrete source limits

| Required group | Current evidence / missing authority |
| --- | --- |
| Trust | Actual trust invocation/reference and frozen observation exist in PlanEffect acquisition; a dispatch-specific issuer still needs current operation admission. |
| Planning | Planning trace supplies registered TRUST/PLANNING/REGISTRY/ADOPTION reads. It is not a complete current effects constraint, contradiction or applicability issuer. |
| Semantic registry | HermeticPlanEffectRegistry registers initial publication and explicitly denies send; it cannot be upgraded by recapture. |
| Original adoption | Exact display, ingress, owner request/result and selected PlanEffect are retained. They authorize neither a new recipient nor a new operation. |
| Runtime/fence | MaterializedEffectsCut and the actual supervisor/read ledger provide Run and session evidence; complete dispatch writer-cut reproduction remains required. |
| Endpoint | Original exact configured endpoint exists; configuration is not send permission. |
| Credential lifecycle | Offline account reference has no send grants; actual send-capable credential custody/lifecycle is unavailable in this profile. |
| Deployment entitlement | Initial publication provides no dispatch entitlement. Last-reversible-boundary admission must be independently wired. |
| Clock | Existing acquisition uses monotonic deadlines. Cross-epoch continuity and new adopted horizon semantics are not implemented; unknown continuity must remain unavailable. |
| Effects history | read_materialized_effects proves complete selected/materialized effects membership, expressly not current effect authorization. |
| Normative conflict generation | No independent generation source exists today; the current registry hashes the complete snapshot. A new registered verifier is required. |

Every group is required in the DTO, even when explicitly UNAVAILABLE. CAPTURED
means a claimed capture description, not a validated authorization. Missing or
unverifiable required sources must prevent a future private SendAuthority issuer
from granting a send. Shape validation cannot discharge this runtime obligation.

## History and denial semantics to implement

The proposed new conflict verifier consumes every selected effect record in order,
including terminal and historical records. It may exempt only the exact original
own publication and registered own pre-send successors whose original command,
output, predecessor, immutable mandate and interpreter verify at their original
selected cut. A separate complete current inventory includes those own records.
All foreign events remain significant even if a later event clears current blockers.
Current trust, planning, credential, deployment, clock and fence checks are separate
requirements; a matching history digest cannot replace them.

All ten currently declared EffectRecord kinds are enumerated in the observation
contract. Unknown kinds reject at the boundary. Omission, duplication, ordering,
identity, canonical bytes and actual selection authenticity remain verifier work.

HOLD/CANCEL/SUPERSEDE require their own current actor/operation, decision evidence,
latest attempt, observation lease, fence and registered interpreter. They must be
possible under separately valid denying authority even when original SEND authority
has expired or been revoked. They cannot extend that authority, clear ambiguity or
allocate a transmission. An unavailable interpreter means no invented owner revision.

## Claim-to-evidence map

| Claim | Owned data / independent observable | Forbidden substitute / fixture |
| --- | --- | --- |
| No source silently omitted | Required inventory fields; consumer removes each field and sees validation fail | Default empty inventory; public shape tests executed |
| Exact history bytes retained | Separate original command/result and selected cut; non-UTF8 byte roundtrip | Rebuilding old output using current compiler; public shape tests executed |
| No descriptor grants SEND | No grant/ticket fields or send port; injected fields reject | Caller-supplied success flag; shape tests executed, private issuance still absent |
| Complete authenticated history | Broker independently joins selected journal and full materialization at one cut | Matching DTO hashes or latest-only projection; future composition omission/addition/alias/duplicate/order fixtures HOLD |
| Normative generation preserved | New registered history fold and immutable adopted scope | Dropping resolved rival events; future own/rival transition fixtures HOLD |
| Current authorization | Actual source observations and writer recapture over exact operation/fence | Historical verdict treated as current; future revocation/race fixtures HOLD |
| Expiry and identity boundaries | Adopted maximum horizon plus fresh lease and registered clock epoch | Renewal by recapture or comparing monotonic timestamps across epochs; future equality/N+1/restart fixtures HOLD |

Validation command:

```sh
uv run pytest -q tests/capabilities/effects/test_dispatch_authority_contracts.py --tb=short
```

It passed 13 consumer shape tests. Targeted Ruff and mypy passed. Runtime admission
is intentionally unclaimed. The original staged PlanEffect patch stays unchanged;
this contract preparation is a separate unstaged change. Remaining work is the
complete versioned mandate/source verifier contract, then actual issuer, denying
dispositions, dispatch tickets, transport evidence, reconciliation and R14 recovery.
