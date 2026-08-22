# Hexagonal code layout

## Status and authority

This document records the physical Python package layout selected for Chiplog's modular monolith. It realizes the implementation boundaries described in [`../grill/project-architecture/document.md`](../grill/project-architecture/document.md) and is subordinate to [`VISION.md`](VISION.md). It defines code ownership and dependency direction; it does not create or change product semantics.

For implementation ownership, this layout refines and supersedes the implementation draft: `planning` owns the authority, acceptance, and delegation record families. `VISION.md` authorizes this layout to select the trust-boundary and atomic-publication constraints that realize those semantics.

The target layout is evolutionary. A directory or future boundary is created only when it has its own lifecycle or invariants, a real consumer, and a negative fixture enforcing the boundary. The complete target map is not a request to create empty package skeletons.

## Organizing principle

The production tree exposes four architectural roles:

```text
capabilities -> application ports <- adapters
                            ^
                       composition

domain_primitives supplies shared policy-free values to capability domains.
platform supplies policy-free process mechanisms beneath adapters.
```

A capability is the unit of semantic ownership and encapsulation. It need not own Plan or human authority: a durable workflow or state machine such as `agent_loop`, `interpretation`, `effects`, or `projections` may also justify a capability boundary.

Driving adapters invoke inbound application ports. Driven adapters implement outbound application ports. Domain code has no dependency on application code, adapters, composition, Dishka, SQLite, provider SDKs, or channel SDKs.

`planning` is the sole owner of normative planning state. Its domain includes the closed planning revision families for authority grants, acceptance, delegation, commitments, arrangements, and other Plan state adopted by `VISION.md`. The `policy_authority` capability evaluates exact planning heads and returns a non-authoritative `PolicyEvaluation`; it is not a second semantic owner of those families.

The capability is the semantic owner of its record family. Within it, the domain factory or aggregate is the sole constructor and validator of a new authoritative record; an authorized use case invokes that domain operation and coordinates ports. A publication coordinator may check cross-record commit preconditions and pass immutable owner-produced records to storage. An adapter performs only mechanical validation, translation, and persistence. Neither coordinator nor adapter may create, classify, repair, or replace domain records. These roles remain distinct even when one SQLite transaction executes them all.

## Authority and construction matrix

| Record family | Human or external authority | Semantic and lifecycle owner | Forbidden constructors |
|---|---|---|---|
| `PlanningRevision`, including grants, acceptance, and delegation | authenticated principal or an exact bounded mandate | `planning` | journal, effects, policy evaluator, projections, adapters |
| `AuthorizationEvidence` | an admitted authority fact bound to exact planning heads, subject, tenant, and scope | `planning`; immutable evidence is issued by its domain and superseded or revoked only by a successor planning revision | policy evaluator, ingress, workflows, effects, journal, adapters |
| Owner-authored `FactClaim` and `ClaimDisposition` | authenticated principal's assertion or direct confirmation | `evidence_journal` | provider, model, planning, effects, projections, adapters |
| Candidate evidence and provenance links | attributed authenticated source under admitted evidence policy | `evidence_journal` | provider adapter, effects reducer, projections |
| Provider receipt and effect outcome families | authenticated provider/reconciliation evidence under the exact attempt | `effects` | planning, journal, projections, provider adapter acting alone |
| `AtomicPublicationEnvelope` and idempotency mapping | authority required by every enclosed owner result | the named workflow, for non-authoritative publication metadata only | storage and transport adapters acting alone |
| Projection rows and narrative views | none; they are deterministic derivatives | `projections` rebuilders | every command/effect path treating a projection as authority |

The matrix assigns semantic construction, not physical table ownership. All rows may share the same tenant log and physical appender without sharing semantic authority.

In this matrix, the named capability is the semantic and lifecycle owner. Construction and validation of each authoritative record remain exclusively inside that capability's domain factory or aggregate; its use cases are authorized invokers and coordinators, not rival constructors.

## Trust boundaries

