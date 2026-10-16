# R14.6 durable-version admission preparation

Status: **context/contracts only; startup gate and mounted evidence HOLD**.
This is the existing per-slice requirement in `R14-REMAINING-ROADMAP.md`, not a
migration capability or permission to recreate user data.

## Requirement and current gap

`project-architecture/NORMATIVE.md` paragraphs 64, 163 and 677 require the exact
database-genesis/schema/journal-schema/canonicalization/registry/companion-manifest
tuple before replay, materialization, epoch, owner compartments, caches or work.
The independent authenticated genesis and protected current binding govern that
comparison. Parsed database metadata, a requested runtime profile or a supplied
DTO cannot grant admission. Unsupported, historical or missing tuple members
produce typed `UNSUPPORTED_SCHEMA_VERSION / HOLD_ADOPTION`, without synthesis,
coercion, migration or compatibility ranges. Unverifiable authority/content is
an integrity hold, not a supported version.

Actual current code:

* `capabilities/deployment_trust/_model.py::DatabaseGenesis` retains the legacy
  three-field genesis. `_r7_process.py::_evaluate` still emits those three fields
  during bootstrap. Preserve these historical bytes and their interpretation.
* `platform/r7_trust_durability.py` validates that legacy INITIALIZE payload in
  both historical reads and fresh owner output. No full version tuple is present.
* `platform/_sqlite.py::_admit` checks physical table/column/schema structure,
  `store_metadata.store_version`, and registered persisted record owner/schema.
  This is necessary physical admission, not the six-member startup gate.
* `composition/r7_planning.py::_open_runtime` constructs trust journal/materializer,
  planning store, read ledger and supervisor before store admission and trust
  recovery. The version check must precede those mutating constructors and replay.
  Read-only authority validation primitives remain necessary to authenticate it.
* The current INITIALIZE companion set in `deployment_trust/_records.py` is
  tenant decision, current database state, database genesis, deployment tenant
  binding and tenant registry entry. A tuple label cannot substitute for exact
  authenticated completeness of that set.

The preparation adds an explicit complete `DatabaseVersionTuple`, a distinct
eight-field `DatabaseGenesisV2`, and a typed unsupported-version failure through
the public deployment-trust boundary. Every version member is required; legacy
missing fields receive no defaults. V2 fixes genesis version 2 without changing
the old private V1 model. These values are inert; constructing one neither
authenticates genesis nor opens a runtime.

The other running version identities still need a concrete mapping to admitted
canonical bytes, digest domains, registry and companion set before initializer
or gate implementation. This slice defines no running-tuple constant. An
unregistered integer in a DTO is not policy registration.

## Requirement-to-evidence map

All runtime evidence below is **HOLD** until executed. Planned tests live in
`tests/composition/test_r14_startup_version_admission.py`; this is a future path.

| Claim | Owned decision/data | Independent observable | Forbidden substitute | Boundary fixture / planned evidence |
|---|---|---|---|---|
| Exact complete tuple | Independently authenticated genesis and exact running tuple | Each missing/older/newer/unknown member refuses before startup effects | Store version alone, tuple-shaped caller DTO, coercion or default | Genuine current initializer plus one-member mutations; public shape tests support expressibility only |
| Gate before replay/work | Canonical `_open_runtime` admission cut, shared by legacy entrypoints | No replay, materializer, owner start, epoch, cache or ingress callback; unchanged SQL and every pre-existing authority file | Error raised after constructors/recovery already ran | Authentic legacy bundle and unsupported tuple; observe callbacks and file set/bytes |
| Immutable original version interpretation | Legacy V1 bytes/decoder separate from V2 genesis | Authenticated old fixture remains byte-identical and is refused for ordinary startup | Rewriting V1, synthesizing five missing members | Reproducible genuine legacy producer, not current initializer after it changes |
| Only canonical empty initialization | Empty database/journal authority pair and exact V2 companions | One exact initialization; nonempty missing/duplicate/foreign genesis refuses without repair | Empty SQL tables treated as empty authority despite journal decisions | Empty/current, journal-only, DB-only and duplicate/foreign fixtures |
| Current recovery partition | Complete authenticated current decision/commitment and exact physical members | Absent/prefix completes exact remainder; complete returns exact replay; corrupt/rival/extra/incomplete companions refuse | Prefix completion confused with unsupported history, companion synthesis | Exact current absent/prefix/complete and corrupt companions, unchanged-head replay |

