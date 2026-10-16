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

## Executed preparation checks

Before and after changes, `sh .harness/scripts/test.sh --preflight` exited 0.
Command `.venv/bin/pytest -q
tests/capabilities/deployment_trust/test_genesis_contracts.py --tb=short`
passed **15 tests in 0.13s**. Scoped Ruff and diff checks passed. These tests
validate public immutable shapes, required explicit members, numeric types and
the fixed failure code/adoption status. No startup behavior is implemented or
claimed by this preparation. The V2/no-defaulting choice is recorded through
`decide.sh`; no running-tuple values were inferred from the fixture's integers.
