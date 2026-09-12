# R11 calendar observation boundary

Status: component evidence executed; integration/catalog audit and cold promotion
remain parent-owned. No Stage-1 or real-provider promotion.

Scope: read-only hermetic calendar agenda/detail; A05/A24 and component V4/V5.
Sources: IMPLEMENTATION-ROADMAP.md R11, R9-R11-CONTRACT-FREEZE.md,
src/chiplog/platform/read_ledger.py, platform/authority_reads.py. R7's planning
SQL and its frozen physical authority surface are not calendar queries.

Plan: freeze calendar observation/provider contracts; validate public imports;
specialize the broker operation/state/manifest while preserving R7 defaults;
implement immutable snapshots and authenticated, bounded release; exercise public
ports and adversarial mutations; run R7/R8 regressions. Parent cold-reviewed the
parallel plan; parent retains final integration and cold result inspection.

Assumptions: hermetic provider observations are non-authoritative evidence. Runtime
composition issues a peer capability independently of request DTOs. A response is
linearized at durable release, before invalidators that order after that release.
Provider freshness is source-asserted and never authenticates internal state.
R12 owns cross-family and R20/R21.1 real-provider composition.


## Current test selectors after placement migration

The historical commands below retain their original paths and reported results;
they are records carried by pre-migration commit
`d6339eed2bc19c92d126bda3aef3a0db0025a143`, not commands rerun on the new layout.
The current combined migration run passed 197 items; the audit compared all 591
setup/call/teardown records, including skip/xfail. Exact commands, full nodeid pairs,
and observed outcomes are in `quality/test-migration-r9-r11.json`.
This placement evidence does not grant runtime or deployment promotion.

| Key | Current selector | Scope |
| --- | --- | --- |
| C | `tests/platform/test_calendar_reads.py` | Direct broker/ledger context, invalidation, release and integrity |
| A | `tests/composition/test_calendar_acquisition.py` | Public composition agenda/detail and independently pinned provenance |


## Owner-local contracts

CalendarObservation retains exact provider/calendar/event/version, observed time,
freshness and canonical content plus complete disclosure provenance. CalendarBatch
is immutable with source revision. ProviderPort supplies only batches, no mutation.
CalendarReadBroker issues a WorkspaceQueryPort bound to a separately minted peer
capability, immutable broker-private snapshot and complete public context. The
verified_snapshot_bytes field is a random opaque reference, never raw snapshot.
The ledger state adds explicit database, principal, channel, policy/deletion and
provider heads; its subclass leaves R7 files and default serialization unchanged. Queries
are closed CALENDAR_AGENDA/CALENDAR_DETAIL, owner calendar_observations only.

## Guarantee preflight

| Claim | Owned data/decision | Independent observable | Forbidden substitute | Boundary fixture | Evidence |
| --- | --- | --- | --- | --- | --- |
| Authenticated same cut | broker peer capability, exact issued context and private batch | returned bytes and durable attempt state | payload identity, supplied snapshot bytes, replacement session | mutate each context field, foreign peer, invalidation at acquisition/release | C (current selectors below) |
| Current invalidators | durable extended state and registry, shared release transaction | stale result has no bytes | registry equality alone | each current state head changes before release | C (current selectors below) |
| Bounded closure/progress | exact ordered immutable events, one complete envelope per row, cursor bound to query and snapshot | N/N+1 traversal, no duplicates, preserved sources | lineage truncation or new snapshot per page | unknown/reordered cursor, bound, missing/foreign lineage | C, A (current selectors below) |
| Provider evidence only | immutable provider identity/version/provenance and typed freshness | canonical rows explicitly provider observed; no effect/write handles | projection as Fact or Plan or authority | lagging/unknown source, no mutation port | C, A (current selectors below) |
| R7 compatible release | structural operation contract and opt-in state model | existing R7/R8 tests, unchanged default state encoding | calendar masquerading as planning operation | exact query/owner/capability/target manifest | C (current selectors below) |

Mutation families apply: omission, unknown/addition, substitution/alias,
duplicate/reorder, stale/race, N/N+1, logical/physical identity mismatch. Physical
identity is the ledger database binding for this hermetic component; this does
not claim external provider authenticity or R7 AMR authority verification.
Authority-sensitive reads continue to use BrokerAuthorityReader's recorder path.
Canonical observation bytes are owner-made; downstream may frame transport only.
Public provider data cannot issue peers, read sessions, write internal authority,
or invoke effects. Contract validation alone is not behavioral evidence.

## Historical evidence and implementation boundary (pre-migration record)

`uv run pytest tests/r11 -q` → 70 passed. The historical `tests/r11/test_calendar_reads.py` executed
every planned row: each public context field and each current state invalidator;
acquisition and prepare/release races; release-first order; foreign or forged peers;
request/attempt/response-slot mutations; strict cursor progress and snapshot/query
binding; numeric and aggregate provenance bounds; duplicate/reordered/missing/foreign
sources; independent pinned labels versus joint provider downgrade; nested schema
bypasses; typed lag/unknown; immutable canonical rows; and no Plan/Fact/effect storage.
These rows are PASS for component evidence, not promotion evidence.

The capability-aware coordinator is `adapters/driven/calendar_reads.py`, constructed
by `composition/r11.py`, and calls calendar owner public exports. The platform module
`calendar_read_ledger.py` is only the exact technical query/operation/state manifest
and a structural adapter onto the unchanged R7 durable begin/release/dequeue algorithm.
R8 identity, trace and recorder modules remain unchanged: calendar never presents
provider evidence as an AuthorityRead, and authority-sensitive callers still require
the existing recorder. The calendar release ledger binds full request/recipient and
proof fingerprints; this is component read provenance, not an R8 authority trace.

Physical identity is obtained from the actual ledger object, with no alternate
path argument. Reached replacement inside release or dequeue returns typed stale
before content emission; pre/post stat checks do not claim an atomic filesystem
identity primitive. Invalidations and release retain R7's durable common order.
Authoritative state corruption raises CalendarReadIntegrityError with operation,
tenant, record and chained cause, rather than becoming a partial observation result.

`uv run mypy src/chiplog/adapters/driven/calendar* src/chiplog/platform/calendar_read_ledger.py src/chiplog/composition/r11.py src/chiplog/capabilities/calendar_observations tests/r11`
→ success in 9 files; corresponding `uv run ruff check` → all checks passed.
`uv run pytest tests/r7 tests/r8 -q` → 151 passed, 19 failed. All 19 failures are
the deliberately closed R8 implementation inventory refusing new source files
before parent review/catalog reconciliation. No R7 file or implementation pin was
edited or bypassed; the R7 suite passes. At that historical checkpoint, parent review of combined files, inventory
reconciliation and a subsequent R8 rerun were still pending. This failure count
does not describe the current migrated tree.

Cold review reproduced a P1 where schema-valid durable response substitution could
escape the dequeue path. The broker now retains the exact prepared response bytes
and compares them before emission; a mismatch raises CalendarResponseIntegrityError
with operation, tenant, attempt identity and chained cause. Reached tests cover both
bytes-only substitution and substitution with a rewritten stored digest. The original
witness now raises the typed error; cold recheck reports zero remaining P0/P1.
`uv run pytest -q tests/r11 tests/architecture/test_r9_r11_contract.py` → 77 passed.
