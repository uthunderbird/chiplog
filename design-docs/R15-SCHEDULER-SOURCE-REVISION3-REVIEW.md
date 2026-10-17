# Scheduler V3 source revision 3 review

Revision 3 registers three formatter changes. Original V3 and revision 2
manifest bytes and registration digests remain unchanged. Current registration
uses actual formatted source bytes; historical admission recognizes only the
three exact whole-file hash pairs below. No generic formatting or AST
normalization is used at runtime.

## Reviewed source bytes

Pre-format bytes were checked against verified Git commit `87f9173` using
`git rev-parse --verify 87f9173^{commit}` and `git show 87f9173:<path>`.
Each formatted file has exactly the same Python AST as that source, comparing
`ast.dump(ast.parse(raw), include_attributes=False)`. The byte differences are
boolean-condition line joins, one blank line, exception parentheses and tuple
layout. No operation, guard, output or production deadline changes.

| Module | Historical SHA-256 | Formatted SHA-256 |
|---|---|---|
| `scheduler_mandate_lifecycle_contracts` | `f1fb581e0fe34d32ae0e45a0592b8e5792843c82fb5f47fd0d076554e195e365` | `aea44a2e955bea4026dae719e3753e5fa0372c62e64653726c0bce43d4aa0791` |
| `r15_interval_service` | `96a91c9cc34f2c216a935cbad8c06088169c27a8c8bae393b37013777a10f995` | `e30e268d3bca91dc9a94556ab62777f854e332c6d5bad2292ef15d69372604c0` |
| `r15_tick_runtime` | `a4522bf0df4a6abc78d7277a20dacc6381a7c253052a52824eb875f307c18be6` | `797a14de2280add3ac8829e27a99a8c683f53c519d7e8a3353f2c02366354ff5` |

The current manifest differs from revision 2 only at those source hashes.
Runtime, package inventory, lock, historical closures and generated schema graph
remain unchanged. Manifest SHA-256:
`384e474aa8260710972b7e447eadaeb35ea5d482fca8c10acff06b2a44ab7582`.

## Historical selection

Original V3 keeps rank 5 and registration digest
`b84bf53c30142dac8a8487341b9e64e6a3768e35b4393da7df0325056a25f1a5`.
Revision 2 keeps rank 6 and registration digest
`a5e10b466ae5f091808159882d007da4b4f6757d0fffdd872f0db85c4f4a19bd`.
Revision 3 has rank 7. Startup rejects regression between these registrations.
All historical manifests are authenticated before reconstructing registration.
The unchanged V2b closure passes through the same exact source matcher.

## Validation

RED after formatting, before compatibility:
`uv run pytest -q -p no:timeout tests/platform/test_scheduler_reads.py::test_registered_actual_producer_history_is_source_admitted`:
1 failed, specifically the lifecycle module's historical V2b source pin.

GREEN: `uv run pytest -q -p no:timeout tests/platform/test_scheduler_reads.py tests/composition/test_r15_lease_v2b_history.py`:
79 passed in 26.41 seconds. These include exact original/revision-2 registration
goldens, actual historical selections, all three immutable manifests, mutation
rejection on both sides of the source pairs, and forward/regressed ordering for
original→revision 2, revision 2→revision 3 and original→revision 3.

`uv run ruff check --force-exclude .` passed;
`uv run ruff format --check --force-exclude .` reported 1475 formatted files.
A direct `current_scheduler_registration()` observation returned
`scheduler-source-registry-v3:b51097a1129b121885fd037473b993851681a34ef94698865d25f462ca8e9c02`.
Mounted runtime confirmation on the final source remains pending.
This source revision does not establish whole-repository merge readiness or
deployment eligibility.