Every authority-bearing inbound call receives a trusted `InvocationContext` separately from its untrusted payload. Only an authentication boundary constructs it. The context binds the deployment-selected tenant, authenticated principal or explicitly issued service identity, channel/session evidence, security scope, and bounded mandate when present. Payload fields that resemble tenant, principal, mandate, or `AuthorizationEvidenceRef` are never effective authority. Internal calls and scheduler activations use explicitly issued service identities and bounded mandates, not ambient process authority.

For v1, `TenantId` is the storage and security-domain partition. `PermissionScope` expresses what an identity is authorized to do within that tenant. There is no separate `SecurityDomainId`.

`AuthorizationEvidence` is an immutable planning-owned authoritative stored record in planning's schema registry. Only the planning domain issues it from an admitted authority fact; amendment, revocation, or supersession is a new planning revision, and reference validity is evaluated against the exact bound heads inside the consuming transaction. An authority-bearing transaction receives only its trusted tenant-scoped reference; payload-carried evidence or an unscoped opaque ID is never effective authority. Owner-produced results have a closed construction boundary and carry an owner tag and schema tag that storage verifies mechanically. These tags and module boundaries provide enforceable in-process provenance checks, not a cryptographic guarantee of in-process provenance.

Repository, query, idempotency, projection, and publication ports are tenant-scoped. Every key, reference, owner result, envelope, authorization-evidence reference, receipt, provenance link, and projection row must match the trusted context and deployment tenant. Unscoped lookup by opaque ID is forbidden; storage keys and references carry tenant scope, and a transaction verifies every transitively referenced record through a typed reference graph. Unknown reference-bearing schemas, missing records, cycles that prevent complete validation, and any cross-tenant edge fail closed. Cross-tenant tests traverse indirect and cyclic references, not only envelope fields.

Model, provider, channel, scheduler, and eval inputs remain untrusted observations or proposals after parsing. They cannot supply an effective context, `AuthorizationEvidenceRef`, or planning command. An authority-bearing transition always follows `untrusted payload -> validated observation/proposal -> separately authorized command` under a trusted context. External credential or provider material is `ExternalEvidence`; it becomes an `AuthorizationEvidenceRef` only through its separately admitted verification path.

Credentials belong to composition-provided secret/configuration providers. Capabilities receive narrow tenant-bound connector handles, never raw secrets. Secret values are forbidden in domain records, fingerprints, envelopes, receipts, projections, model context, and logs; ingress adapters redact them before constructing application DTOs. Evaluation composition cannot use production credentials or a production tenant store without an explicit separately authorized operating mode. End-to-end canary tests cover success, error, retry, exception, structured-log, fingerprint, and model-context sinks.

## Target production tree

```text
src/chiplog/
├── __init__.py; __main__.py
├── domain_primitives/{identity.py,tenant.py,principal.py,canonical.py}
├── capabilities/
│   ├── planning/{domain/,application/{inbound/,outbound/,use_cases/}}
│   ├── policy_authority/application/{inbound/,outbound/,evaluators/}
│   ├── evidence_journal/{domain/,application/{inbound/,outbound/,use_cases/}}
│   ├── effects/{domain/,application/{inbound/,outbound/,use_cases/}}
│   └── interpretation/; agent_loop/; projections/
├── workflows/{mark_done/,planning_effect/,provider_evidence/,deletion/}
├── adapters/
│   ├── driving/{cli/,telegram/,scheduler/,evals/}
│   ├── driven/{sqlite/,model/,calendar/,delivery/}
│   └── bridges/
├── platform/{persistence/sqlite/,concurrency/,clock.py,ids.py}
└── composition/{production.py,evaluation.py,scopes.py}
```

The target names follow the accepted logical module names. Physical packages appear only when their stage has real behavior. In particular, the tree does not imply that all capability packages exist in Stage 0.

