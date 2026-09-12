# R9/R10/R11 parallel boundary

Status: common contracts validated; component evidence is recorded in each lane freeze.
Stage-1 runtime composition and promotion remain owned by R12.

Sources: `IMPLEMENTATION-ROADMAP.md` R9–R12, `project-architecture/NORMATIVE.md`
conversation/disclosure, dashboard families, journal admission and verified workspace
reads; `HEXAGONAL-CODE-LAYOUT.md`; R7/R8 freezes. R8 base is
`1d5578a940b30911433b6483ba12356929b54084`.

## Scope and ownership

R9 owns conversation, projections, disclosure enforcement, navigation, budgets and
R3 derivative/fence bridges. R10 owns evidence_journal records and actions, its
application persistence port and adapter to the existing platform EventAppender.
R11 owns calendar_observations, hermetic provider leaves and the specialized broker
read/release extension. It alone changes existing read-ledger/query/invalidator
implementation; changes needed by another lane are requested through the parent.
The parent owns this freeze and common wire schema. No lane changes another lane's
boundary without review and synchronized consumer validation.

R12 still owns composed cross-family transaction/frontier and Stage-1 promotion.
R9 admits journal/calendar families through ports at R12, not private imports now.
No real provider, model loop, production entitlement or deployment is introduced.

## Public wire contracts

`inert_shared/r9-r11-workspace-v1.json` freezes owner-local public DTO schemas in
`projections/workspace_boundary.py`, `evidence_journal/boundary.py`, and
`calendar_observations/boundary.py`. The copies carry bytes across owner boundaries;
they share no executable policy. Public async query ports consume `WorkspaceReadRequest`
and return `WorkspaceReadResult`. A DTO is untrusted data, never authentication or
proof of a snapshot: the broker/bridge must authenticate its caller independently,
bind its exact issued session and validate the complete context at acquisition and
release. String equality and caller-produced snapshot bytes are forbidden substitutes.
`verified_snapshot_bytes` is an opaque broker-authenticated reference/binding; raw
verified authority snapshots stay broker-private. Registry digest equality does not
replace validation of every independently current invalidator head.

The context binds tenant/database/principal/contour/channel, broker epoch, exact
owner/generation/session, snapshot bytes and identity/frontier, invalidator registry,
policy and deletion fence. Requests bind attempts, response slots, typed query,
bounded page size, ordering cursor and optional detail identity. Results preserve the
complete request context and explicit staleness; failure releases no content. Cursor
continuation retains the same snapshot and context and makes strict forward progress.
Every returned row preserves complete source identity/version/digest/label-head and
provenance, never a bare provider or journal string. Omission of required lineage
cannot create a successful bounded result; return typed indeterminate if closure
cannot fit the bound. External provider freshness never upgrades internal authority.

R9 owns the closed v1 disclosure lattice: UNRESTRICTED is explicit bottom;
ENDPOINT_RESTRICTED carries exact allowed endpoints; DENY_ALL is top. Missing or
unknown envelopes fail closed. Restricted joins intersect endpoint permissions;
empty intersection becomes DENY_ALL. Policies and exact source heads are checked
outside DTO constructors. R9 validates label canonical ordering and complete joins.
No model API narrows labels. Subsequent narrowing requires separately authenticated
exact successor authorization; existing descendants retain restrictions.

R9 adds its own typed ScreenLocation and ScreenSnapshotRef, binding tenant, builder
version, source frontier, clock, policy/config/budget and content hash. It uses the
stateless agent-dashboard package; Chiplog owns persistence and transitions.
R10 adds owner-local immutable command/display DTOs before behavior, binding exact
ingress utterance/digest, typed subject/payload/provenance, consequence, schema and
canonicalization versions, all current heads, confirmation identity and fingerprint.
R10 authorizes separately from payload, returns replay/conflict/stale/unresolved,
and routes physical publication through EventAppender; no private competing appender.

## Guarantee preflight and planned evidence