Mutation families: omission (each version/companion); addition/unknown (extra
version member/schema); substitution/alias (tenant/database/root and version
source); duplicate/reorder (duplicate genesis, companion order); stale/race
(version/binding changed between observation and use); N/N+1 (exact current
version versus adjacent versions, not a range); logical/physical mismatch
(authenticated genesis versus materialized bytes). None is silently N/A. Failure
classification alone cannot prove no write.

## Semantic splits and existing workflow

1. This context, public inert contracts and consumer-side validation. Do not
   change the initializer, legacy decoder or startup behavior in this split.
2. Freeze the exact running tuple and a reproducible authentic legacy fixture;
   write RED mounted admission/no-write tests. Define V2 canonical bytes and
   digest selection explicitly before changing the owner producer or validators.
3. Mount read-only preflight before mutating constructors, update the fresh
   initializer and explicit V2 interpretation, then verify current recovery,
   unsupported refusal and all historical entrypoints. No migration branch.

Inspect `open_r7_runtime`, `open_r8_runtime`, `open_execution_runtime` and
`open_common_cli_execution_runtime` plus their existing fixture drivers before
changing the canonical entrypoint. Their bootstrap conveniences cannot waive
version admission or silently create missing authority companions.

Entry command `git status --porcelain=v1 | wc -l` returned 15 inherited untracked
paths; preserve them, including `.worktrees/`, diagnostic documents, OS files
and SQLite sidecars. No inherited tracked changes at HEAD `e24d75e`. This split
owns four paths: this context, public contracts, public exports and consumer
tests. Count final status/evidence/review paths when estimating subsequent
splits against the harness soft 10/hard 40 dirty-path thresholds; do not split
semantics merely to evade them.

The active native cross-seal run uses the separate frozen source checkout
`/private/tmp/chiplog-r14-readiness-witness-4452ebb`; this preparation must not
edit it or imply its result. R8 implementation identity hashes `src/chiplog/**/*.py`.
Checkpoints use staged structure/import checks without repinning; final integration
requires stable reviewed source, strict catalog reconciliation and full tests.
Contract shape checks and cheap preflight do not prove runtime version admission,
full architecture compliance, R14.6 completion or release/adoption readiness.

## Observed startup gap and existing-only exclusion

The authentic V1 fixture now drives the actual R7 public opener in the pending
`tests/composition/test_r14_startup_version_admission.py` worktree test. Both
ordinary legacy input and the same input with only its authority lock removed
were admitted: journal, trust materializer and supervisor constructors ran.
Command `.venv/bin/pytest -q tests/composition/test_r14_startup_version_admission.py
--tb=short` was RED, **2 failed in 2.68s**. The test observes real constructors,
not replacement implementations, and captures all file names, bytes and modes.
These tests remain RED work in progress; they are not completion evidence for
the primitive below.

`AuthorityGate.hold_existing()` now shares the existing lock implementation while
omitting parent creation and O_CREAT. It keeps exact file identity/mode/owner,
cross-process exclusion, nesting and fork checks. A nested ordinary hold reuses
the existing descriptor. This is lock acquisition without provisioning, not a
read-only transaction or admission credential. The missing-parent/lock tests
were RED because the method was absent; after implementation, command
`.venv/bin/pytest -q tests/platform/test_authority_gate.py --tb=short` passed
**33 tests in 2.31s**, including independent-process exclusion and fork cleanup.
No startup caller is wired to this primitive yet.

Cold route inspection found further upstream work: the existing historical trust
verifier requires a materializer constructor; verification authenticates the
INITIALIZE binding but does not resolve an independent protected CURRENT registry
head after binding/root changes. Live SQLite mode=ro can also modify/create SHM,
while immutable=1 would ignore live WAL. The selected design route is a pure
authenticated journal snapshot under existing-only exclusion, a protected current
binding/root registry and SQL content verification on a disposable WAL-aware copy.
It must preserve the lock through constructor consumption and retain all original
sources byte-for-byte on refusal. This route is not implemented or admitted.

The candidate tuple `(2,1,1,1,1,1)` remains a proposal until exact schema/read-graph,
registry, canonicalization/digest and companion descriptors plus golden bytes are
frozen. No constant is registered from a list of integers alone. Canonical empty
initialization, current binding/root publication, unsupported refusal and current
exact-prefix recovery remain required. No all-startup-HOLD shortcut or migration
branch is the intended product result.