`domain_primitives` is not a shared domain model. It contains only opaque typed identities, tenant/principal/permission-scope values, and policy-free canonical value representations. Planning revisions, fact claims, effect records, authority statuses, repositories, events, and commands are forbidden there. `platform/ids.py` supplies generators and technical codec implementations for these value contracts; it does not define rival identity types.

`workflows/` is application-only coordination for multi-owner flows: Mark done, planning-to-effect, provider-to-evidence, and deletion. Each workflow defines only its own inbound coordination contract, one consumer-owned outbound port per participating capability, and one atomic-publication port. A bridge implements each participant port by translating to that capability's public inbound contract; the SQLite driven adapter implements the publication port. The workflow imports no capability contract or foreign domain type and owns no authoritative record, domain invariant, or product policy; every owner result still comes from its capability's authorized use case and domain.

## Durable type and byte identity

Every persisted record has a stable namespaced `record_type_id` and `schema_id` independent of its Python module, class, or import path. The owning capability owns its schema/decoder registry; composition aggregates registries and rejects collisions or missing decoders at startup. Moving a module or splitting it into a package never changes a persisted discriminator.

Record schema, integration DTO, codec, and canonicalization versions are distinct bindings. A stored fingerprint is always verified with the exact versions that produced its canonical bytes; new defaults, field order, normalization, or codecs never recanonicalize historical bytes before verification. Version-specific decoders preserve unknown/original bytes until the separately selected compatibility and migration policy permits a semantic transformation.

### Stage-0 version and rollback boundary

Stage 0 runs one process and one application/store version. Startup accepts only an empty store it can initialize or an exact physical-store version match with all encountered record decoders; every other combination fails before admission. Mixed-version operation and code-only rollback after a newer binary has written new schema identities are not promised. Rollback requires a compatible binary plus a separately verified compatible store/snapshot.

Migration, when its later trigger is adopted, verifies original bytes with their producing schema, codec, and canonicalization versions before decoding or transforming them. It never rewrites authoritative history in place; a transformed representation has a separately identified version or successor record. Stage-0 `schema.sql` is initialization-only. Migration artifacts and rollout order belong to the future migration contract.

Before the first release, public application ports are unversioned. A breaking port change requires synchronous updates to every in-repository caller and bridge. Persisted schema and record identity remain separate from application-port compatibility. External API compatibility and versioning are future work that must be selected before the first external compatibility promise; this document introduces no current application API versions.

## Canonical dependency rules

This numbered section is the canonical statement of required dependency and ownership invariants. They become enforced only with the corresponding static, composition, contract, or runtime test; an import fitness test alone does not prove a semantic or transactional invariant.

1. Domain modules import only their own domain and the allow-listed policy-free
   `domain_primitives` value contracts.
2. Application modules may import their own domain and their own application contracts.
3. Capabilities never import adapters, platform mechanisms, composition, Dishka, SQLite, or SDKs.
   Dynamic import, entry-point lookup, reflection-based dependency resolution, container access, and
   service-locator interfaces are likewise forbidden inside capabilities.
4. Cross-capability calls enter only through public inbound ports.
5. An outbound port is owned by the consuming capability and expresses only the dependency that
   its use case needs.
6. Adapters may depend on the ports they drive or implement, but adapters do not call other
   adapters.
7. Only bridge adapters may depend on application contracts from both connected application boundaries.
   A bridge implementation lives under `adapters/bridges`; composition only constructs and injects
   it. Every cross-boundary call, including a workflow participant call or a read-only call, connects
   a consumer-owned outbound port to the target capability's public inbound port through a bridge.
8. The directed graph whose nodes are capabilities and workflows must be acyclic. Architecture tests build
   that graph from imports and registered bridges and reject every strongly connected component
   containing more than one node. The bridge registry records consumer, provider,
   implementation, and edge kind; its entries must exactly equal the bridge bindings discovered in
   production/evaluation composition. Missing and orphaned entries both fail.
9. Only composition code constructs containers, opens scopes, and knows the complete object graph.
10. Platform contains no domain or application policy and exposes no generic authoritative write
   API.