| Claim | Owned decision/data | Independent observable | Forbidden substitute | Boundary fixture | Evidence |
| --- | --- | --- | --- | --- | --- |
| Same read cut | Broker-issued full context and recipient | Invalidator/release order and actual returned rows | Caller frontier equality or replacement session | Every identity/head substituted; both race orders | R11 current selectors; promotion HOLD |
| Bounded complete reads | Owner order/cursor and complete source closure | N/N+1 rows, strict progress, source graph | Truncating lineage or changing snapshot between pages | Limit, duplicate/reorder, unknown cursor, missing source | R9/R11 current selectors; promotion HOLD |
| Immutable workspace | Projections snapshot/LRU/budget transition | Persisted sequence and public rendered output | Screen as authority, background focus promotion | Fourth family, budget boundary, lagging screen | R9 current selectors; promotion HOLD |
| Monotone disclosure/deletion | R9 complete envelope, current policy and R3 fence | Restricted marker through every executable derivative; ordinary read after fence | Missing equals bottom, model narrowing, rebuild resurrection | Omit/add/alias/substitute surface and provenance; fence race | R9 current selectors; promotion HOLD |
| Principal-authored fact only | Journal proof/display and owner canonical bytes | Durable Fact rows and unchanged Plan through public ports | Model/provider assertion or generic assent | Complete negative speech-act vector; confirmation head mutants | R10 current selectors; promotion HOLD |
| Exact replay/current heads | Journal whole fingerprint and predecessor | Lost-ack replay, rival successor outcomes and durable rows | Timestamp winner, correction replacing subject | Same ID changed field, stale predecessor, competing candidates | R10 current selectors; promotion HOLD |
| Calendar evidence only | Calendar observation provenance and broker release | No Plan/Fact/effect writes, stale typed response | Provider projection promoted to authority or adapter shortcut | Foreign peer/context, invalidation, provider lag/unknown | R11 current selectors; promotion HOLD |

All mutation families apply: omission, addition/unknown, substitution/alias,
duplicate/reorder, stale/race, N/N+1 and logical/physical identity mismatch. Numeric
bounds also require ordering, progress and referential closure. Authentication
evidence is independently issued peer/session state, never payload principal IDs.
Downstream fill-ins are transport framing only; canonical owner records are retained.

Requirement map: R9 A19/A21/A24/A27/A106 → V4/V5/V7 fixtures;
R10 A04/A22/A30–A32 → V5/T02 component fixtures (version negative T02 before use);
R11 A05/A24 → V4/V5 specialized bounded-read fixtures. Full T02 loop is R18/R19.
Contract consumer shape tests are not behavioral or production-readiness evidence.

## Execution plan and assumptions

Cold preflight identified and this plan resolves two blockers: the R7-specific read
extension has one owner (R11), and R10 must use the existing EventAppender. Prepare
and validate common DTOs, then copy this exact base into three worktrees and run
lanes concurrently. Each lane prepares its remaining owner-local contracts before
logic and provides executable component evidence. Parent inspects actual changes,
runs combined regression and a cold result review. No commit is implied; existing
user changes stay intact. Worktree transport uses checked patches, not hidden commits.

## Convergence review

Public consumer shape checks, Ruff and mypy passed before parallel implementation.
Cold plan review resolved the broker-extension and physical journal ownership gaps.
Cold component reviews subsequently reproduced and drove repairs for calendar response
substitution, journal candidate/claim separation and principal ownership, actual
workspace disclosure dependencies, and recent-message context selection. Rechecks
reported zero remaining P0/P1 within their component scopes; this is bounded review,
not independent production entitlement or proof of all possible executions.

One changed predecessor mechanism is exact derivative-registration replay in
the existing EventAppender: current fence, complete sources and fingerprint still
validate before identical durable binding returns REPLAY; changed binding rejects.
`tests/platform/test_deletion_provenance.py` covers restart, changed binding and
post-fence rejection. R8's exact source inventory is extended only after actual source
review; it is never refreshed by runtime verification. Common-only and combined
worktrees have inventories for their own exact source sets.

The combined R8 offline-import gate registers only the stateless `agent_dashboard`
facade names used by the two R9 consumer modules. The installed package facade,
renderer and serializer were inspected: their reachable imports are local DTO/helpers
and the standard library, with no provider/transport dependency. Other consumers,
optional hub/TUI submodules and unregistered symbols still reject. Direct import-gate
mutants exercise these denials independently of the separate source-hash gate.
This changes no canonical R8 entrypoint and grants no R12 deployment entitlement.

The first combined run and staged cold review exposed two integration blockers:
the unregistered dashboard dependency and unchecked private R9 dispatch. Repair is
authorized under the user's one-time exception to the uncommitted-file limit;
no threshold/hook is changed and no commit is authorized by that exception.

## Current evidence locations

R9, R10 and R11 lane freezes each list current selectors by tested entry. Their
historical commands and counts remain explicitly historical. Cross-owner DTO
checks stay in `tests/architecture/test_r9_r11_contract.py`; derivative replay stays
in `tests/platform/test_deletion_provenance.py`; offline-import mutants now live in
`tests/verification/test_r8_surface.py`.

`quality/test-migration-r9-r11.json` records the pre-migration baseline
`d6339eed2bc19c92d126bda3aef3a0db0025a143`, all 197 old/new full nodeid pairs and exact
execution/audit commands. The current migration run passed 197 items and matched
all 591 phase outcomes, including skip/xfail. Existing evidence slices are retained;
this comparison is placement evidence, not deployment promotion.