## Read-only journal acquisition and unresolved admission design

The private adapter now provides `read_authenticated_journal_snapshot` without
constructing an append-capable journal or SQLite materializer. It requires the
caller's already-held gate before opening anything, takes a shared lock on the
existing journal lock, opens body/key/head/lock O_RDONLY with no provisioning,
pins the actual decoded head descriptor, and rechecks identities and bytes before
returning immutable entries/source identities/key fingerprint/body digest. Partial
acquisition closes every opened descriptor. The physical decoder and HMAC formulas
are shared with the existing writer; exact-body cache hits still check the head.
This observes the protected physical chain, not operator authorization or CURRENT.
It cannot defeat coherent replacement of all protected anchors or adversarial
change-and-restore between observation cuts; those stronger claims are not made.

Command `.venv/bin/pytest -q tests/platform/test_readonly_decision_journal.py
--tb=short` was RED: **2 failed in 0.51s**, because the new API was absent. After
implementation, the new 21 cases plus existing journal/cache, historical trust
reader and deployment-gate tests passed: command `.venv/bin/pytest -q
tests/platform/test_readonly_decision_journal.py
tests/platform/test_existing_only_decision_journal.py
tests/platform/test_journal_exact_body.py
tests/platform/test_h1_historical_trust_reader.py
tests/platform/test_deployment_authority_gate.py --tb=short` gave **70 passed in
1.42s**. This includes authentic V1 bytes with all writer factories and SQLite
opening forbidden, missing/corrupt/aliased sources, interrupted head publication,
in-place changes, replaced head and resource cleanup. Package exports stay intact;
the helper is private pending its eventual registered consumer.

A wider seven-file check found **3 failed, 107 passed in 1.88s**. The same three
selectors on unchanged `2e7c27f` also failed (**3 failed in 0.21s**): the R4/R5
freeze does not enumerate current trust exports and two already-added record
types. These baseline failures remain integration obligations; do not repin or
weaken the old freeze merely to accept current bytes. The two actual startup
admission RED cases also remain open and outside this primitive checkpoint.

The cold design audit `/private/tmp/r14-version-route-design-redteam.md` found four
P1 issues in the larger proposed route, not in this independent journal primitive:

1. `_open_runtime` has no database-instance input while `bootstrap` first starts
   a quarantined owner and only then emits INITIALIZE. Freeze a sole initializer
   and identity contract before mounting the pre-constructor version gate; retain
   the existing bootstrap workflow through an explicit, tested split.
2. Missing mandatory CURRENT/keys/head must HOLD without synthesis. Published
   CURRENT plus authentic current absent/exact-prefix materialization must instead
   recover exact bytes. Freeze the mandatory-anchor versus recoverable-materialization
   partition and initialization fsync/rename crash cuts; blanket partial rejection
   or silent initial companion synthesis is insufficient.
3. A thread-reentrant gate cannot protect across await. Consume admitted sources
   in a synchronous cut before the first await, then require a fresh protected
   check before later startup effects; test competing same-loop substitution.
4. CURRENT may bind the INITIALIZE decision, but INITIALIZE cannot also embed
   final CURRENT digest. Freeze a forward-only registry-instance → INITIALIZE →
   CURRENT graph with exact five-companion output and no digest placeholders.

These P1s must be resolved before executing that larger route. Existing
`architecture/r7_storage_surface.py`, `r7_read_registry.py`, `verification/r7_read_surface.py`
and `platform/authority_reads.py::AMR_FINGERPRINT` supply real descriptors to reuse;
there is no basis to invent a parallel storage/read manifest. Full actual graph
coverage, tuple registration and all no-write/current recovery evidence are still
required, not inferred from the existence of those constants.

## Frozen authentic legacy input