11. Production and eval composition use one assembly manifest and the identical application loop;
    they may differ only in allow-listed adapters and tenant/credential handles.
12. Planning is the sole semantic owner of every `PlanningRevision` family, including authority,
    acceptance, and delegation; policy-authority evaluation is non-authoritative.
13. Effect receipts, candidate evidence, and personal fact claims have distinct owners and no
    provider path may promote evidence into a personal claim.
14. Every cross-capability atomic publication follows the normative protocol in “Atomic invariant
    islands”; it preserves each record family's sole semantic owner and uses a named typed
    integration contract rather than a generic event batch.
15. Every authority-bearing inbound call and storage operation follows “Trust boundaries”; typed
    caller-supplied identities never substitute for trusted invocation and tenant scope.

| Rules | Enforcement layer |
|---|---|
| 1–9 | static import graph, bridge registry, SCC check, and negative import fixtures |
| 10 | static platform-content boundary plus focused runtime tests |
| 11 | production/eval assembly identity test |
| 12–13 | domain/application tests plus forbidden-constructor and untrusted-promotion fixtures |
| 14 | adapter contracts, transaction/concurrency/replay tests, and failure injection |
| 15 | authentication, cross-tenant, confused-deputy, secret-leakage, and ingress-adversarial tests |

Each stage records which rows are implemented. An unimplemented row remains a required target invariant, not evidence that the current slice already enforces it.

## Port failure and lifecycle contract

Every public port defines the closed application-owned result and failure variants its consumer needs. Domain violations remain domain types; dependency unavailability, timeout, rejection, and ambiguous outcome live beside the consuming application port. Adapters translate SQLite, SDK, and transport exceptions before they cross that port. A mechanism-specific exception may remain as a redacted diagnostic cause, but capability behavior never branches on its type.

Semantic retry decisions and stable attempt identities belong to application/effects policy. An adapter may retry only when its port contract explicitly names a mechanically transparent retry; provider, model, delivery, or other consequential retries cannot be hidden inside an adapter. Timeout and ambiguous outcome return through the consuming port's typed result.

Cancellation propagates through ports as control flow, not as a retryable business failure. It may be delayed only by an explicitly bounded cleanup or commit-critical section, which must publish the owner-defined terminal/ambiguous result when an external effect may already have crossed its last reversible boundary. An adapter that creates a resource implements a narrow lifecycle contract; composition closes resources in reverse construction order and waits for child work on success, startup failure, error, timeout, and cancellation. Bridges have no lifecycle exemption.

## Composition startup and identity

Startup follows one fail-closed sequence: load an immutable validated configuration snapshot; validate schema/decoder, bridge, scope, store-version, and tenant bindings; acquire resources under a composition-owned async exit stack; start children; then cross the single readiness point that opens admission. Driving adapters and scheduler activation are unreachable before that point. A failure or cancellation at any earlier boundary keeps admission closed, joins started children, and runs every registered compensation.

Every resource-producing provider registers its close action atomically with each successful acquisition, including during partial construction. Every handle has one owning scope; dependants borrow it, do not close it, and cannot outlive the parent. Disposal orders `close admission -> cancel/join children -> close dependants -> close dependencies`. Failure-injection tests cover every acquire/start boundary, shared handles, double close, and attempted child-scope escape.

Production and evaluation use one shared graph builder and the same serializable assembly manifest. Each configuration field is classified as policy-affecting, adapter-specific, credential, or tenant-bound. The manifest fingerprint binds capabilities, ports/bindings, bridges, schema/decoder registries, scope/lifetime classes, application-loop identity, every canonically represented policy-affecting value, and the identities—not secret values—of credential and tenant handles. Profiles may differ only in an explicit allow-list of adapter implementations and tenant/credential handles. A policy-affecting difference requires a distinct typed operating mode; an unclassified field, conditional profile branch, or unlisted wiring difference fails manifest validation and branch-sensitive production/evaluation contract tests.

## Capability, adapter, and call anatomy

The rules above map to implementation roles as follows:

