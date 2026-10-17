# Scheduler V3 source revision 2 review

This revision admits the current producer while preserving selected V1 through
original V3 history. It does not change historical manifest bytes or their
registration digests. The new current V3 source revision has rank 6; original
V3 retains rank 5. The wire profile remains V3.

## Actual artifact differences

`_observe(v3=True)` was inspected in real mcp-debugger sessions. Runtime and
serializer package inventories match the original V3 profile after excluding
Finder `.DS_Store` metadata. Ordinary artifact changes and all non-cache
symlinks remain rejected. The runtime mismatch was reproduced by a RED test
and the restored runtime hash was observed in the debugger.

Only `sources` and `lock_sha256` differ between the old and new reviewed
profiles. The new profile uses actual source bytes and the complete current
lock hash; historical compatibility projections never replace `_observe()`.

- `chiplog.capabilities.agent_loop.contracts`: `8073d9d47b9a8edc118c3e79888c0128c2a3651f5c20fc855db801d48a60c0fd` → `5530f05cdc6327fe79f3ba393ffb93c2d9954a6584321a0a408e12c183e5b9bf`.
- `chiplog.platform._owner_publication_contracts`: `f5fb4bbab99bca510926f9d6487dbb44e890fb8fcfd553ebed625efc22e88de6` → `d3c156b704af5b1fe45145c668a1c3067f31bde28592c5d969fa98e561bdfbbd`.

The complete V2b closure was independently recovered from verified Git commit
`f722066d6d246509f907f24c8620d5fad15f80ab`; all 40 recovered sources matched
unchanged closure pins. The live diff changes three modules: inert `LoopHold`,
exact `Final` annotations on existing constants, and the already-supported
interval-service operation addition. Compatibility removes only those exact
reviewed byte sequences and still requires each original source hash.
No generic annotation or AST normalization is used for these additions.

The original V2–V3 lock was recovered from the same commit and is authenticated
before comparison. Only `orjson`, `psycopg`, `psycopg-binary`, and `tzdata` package
entries plus the exact two new project dependency/requirement entries are
projected out. The entire remaining parsed document must equal the historical
lock, with duplicate package names rejected. Current registration still pins
the full actual lock. The separate V1 archived lock remains unchanged.

## Asset hashes

- `src/chiplog/composition/scheduler_source_manifest_v3.json`: `36041f23cf61e8834cfe3ac89ce7e884f1224f68be9e9694f501950e719a8ca2`.
- `src/chiplog/composition/scheduler_source_manifest_v3_revision2.json`: `5d01204db2e1bf02c02fbd815d682a4d707b66d88ca34ea95fe13e636bdc7eec`.
- `src/chiplog/composition/scheduler_v2b_sources/uv.lock.source`: `e2dfa2959958ab82de7a1f690bf97da3cfe429697b032a9a85ee45993cca3f8f`.

## Observed validation

- RED: `uv run pytest -q -p no:timeout tests/platform/test_scheduler_reads.py::test_registered_actual_producer_history_is_source_admitted`: failed at historical `agent_loop.contracts` admission.
- GREEN: `uv run pytest -q -p no:timeout tests/platform/test_scheduler_reads.py`: 63 passed, including original-V3 digest retention, source/lock mutations, immutable manifests and revision-order checks.
- `uv run pytest -q -p no:timeout tests/composition/test_r15_lease_v2b_history.py`: 5 passed, including actual committed V2b/V2c selected bytes and evaluator tampering rejection.
- `uv run mypy src/chiplog/composition/scheduler_source_registry.py`: no issues.
- Mounted interval-service runtime validation is still running; its SKIP case has failed and the terminal traceback is pending. This checkpoint does not close that runtime failure.

This is a checkpoint review, not final merge acceptance. Full regression,
strict R8 inventory, remaining static diagnostics and final review remain open.