Before changing the initializer, the real R7 runtime at revision
`08465384aa9127909909de49d9a9eb560a8b3cd4` produced a bootstrap-only V1 bundle.
The fixture `tests/composition/fixtures/r14_legacy_genesis_v1.json.gz` preserves
all ten closed files, including authenticated journal/key/head and exact trust
materialization. Its adjacent Markdown file records the reproduction command,
public fixture credentials and artifact SHA256; the capture script lives at
`tests/support/r14_legacy_genesis_capture.py` and requires that producer revision.
No test regenerates the historical fixture. Command `.venv/bin/pytest -q
tests/composition/test_r14_legacy_genesis_fixture.py --tb=short` passed one test
in 0.76s: complete historical authentication succeeded and every restored file
remained byte-identical. This supplies preserved V1 input for the future startup
refusal test; it does not implement version admission or its no-write guarantee.

## Executed preparation checks

Before and after changes, `sh .harness/scripts/test.sh --preflight` exited 0.
Command `.venv/bin/pytest -q
tests/capabilities/deployment_trust/test_genesis_contracts.py --tb=short`
passed **15 tests in 0.13s**. Scoped Ruff and diff checks passed. These tests
validate public immutable shapes, required explicit members, numeric types and
the fixed failure code/adoption status. No startup behavior is implemented or
claimed by this preparation. The V2/no-defaulting choice is recorded through
`decide.sh`; no running-tuple values were inferred from the fixture's integers.

## Refined initialization and recovery route (not implemented)

The actual fresh public R7 opener now has a third RED lifecycle witness in
`tests/composition/test_r14_startup_version_admission.py`. Command
`.venv/bin/pytest -q tests/composition/test_r14_startup_version_admission.py::test_fresh_public_opener_initializes_before_owner_and_reopens_without_bootstrap --tb=short`
failed **1 test in 0.64s**: `trust.verify()` is None before yielding. The test
requires INITIALIZE-only BOOTSTRAP_REQUIRED, exact eight-key V2 genesis, no real
supervisor start or owner call, and stable journal/AMR-anchor bytes on reopen.
Its later assertions are not reached yet. It does not prove tuple registration,
CURRENT validation or no-write refusal. All three startup tests remain RED WIP.

The selected sole broker initializer lives inside the canonical opener's proven
fully absent branch, before owner/epoch effects. An explicit initialization
database identity is forwarded by existing fixed-id CLI/hermetic callers; the
default fresh opener generates and retains one identity. On existing state the
same input is only an equality constraint. Bootstrap checks authenticated identity
and BOOTSTRAP_REQUIRED before starting quarantine; owner emits BOOTSTRAP only.
Convenience wrappers must replace their `verify() is None` bootstrap heuristics
with the exact initialized phase. Historical R6 producers and V1 fixture bytes
retain their original interpretation.

The initial main image includes exactly `(tenant_id, "r6", 0)` in deletion_fences
before publishing initial commitment C0/CURRENT. Full AMR includes this row;
the local bootstrap binding's own-fence exclusion does not remove it from AMR.
Thus bootstrap's fence is exact no-mutation replay and initial anchor remains
unchanged. A metadata-only C0 followed by a new fence would violate the existing
full commitment check. The fresh-image constructor must not call writer-only
`_install_fence`, attach a writer early or bypass its token. Missing/changed
baseline on an existing bundle is not permission to provision it again.

NORMATIVE paragraphs 64/66/163/677 distinguish an exact prefix of complete
decision batches from incomplete companions. Missing trust materialization or
whole complete-decision prefixes can replay the authenticated exact batches;
1..4 of the five INITIALIZE companions, gaps, extras, orphan rows or wrong
digests must HOLD without repair. Do not broaden the existing partial-companion
rejection. A global decision-prefix/content proof must precede live replay.

CURRENT is downstream of immutable registry identity, genesis/signed binding,
INITIALIZE physical decision id and five companions. INITIALIZE never embeds
the final CURRENT digest. CURRENT publishes last after durable journal, SQL and
initial commitment; interrupted provisioning without CURRENT holds without
automatic adoption. A visible authentic CURRENT after rename uncertainty is
classified from the actual protected namespace. Protected anchors are mandatory;
recoverable SQL is distinct. The trust journal can reconstruct its own exact
records; a main authority hash alone cannot recreate arbitrary lost R14 data.
Published fresh-state reconstruction requires the exact registered initial image;
later main recovery requires complete authenticated replay bytes and a verified
predecessor. Full main/WAL loss with insufficient recovery bytes must HOLD.