| Role | Responsibility and dependency direction |
|---|---|
| Capability | Semantic and lifecycle owner of its record families. |
| Domain factory or aggregate | Sole constructor and validator of authoritative records in those families; depends only as allowed by rule 1. |
| Inbound port | Public application API of a capability: commands, queries, results, and protocols reached by driving or bridge adapters (rules 2 and 4). |
| Outbound port | Consumer-owned description of the exact dependency a use case needs (rules 2 and 5). |
| Use case | Authorized invoker of domain construction and coordinator of domain behavior and outbound ports; contains application policy but no transport, database, provider, model, or dependency-injection mechanics (rules 2 and 3). |
| Adapter | Performs mechanical validation, translation, and persistence at a port boundary; driving adapters invoke inbound ports and driven adapters implement outbound ports (rule 6). |
| Bridge | The only module that may know both connected application boundaries' contracts; translates a consumer-owned port to a capability's public inbound port under rule 7. |
| Workflow | Application-only coordinator for a named multi-owner flow; imports only its own coordination, participant, result, and publication contracts and owns no authoritative records or policy. |
| Platform | Supplies policy-free process mechanisms beneath adapters, including the physical `EventAppender`, SQLite connection/transaction/schema mechanics, clocks, IDs, and concurrency (rule 10). |
| Composition | Constructs the object graph and supplies production or evaluation adapters to the identical application loop (rules 9 and 11). |

### Capability example

For example, the mature `planning` capability may have this shape:

```text
capabilities/planning/
├── domain/{commands.py,revisions.py,plan.py,authority.py,acceptance.py,
│          delegation.py,invariants.py,errors.py}
└── application/
    ├── inbound/{commands.py,queries.py}
    ├── outbound/{repository.py,authority.py,effect_publication.py}
    └── use_cases/{revise_plan.py,read_plan.py}
```

These subdirectories are not mandatory ceremony. A new capability may begin with one module per role and split into packages only when multiple files have distinct reasons to change.

### Policy-authority evaluation

`policy_authority` is an application-level validation boundary. It reads exact current planning heads through typed outbound ports, evaluates grants, acceptance, delegation, holds, conflicts, freshness, and state-head compatibility, and returns a decision bound to those exact heads. Its `PolicyEvaluation` grants no new authority and writes no planning state. Grant issue, amendment, revocation, acceptance, delegation, and hold transitions remain `PlanningCommand` operations owned by `planning`.

For example, persistence follows rules 5–6:

```text
capabilities/planning/application/outbound/repository.py
                                      ^
adapters/driven/sqlite/planning_repository.py
```

This permits one SQLite boundary to implement several domain-shaped ports without giving application code a generic event-store escape hatch. Cross-capability translation follows rules 4, 5, and 7:

```text
agent_loop outbound PlanningPort
                ^
agent_loop_to_planning bridge
                v
planning inbound PlanningCommands
```

## Platform boundary

`platform` is a narrow policy-free technical substrate. It owns:

- the platform-owned physical SQLite `EventAppender`, connection, transaction, and schema mechanics;
- clock and ID implementations;
- asyncio lifecycle and concurrency primitives.

It does not own repositories, provider mapping, domain DTOs, product policy, workflow orchestration, or cross-capability coordination. Those belong to capability ports, adapters, and use cases.

There is exactly one authoritative tenant log, one platform-owned physical `EventAppender`, and one composition root. Individual capabilities do not create physical appenders or units of work.

Composition owns the process/root task groups, admission lifecycle, and worker scopes. Adapters and bridges attach child work to the supplied scope and never create detached tasks; `platform/concurrency` contains only policy-free bounded primitives. Capability/application policy decides permitted fan-out and shielding, while composition tests prove no orphan work after success, startup failure, error, timeout, or cancellation.

The SQLite platform slice separates admission, accepted-command ownership, physical append/drain, and lifecycle control even when those roles initially share a module. Close linearizes against admission, rejects new work, and drains accepted commands according to the upstream runtime contract. It also owns policy-free connection and snapshot handles; capability read ports express consistency and frontier needs without exposing SQLite, and driven adapters join reads to a composition-owned read scope.

