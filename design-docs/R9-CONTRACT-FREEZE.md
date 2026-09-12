# R9 component contract freeze

Context: IMPLEMENTATION-ROADMAP R9, NORMATIVE workspace/disclosure, R9–R11 common
freeze. R8 authority remains external. R9 component assembly is hermetic and does
not promote the R7 runtime graph; cross-family runtime composition remains R12.

Plan: materialize owner-local immutable navigation, budget, snapshot, conversation
and disclosure ports; validate consumer imports; implement deterministic workspace,
canonical tenant history, durable replay and existing R3 fence bridge; execute
negative public-port fixtures. Assumptions: published agent-dashboard 1.0.0 is the
stateless boundary; broker supplies authenticated read/context and current heads;
conversation ingress is accepted content supplied by an authenticated upstream port.
No model callable API appends history or narrows labels. Re-read against scope.

| Claim | Owner/data | Observable | Forbidden substitute | Fixture | Evidence |
|---|---|---|---|---|---|
| Canonical tenant history | Conversation immutable ordered records, channel visibility | Reopen database, bounded cursor continuation | Per-channel logs, caller principal authentication | Foreign channel/tenant, changed replay bytes, unknown/stale cursor | H (current selectors below) |
| Exact disclosure | Projections joined envelope and registered actual callable identities | Restricted marker denied through each executable sink | Label omission, partial provenance, alias/dynamic replacement | Missing/extra/duplicate/substituted callable, narrowed join | W, D, S (current selectors below) |
| Immutable replay | Snapshot complete builder/context/budget/hash and sequence | Restart returns same serialized screen and LRU | Rebuild masquerading as replay | Corrupt bytes, changed context, reordered refs | W, S (current selectors below) |
| Bounded workspace | Versioned allocation and three distinct family LRU | Fourth access evicts tail, focus last, floor | Dropping conversation or provenance | N/N+1, low budget, background refresh | W, B (current selectors below) |
| Same batch cut | Entire input read context checked before rendering | Mismatched snapshot/session/frontier returns no screen | Frontier integer equality | Each context member substitution, lagging output | W, P (current selectors below) |
| Fence exclusion | R3 guarded source reads plus immutable source IDs | Ordinary history/replay/context fails after fence advance | Unchecked cached display, stale generation rebuild | Pre/post fence order, missing/foreign physical source | H, D (current selectors below) |

Mutation families apply to all claims: omission, unknown/addition, alias/substitution,
duplicate/reorder, stale/race, bounds and logical/physical identity mismatch. Source
closure never truncates. Invalid authoritative bytes raise typed operation/tenant/ID
integrity errors chained to cause. Fixed snapshot pagination orders by sequence and
strictly advances. Authentication is an independently configured port concern, not DTO
principal strings. Shared DTOs are unchanged; owner-local additions live in
projections/r9_boundary.py. A19/A21/A24/A27/A106 map to the current component selectors below;
full V4/V5/V7 runtime promotion remains R12.


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
| W | `tests/capabilities/projections/test_workspace.py` | Snapshot, budget, replay, context binding and disclosure lattice |
| B | `tests/capabilities/projections/test_budget_policy.py` | Budget DTO floor |
| P | `tests/capabilities/projections/test_planning_workspace_bridge.py` | Public planning query bridge |
| D | `tests/capabilities/projections/test_disclosure_provenance.py` | Complete source closure |
| H | `tests/composition/test_conversation_workspace.py` | History, channel scope and fence through component |
| S | `tests/composition/test_workspace_surfaces.py` | Component dispatch and graph rejection |


## Historical component evidence (pre-migration record)

`uv run pytest tests/r9 tests/platform/test_deletion_provenance.py -q` — 57 passed.
`uv run mypy src tests/r9` — success (95 source files, command-reported scope).
Scoped `uv run ruff check` over R9 files and tests passes. The earlier contract
consumer import validated ScreenLocation and public persistence/history protocols
before behavior. agent-dashboard is a normal published dependency pinned by uv.lock;
no local filesystem dependency or copied package is used.