Admission, constructor consumption and permitted recovery form a synchronous
gate-held cut before await. After suspension, fresh short protected checks must
precede each actual startup authority effect or generation. Do not move generic
full-readiness admission ahead of exact fence replay: pending recovery can need
that no-mutation replay before readiness. Revalidate the current admission
receipt/full commitment after replay and before subsequent consumers instead.
Private WAL-aware SQLite copies permit content inspection without opening source
SQLite or writing source SHM; immutable=1 would omit WAL and is unsuitable.

These source-grounded route refinements resolve design choices, not runtime
obligations. The exact registered descriptor, protected CURRENT implementation,
crash/recovery state matrix and all async startup consumers still require
implementation and mounted evidence. Detailed cold route artifacts are
`/private/tmp/r14-initialization-split-route.md` and
`/private/tmp/r14-current-crash-route.md`; neither is empirical verification.

The subsequent cold design audit
`/private/tmp/r14-refined-admission-design-redteam.md` found no new confirmed
P0/P1 contradiction in this initial-root protocol. It explicitly retained the
implementation obligations: exact receipt successor operations, authenticated
database id in owner snapshot, full executable read-graph/descriptor registration
and applicable CURRENT transition scope. This is a bounded design result, not
full R14 closure. Command `.venv/bin/pytest -q
tests/composition/test_r14_startup_version_admission.py --tb=short` confirmed
**3 failed in 2.66s**: authentic V1 was still admitted with sidecar writes,
missing-lock input still recreated its lock, and fresh opener still yielded
without INITIALIZE. Those are the implementation targets, not accepted behavior.

## Physical schema observer gap closed (constructor scope only)

The existing materializer schema manifest omitted view/trigger objects and used
`LIKE 'sqlite_%'`: its underscore also excluded user objects named sqliteXhidden.
Six real closed-store reopen cases now cover ordinary and lookalike names for
index/view/trigger. Command `.venv/bin/pytest -q
tests/platform/test_sqlite_substrate.py::test_unregistered_schema_objects_reject_existing_store_without_repair --tb=short`
was RED: **5 failed, 1 passed in 0.26s**. The observer now includes all four
SQLite schema object kinds and excludes only the literal internal `sqlite_`
prefix with GLOB. All six reject while preserving original main-file bytes.
Expected fresh schema/DDL and the V1 AMR encoding are unchanged.

An existing substrate test helper also passed its fault value positionally into
the later-added expected_preimage_commitment slot. The ordinary atomic-publication
test was RED (**1 failed in 0.15s**, unexpected STALE); passing `fault=fault`
restores the intended fault/replay tests. Command `.venv/bin/pytest -q
tests/platform/test_sqlite_substrate.py
tests/platform/test_authority_checkpoint_admission.py --tb=short` passed
**36 tests in 0.69s**. After a context-manager lint correction, the six new cases
passed again (**6 passed in 0.09s**); Ruff and diff checks passed.
This strengthens existing constructor admission; it does not move that check
before constructors, register a tuple/read graph or prove source-SHM invariance.

## Existing current contract extensions registered separately

The three baseline convergence failures were reproduced on `0aa05a1`: command
`.venv/bin/pytest -q tests/capabilities/deployment_trust/test_trust.py::test_durable_record_discriminators_equal_the_freeze
tests/architecture/test_r4_r5_convergence.py::test_runtime_exports_equal_the_frozen_r4_r5_set
tests/architecture/test_r4_r5_convergence.py::test_runtime_record_schema_and_projection_sets_equal_freeze --tb=short`
gave **3 failed in 0.19s**. Runtime contained nine additional public exports and
the prepared external-grant/self-policy record kinds. There were no missing
historical exports or record kinds.

`architecture/current_contract_extensions.py` now declares only these existing
additions, representation kinds, full nested schema fingerprints, field order
and evidence selectors. Its literal registry fingerprint binds exact declaration
bytes. Current convergence requires the exact historical-plus-extension export
set, sorted disjoint record IDs and complete ownership union. No prefix/subset
exception or runtime discovery adds declarations. Historical R4/R5 shapes,
records, digests and R7 predecessor universe are unchanged.

Seven candidate JSON schemas were produced by TypeAdapter, then their required
fields, constraints, variants and nested definitions were reviewed against the
actual declarations and existing negative contract cases before freezing the
golden file. Protocol and exception expectations were written explicitly. Tests
compare full fixed schema bytes/digests, exported-model config, field order,
protocol parameter/return identities and read-only typed failure properties;
they do not regenerate expected values. JSON schema is not proof of behavioral
validators; those retain their own tests/evidence. No external verification or
running database-version authority follows from these shape fingerprints.