Scheduler execution follows `scheduler driving adapter -> agent-loop/scheduler inbound use case -> consumer-owned occurrence/lease ports -> SQLite driven adapter -> platform transaction primitives`. Composition owns worker tasks, capability application owns occurrence/lease/takeover policy, and the driven adapter implements CAS/fencing mechanics. The lease epoch is bound to the occurrence and stable effect-attempt identity and is revalidated immediately before the last reversible boundary. A consequential provider call uses that stable attempt identity as a provider idempotency key when the provider supports one. Without provider idempotency or an external fencing mechanism, an attempt that may have crossed—or may still cross—the last reversible boundary remains ambiguous and cannot be replaced automatically. Only provider-specific reconciliation evidence that rules out both completed and delayed delivery may classify it as safe to replace; otherwise resolution requires an explicitly authorized human decision. A stale worker may record an ambiguous outcome for reconciliation, never authorize a replacement attempt.

Backup restore uses a separate composition-owned offline administrative mode and a narrow platform SQLite recovery seam, never the application append API. It copies an authoritative store/snapshot without reconstructing domain records, keeps admission closed, and verifies tenant binding, available schema decoders, fingerprints, reference integrity, and complete publications before startup; projections are then rebuilt. Backup commands, retention, encryption/key custody, RPO/RTO, replication, and drills belong to the operations/recovery contract, not this layout document.

## Projection recovery and visibility

The replay unit is one validated complete publication manifest and its owner records; projections never observe a partial publication. Rebuild consumes those units in stable tenant-local commit order through an inclusive frontier. Reducer identity/version, input schema versions, tenant/log identity, frontier, and state digest bind every projection generation and checkpoint.

Rebuild writes a new generation through a projection-owned build-store port and leaves the currently published generation unchanged on crash, cancellation, or validation failure. Only a sealed generation whose digest and frontier match the authoritative log becomes visible through one atomic publish/CAS. Projection-table writes outside `begin_generation`, `apply`, `seal`, and `publish_if_current` operations are forbidden.

A projection checkpoint is a disposable accelerator, never recovery authority. It is accepted only when its log identity, tenant, inclusive frontier, reducer/schema versions, and digest verify; otherwise replay starts from genesis or another verified checkpoint. An authoritative-store backup snapshot is separately typed and contains the committed log, not projection authority.

Every query declares a `minimum_frontier` or an explicit bounded-stale mode. Results carry their served frontier plus generation/reducer identity. If the requested frontier is unavailable during rebuild, the port returns a typed stale/rebuilding/unavailable result rather than silently serving a weaker state.

## Deletion and data-governance seams

The semantic owner's domain factory or aggregate is the only constructor of its authorized deletion revision. The `deletion` workflow coordinates a named deletion transition through typed fence and dependency ports; platform SQLite implements only its policy-free conditional transaction, fence, watermark, and rebuildable reverse-dependency storage mechanics. This document does not select the unresolved payload-destruction, encryption, retention, or index representation.

Every content-bearing port and adapter declares its deletion dependency and negative-path behavior. Composition exact-set checks its managed sinks—projections, caches, indexes, model/session context, logs, exports, telemetry, queued work, pending effects, backups, and evaluation sinks—against their bindings; a missing or orphaned registration fails startup. Content emission or storage outside registered ports and adapters is forbidden by static checks and negative bypass fixtures. This proves coverage of composition-managed sinks, not discovery of arbitrary hidden I/O. Fence state and its frontier are mandatory inputs to ordinary reads, replay, projection generation, publication, and pending-action admission.

The deletion transition first closes admission for affected content and drains or cancels accepted work that has not crossed its last reversible boundary. Work that may have crossed that boundary is reconciled to an owner-produced terminal or ambiguous result and cannot emit another effect. Its linearization point then atomically commits the owner deletion revision, fence/frontier, publication manifest, and idempotency record under the same expected heads. A crash or conflict leaves either that complete state or no deletion publication; admission reopens only after the no-publication outcome, never between revision and fence installation.