- A19/A21: `test_conversation_fence.py` publishes canonical accepted conversation
  via existing EventAppender; reads it through guarded_records; proves replay,
  conflict, strict forward cursor, bad cursor rejection, human channel presentation
  versus same-contour agent context, and post-fence exclusion.
- A24: `test_workspace.py` binds every context member and rejects a foreign result
  cut; `test_planning_bridge.py` uses the real historical planning projection query
  and verifies no Plan mutation. Lagging screens remain labeled non-authoritative.
- A27: fourth-family LRU, preserved navigation, background non-promotion, focus
  allocation, protected minimum and insufficient budget, complete context character
  bound including separators, immutable restart replay and ref/render/provenance
  corruption mutants are executable in `test_workspace.py`.
- A106: `test_surfaces.py` rejects extra, omitted, substituted, aliased, instance
  replaced and renderer replaced executable paths before content release. The later
  staged audit exposed an omitted private history dispatcher. Its repair adds
  explicit private history/cursor, snapshot verification, persistence verification,
  registry and budget surfaces, and pins class dispatch descriptors of the bound
  component and platform leaves. Instance shadowing and class replacement, including
  staticmethod replacement, fail at public entrypoints before the replacement runs.
  Workspace canonicalization/identity and payload-verifier bindings are also pinned.
  This is a hermetic graph identity check with externally supplied context/guard
  ports; it does not attest arbitrary process memory, function bytecode mutation,
  external dependency internals or production broker composition.
  `test_conversation_fence.py` independently proves a derived payload cannot drop
  an unrestricted source from its complete provenance manifest. Disclosure lattice
  tests prove intersection, deny-all, endpoint refusal and forbidden narrowing.
- R3: parent-owned exact derivative registration replay repair stays behind original
  fence/source/fingerprint checks; the existing deletion suite verifies rival
  binding failure and stale-fence refusal. Repeated identical snapshot registration
  and ordinary post-fence history/replay are exercised with the actual EventAppender.

Authentication at the owner component is a required independently implemented
ReadContextPort, tested using a separately issued hermetic session leaf. A DTO does
not issue a cut. Broker recipient enqueue total ordering and the global published
runtime graph remain R12 composition work. This evidence does not promote R9 into
an R7 authority generation and does not certify production exposure.

SQLiteWorkspaceStore persists only derivative snapshots; it is not a second
canonical conversation appender. Conversation records carry checked owner canonical
fingerprints inside EventAppender bytes. R3SourceHeads verifies current owner-issued
source identities and either direct physical provenance or separately trusted producer
closure keyed to derived payload digest. Arbitrary caller subsets never establish closure.

## Cold result repairs

The cold audit found two P1 defects. The component manifest now traverses the actual
supplied graph, rejects unknown concrete classes and subclasses at assembly/release,
checks each bound method's receiver and compiled implementation, and includes the
nested planning owner query plus actual EventAppender/materializer edges. Supplying
an unregistered query, wrapping an unknown query leaf in a registered adapter, or
subclass substitution fails before output. Fixtures use real R3 and planning leaves.

Agent context now selects the latest bounded tail at the immutable cut and emits it
in ascending sequence; human history retains forward pagination. The reached fixture
publishes distinct old/new physical sources and accepted conversation entries, then
proves max_rows=1 returns and renders the latest message, not the old marker.
Historical witness at the pre-migration snapshot:
`PYTHONPATH=. uv run python /tmp/r9_cold_witness.py` stopped at startup with typed
unknown/subclass graph rejection before the unregistered implementation executed.
That temporary script used the old helper imports; it is not a current replay command.

Historical repair verification (original commands and reported scope): `.venv/bin/python -m pytest -q tests/r9` — 68 passed.
`.venv/bin/ruff check src/chiplog/composition/r9.py tests/r9` — passed.
`.venv/bin/mypy src/chiplog/composition/r9.py tests/r9` — success, 6 source files.
The public-port regressions cover history, agent history, refresh, replay and
context text; the private history mutant constructs the original mismatched
`UNVERIFIED SECRET` payload, and asserts its dispatcher never executes.