The new registry test was RED on its absent module (**1 collection error in
0.13s**). After implementation, the registry, current convergence and trust
files passed **58 tests in 0.87s**. Adding unchanged historical freeze, genesis,
H1 scope, grant-contract tests, actual policy reopen and unsupported-owner-route
controls gave **118 passed in 0.87s** with command `.venv/bin/pytest -q
tests/architecture/test_current_contract_extensions.py
tests/architecture/test_r4_r5_convergence.py tests/architecture/test_r4_r5_freeze.py
tests/capabilities/deployment_trust/test_trust.py
tests/capabilities/deployment_trust/test_genesis_contracts.py
tests/capabilities/deployment_trust/test_hermetic_output_scope_contracts.py
tests/capabilities/deployment_trust/test_prepared_external_delivery_grant_v2_contracts.py
tests/platform/test_prepared_self_delivery_policy_lineage.py::test_issued_policy_is_physically_anchored_current_and_survives_reopen
tests/capabilities/deployment_trust/test_output_scope_owner_seam.py::test_real_owner_route_remains_unsupported --tb=short`.

This closes the three historical/current convergence mismatches. Binding the
extension fingerprint into the complete registered descriptor and authenticated
CURRENT remains open (handoff 58, continuing 56). The three startup RED tests,
full SQL/non-SQL authority graph and running six-member tuple are still pending;
this registry grants no startup admission.

## Executable SQL inventory: closed snapshot table names

The candidate scan exposed a separate existing consumer gap in
`platform/authority_reads.py::_schema_members`: `NOT LIKE 'sqlite_%'` hid an extra
`sqliteXauthority` table, and observed names reached dynamic PRAGMA construction
before exact membership validation. The prior materializer observer repair did
not fix this reader. Actual SQLite tests against both snapshot APIs first gave
8 failures/1 passing V1-preimage control (`.venv/bin/pytest -q
tests/platform/test_authority_snapshot_commitment.py -k 'unknown_names or missing_registered or closed_schema'
--tb=short`,0.15s). Both APIs incorrectly admitted the lookalike table; other
failures demonstrated pre-validation schema introspection or wrong cause type.

The reader now uses the literal reserved prefix via GLOB, validates the complete
ordered table-name tuple before any table_info, then constructs PRAGMAs only from
the fixed authority storage members. BrokerAuthorityReader retains typed refusal
and no read-result release. The immutable V1 preimage/digest encoder is unchanged;
a literal vocabulary control covers BLOB, null, string and integer content.
Command `.venv/bin/pytest -q tests/platform/test_authority_snapshot_commitment.py
tests/platform/test_authority_reads.py tests/platform/test_authority_checkpoint_admission.py
--tb=short` passed31 tests in0.30s. This is table/column admission only; it does not
prove complete DDL/constraint/read-graph/AMR equality or mount startup admission.

Adding `tests/platform/test_scheduler_reads.py` exposed17 source-registration
failures alongside54 passing tests. A separate unmodified901aaaa worktree with
its own src on PYTHONPATH reproduced17 failed/23 passed in1.30s for that file;
its first refusal is historical V2b evaluator dependency changed for
`chiplog.capabilities.agent_loop.contracts`. Baseline stdout:
`/private/tmp/r14-schema-scheduler-baseline-901aaaa.out`. Handoff60 preserves
this separate integration failure; no historical pin was changed or bypassed.

The refreshed candidate inventory is
`/private/tmp/r14-sql-callsite-inventory-901aaaa.json`, SHA256
dab8392fc47247737891a507a92dfa422f183b8b31006198fee0c29a299dc34b.
Generator command `.venv/bin/python /private/tmp/r14_inventory_build.py` found507
candidates:350 actual SQL calls (329 literal/21 dynamic),53 SQLite factories,
97 snapshot-wrapper calls and7 domain/socket calls. Root independently compared
every source_files entry with SHA256 of current bytes:574 match, including this
working authority_reads repair;901aaaa alone does not identify that dirty source.
Root also inspected every dynamic site expression. Report:
`/private/tmp/r14-sql-current-inventory-route.md`.