Ordinary replay or `publish_if_current` fails closed when fence state is absent, incompatible, or behind the replay frontier. Deleted payload recovered from retained pages, WAL, snapshot, backup, or quarantine can enter only a separately authorized quarantine recovery path and never ordinary state. Backup restore, audit access, and deletion-recovery quarantine use distinct administrative composition modes and inbound ports. None may weaken or bypass the authoritative fence.

## Atomic invariant islands

The common event log is not a common application API. Application code must not receive a generic `append(events)` operation capable of publishing another capability's authoritative records.

**Every atomic publication must** accept immutable results already validated and constructed by their owning domain factories or aggregates, invoked by authorized use cases, through a named, typed integration contract. One transaction must publish the `AtomicPublicationEnvelope`, every owner-produced result, and one publication-level idempotency record. The envelope carries a complete map from each owner-result identity to its authorization subject, exact heads or evaluation, principal or service identity, mandate, `PermissionScope`, and tenant-scoped evidence references; a contract may share one tuple only when it explicitly proves that the tuple authorizes every mapped result. The idempotency record is uniquely scoped by tenant, operation kind, and idempotency key and binds the canonical request fingerprint, committed envelope/result identities, this per-result authorization map, and the authority that governs observation of the result. Reusing the same key and fingerprint returns the original committed result only after those bindings are revalidated; reusing the key with a different fingerprint is a conflict. An unauthorized caller receives only a typed denial and cannot learn whether a stored result exists.

Head validation and append form one conditional state transition and linearization point under the SQLite transaction mode and uniqueness constraints selected by the adapter. At that point the adapter compares the trusted invocation and deployment tenant against the envelope and every owner result, loads every immutable stored `AuthorizationEvidence` named by the per-result map, and compares every expected current head, subject binding, tenant binding, authorization binding, owner/schema tag, record fingerprint, and idempotency record. A missing or insufficient per-result binding rejects the whole publication. Conflict or contention before a confirmed commit returns a typed stale/concurrency result without a partial write. Replay and status-by-idempotency-key are authority-bearing reads: they revalidate the stored observation bindings against the new trusted invocation. After a lost or ambiguous commit acknowledgement, an authorized retry or status lookup returns the stored terminal result rather than attempting another semantic publication.

The minimum failure matrix is normative:

| Injection or conflict | Required durable/observable result |
|---|---|
| Before idempotency record or before commit | no publication rows; typed retryable/stale result as applicable |
| Commit succeeds, acknowledgement is lost | retry by the same key reads the original manifest and result identities; owner use cases do not rerun |
| Concurrent same key and fingerprint | exactly one commit; every caller receives the same committed result |
| Same tenant and key, different observation authority | typed denial without result-existence disclosure; original publication remains unchanged |
| Same key with different fingerprint | conflict; original publication remains unchanged |
| Stored result is missing or fails its bound fingerprint | fail closed and open typed storage reconciliation; never reconstruct by rerunning owners |
| Crash at any named transaction edge | state is either the complete publication plus idempotency record or no publication |

Fingerprints cover versioned canonical bytes: the bound schema and codec version, canonicalization version, digest algorithm and domain separator are part of the publication contract. The adapter verifies the fingerprint over those bytes and stores them or a representation proven equivalent by that codec. Any mismatch, missing required owner result, or internally inconsistent partial input rejects the complete publication: all records commit or none do. A valid ambiguous or partial external outcome is itself an owner-produced, non-authorizing domain result and must be published explicitly, not rejected as an incomplete transaction. The coordinator and driven transaction adapter may coordinate commit mechanics and serialize owner results, but may not construct, classify, validate, authorize, reauthorize, derive, correct, repair, replace, or re-evaluate any domain record or policy.

The scenarios below specialize that protocol without changing it:

| Scenario | Owner-produced results and independent meaning | Integration-specific requirements |
|---|---|---|
| Plan / effect | `planning` produces the `PlanningRevision`; `policy_authority` supplies a `PolicyEvaluation` bound to the exact subject and heads; the result carries the corresponding `AuthorizationEvidenceRef`; `effects` owns the intent lifecycle after publication. | Bridges invoke each owner through the `planning_effect` workflow's participant ports; its publication port commits the returned revision and intent through the SQLite adapter. |
| Mark done: Plan / Fact | `evidence_journal` produces an owner-authored `FactClaim` against its exact claim subject; `planning` produces the `CLOSE_TASK` successor against its exact current planning head. The claim neither validates, authorizes, derives, nor implies closure; closure neither validates nor implies the claim. | Bridges invoke the owners through `mark_done` participant ports; its dedicated publication port commits both returned DTOs. Standalone `CLOSE_TASK` and standalone claim commands remain legal. |
| Provider receipt / candidate evidence | `effects` owns `ProviderIngressEnvelope`, authenticated receipts, intents, attempts, transmissions, outbox state, and effect-outcome reduction. `evidence_journal` owns candidate evidence, provenance links, owner-authored `FactClaim` roots, and owner-authorized dispositions. A receipt may create linked candidate evidence but never creates, corrects, confirms, disputes, or retracts a personal claim without direct principal action. | Bridges invoke the owners through `provider_evidence` participant ports; its publication port commits the returned effect and journal DTOs. There is one canonical receipt identity: the journal record references the effect-owned receipt, never a second receipt model. Ambiguous or partial evidence remains explicit and cannot authorize a retry or planning transition. |

## Stage 0 physical slice

Stage 0 creates only the modules and package roots needed for an executable path:

```text
src/chiplog/
├── domain_primitives/{identity.py,tenant.py,principal.py,canonical.py}
├── capabilities/{planning/{domain.py,application.py},projections/application.py}
├── adapters/{driving/cli.py,driven/sqlite/{planning.py,projections.py}}
├── platform/sqlite/{connection.py,event_appender.py,schema.sql}
└── composition/production.py
```

At this stage `planning/application.py` contains the inbound command, use case, and repository port; `projections/application.py` contains the inbound query, query use case, and read-store port. A role becomes a subpackage only after it has multiple cohesive modules with distinct reasons to change. No empty role package is created to reserve a future shape.

| Stage-0 boundary | Existing behavior that earns it | Required evidence |
|---|---|---|
| Planning capability | its domain validates and constructs, and its use case commits, one tenant-bound typed planning revision | unit test plus forbidden foreign-domain construction fixture |
| Projection capability | rebuilds and queries committed planning state without becoming authority | rebuild test plus fixture rejecting projection-as-command input |
| CLI driving adapter | authenticates/normalizes one command and renders one query result | component test through public inbound contracts |
| SQLite driven adapters | implement the two capability-owned persistence ports | adapter contract and exact-version integration test |
| Platform SQLite substrate | supplies the one physical appender and transaction mechanics | EventAppender backpressure/shutdown and atomicity tests |
| Production composition | constructs this exact graph without policy or workflow code | composition-boundary fitness test |

Its command dependency path is:

```text
CLI adapter
  -> planning inbound port
  -> planning use case
  -> planning domain
  -> planning repository port
  -> SQLite adapter
  -> platform EventAppender
```

After commit, a separate query/render path reads the rebuildable projection; no adapter calls another adapter:

```text
CLI adapter
  -> projection inbound query port
  -> projection use case
  -> projection read-store port
  -> SQLite projection adapter
  -> committed projection result
  -> CLI rendering
```

`agent_loop`, `interpretation`, `policy_authority`, `effects`, `evidence_journal`, model/provider adapters, and eval tooling are added only with the stages that exercise their behavior and invariants.

The already selected test taxonomy remains separate from this production layout: tests are organized by the kind of evidence they provide, then by capability where a single capability owns the claim.