This is complete only for the enumerated candidate AST names, not closed SQL
authority discovery. Caller-to-instance provenance, alias/reflection/import
closure, actual SQLite authorizer edges and exact assembly reachability remain
unproved. No RUNNING tuple or descriptor is registered. The next discriminating
negative is a real publication_sql predicate mutation adding a deletion_fences
subquery: extraction must observe the new frontier read before frozen graph
comparison rejects it. A source-hash mismatch alone cannot satisfy that test.

## First executable SQL read-edge extraction, not descriptor admission

`verification/authority_read_graph.py` now extracts the actual three direct
execute sites in `BrokerAuthorityReader._execute_planning_read`. It parses one
source string without importing/executing it, resolves literal/concatenation and
both finite conditional-string alternatives through unique dominating lexical
assignments, and rejects unsupported stores, expressions, receivers or execution
placement. Connection annotations select syntax only; they do not prove runtime
instance identity. Calls inside lambda/conditional expressions are not direct
sites. Unknown alias/reflection and whole-repository closure remain unimplemented.

Each alternative gets a new disposable SQLite schema connection with statement
cache disabled. EXPLAIN preparation plus explicit authorizer actions produces
the actual `(database,table,column)` set; predicates/subqueries and COUNT(*)'s
empty-column table dependency are retained. SQLite determines binding count,
including question marks inside literals/comments. A denied action, syntax or
binding failure refuses the entire observation; partial callbacks never become
a returned graph. No source authority database is opened and no runtime read
connection gains authority from this verifier.

The real publication_sql predicate mutant adds an EXISTS read of
deletion_fences.frontier while retaining authored actual_edges. The test first
observes that engine edge, then verifies the independent literal graph refuses.
Ghost expected edges/omitted branches also refuse. Lexical forward/conditional
definitions, receiver and SQL-local rebinding, repeated identical queries,
COUNT(*), extension/ATTACH/PRAGMA and incomplete preparation have separate cases.
The initial eight feature tests reached the explicit not-implemented API and
failed in0.06s; that was a missing-feature RED, not a mounted product failure.
Cold design review then found nested execution placement: two actual-source
mutants gave DID NOT RAISE in0.06s, repaired by the direct AST-parent check.
Command `.venv/bin/pytest -q tests/verification/test_authority_read_graph.py --tb=short`
now gives21 passed in0.13s. Review:
`/private/tmp/r14-sql-edge-extraction-design-review.md`.

This is one method's five per-site/alternative observations and literal edge
vocabulary, not a frozen full-source descriptor. It does not remove the existing
authored runtime check or mount startup admission. Schema is the existing trusted
main DDL supplied to the disposable engine; independent full physical-schema
goldens remain required. No tuple registration follows. The earlier507-site
inventory describes the pre-extractor snapshot; the new verifier itself adds
private-schema/EXPLAIN SQL sites. A fresh all-src AST scan over the same seven
candidate names now finds511 calls (execute348, executemany1, executescript8,
connect57, read_connection80, workspace_snapshot10, join_workspace_snapshot7).
Those meta-verifier sites must be classified too; no filename exemption is made.

## Full-src discovery accounting and instance-role correction

`verification/authority_sql_corpus.py` makes source accounting executable across
all `src/**/*.py`, without imports or source execution. Every ast.Call is retained;
terminal SQL names are review tags only. Imports, assignments/stores, parameters,
known bound-method references and opener declarations remain separate records.
Each record binds module, AST-enclosing class/function name, ordinal among all
recorded events in that scope, expression/shape and enclosing AST shape. Repeated
identical calls remain distinct; blank lines preserve structural identity while
changing separately retained byte digests. These are AST enclosure labels, not
proven Python lexical binding, lambda/comprehension or runtime call semantics.

Exact accounting compares both semantic records and source filenames, including
empty files, in both directions. Missing/added mounts/sites, changed binding,
renamed imports, callback aliases and dynamic callables cannot disappear from
the inventory merely because they lack a known SQL terminal name. That is not
alias resolution: every semantic receiver/instance/assembly obligation remains
unproved and require_closed has no acceptance path. Incomplete/duplicate/orphan
accounting also refuses; no caller-provided classification grants admission.

Initial9 feature tests failed on the explicit not-implemented API (0.15s), not a
mounted product bug. Two additional accounting cases exposed empty-file omission
and orphan source references (2 failed in0.04s), then were repaired. Cold design
review found the initial src/chiplog-only boundary; a temporary sibling package
with real SQLite calls reproduced DID NOT RAISE (1 failed in0.05s). The corrected
full-src walk passes with that negative. Review and closure:
`/private/tmp/r14-sql-corpus-accounting-design-review.md`.
Command `.venv/bin/pytest -q tests/verification/test_authority_sql_corpus.py
tests/verification/test_authority_read_graph.py --tb=short` gave33 passed in9.02s.

The root-generated current_sql_corpus export at base506a59a plus this working
source is `/private/tmp/r14-current-sql-corpus-506a59a.json`,41,457,057 bytes,
SHA256 b1f67a472b4c1280d3a185b4a8c23d1a842e81ce4c52f34c3912cbef628672d1.
Its command iterated corpus.source_digests and compared every entry with SHA256
of the current file:576 match. It contains107777 records, including56844 calls;
357 SQL-name candidates,154 factory/wrapper candidates,19 mount candidates and
670 reflection calls remain syntactic tags. No approved descriptor is generated
from those counts or hashes, and no tuple is registered.

The separate domain/mount audit is
`/private/tmp/r14-sql-domain-mounts-506a59a.json`, SHA256
3288fd8a56670fe4c386dda9d096a9236299a1ee2e85fa534197ef6bc8d78016,
with report `/private/tmp/r14-sql-domain-mount-route.md`. Root verified all575
source hashes in that earlier snapshot and read canonical opener routes plus
the actual CalendarReadLedger inheritance. Reproduction is
`python3 /private/tmp/r14-domain-map.py`:54 factories,97 wrapper calls,19 direct
mount forwards and44 connection-annotated helpers. Exact parameter/caller and
dynamic-import closure remains open, not inferred from those annotations.

Two concrete registration constraints follow from actual code: CalendarReadLedger
inherits factory-bearing BrokerReadLedger methods, so one SQL site has different
instance roles and cannot be assigned a domain solely by defining file/class.
Common CLI forwards a manifest parameter, so the runtime class/default does not
close its accepted assembly pairs. Next controls must discriminate an actual
main-path→reads-sidecar substitution and an existing manifest override before
mutable constructors; missing/ghost mount controls must compare both directions.
Separate registered per-domain schema/DDL/DML preparation and complete SQL-family
extraction remain mandatory. The discovery layer does not extend the SELECT-only
engine preparer, install a runtime authorizer or satisfy startup admission.

## Canonical constructor path forwarding

`verification/canonical_database_routes.py` extracts eight symbolic constructor
argument routes from the actual canonical opener and verifies the finite
ProductionPlanningStore path-holder source. The runtime database parameter is
the fourth positional argument. Main, trust SQL, read SQL, broker SQL and the
protected trust journal remain distinct. Both materializer factory alternatives
are checked. An independently written expected-route tuple rejects foreign roles.

The primary actual-source mutant changes ProductionPlanningStore(database) to
the reads-sidecar expression. Extraction first observes that foreign path and
retains it in the runtime's OR alternatives; policy comparison then refuses it.
Unknown expressions, missing/duplicate consumers, constructor substitutions,
rebinding and nested consumer frames refuse. Leaf import-time syntax is restricted
to its actual declarative forms rather than silently ignoring executable code.

The initial feature tests failed on the not-implemented API. Subsequent mutants
exposed constructor shadowing, same-module import aliases, leaf export replacement,
nested consumer frames, conditional class replacement and exception-target
deletion; each was reproduced as a failing test before repair. Cold static review
found two P1 defects (leaf export binding and nested consumers); its final bounded
recheck found both closed. Report:
`/private/tmp/r14-canonical-sql-domains-design-review.md`. The reviewer did not run
tests and this was not a fresh whole-component review.

Command `.venv/bin/pytest -q tests/verification/test_canonical_database_routes.py
tests/verification/test_authority_sql_corpus.py
tests/verification/test_authority_read_graph.py --tb=short` gave56 passed in9.79s,
including23 constructor-route cases. This proves only the supported source-level
forwarding slice. Callbacks, extra_leaves/supervisor effects, caller runtime and
manifest binding, downstream constructors, inherited instances and filesystem
identity remain unresolved. Broader global reflection/binding is unproved.
`require_closed()` always refuses; no tuple is registered and the three public
startup admission RED cases remain open.
