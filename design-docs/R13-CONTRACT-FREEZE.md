# R13 — durable production loop

Status: R13 offline implementation verified. Deployment HOLD.

## Implemented boundary

`composition.r13.open_r13_loop` is the production/evaluation entrypoint. Public DTOs
and Protocols live in `capabilities.agent_loop.contracts`. The service uses the
registered R13 owner graph, an inert hermetic model leaf, strict owned-static
promptstrings 1.3.0 rendering, the R8 recorder/Planning owner and the R12 workspace.
All canonical publications use the existing broker-owned EventAppender.

The public journey is proposal → immutable display → exact authenticated adoption
→ intent proposal → local receipt. The display retains the whole original R8 read
trace. Any intervening canonical frontier change requires redisplay and a new act;
an already committed adoption replays its original result. Model output cannot
authorize the Planning command, supply authoritative receipt wording or dispatch a
provider effect.

R12 receives accepted ingress and exact Planning/conversation source inventories.
Its complete issued workspace batch enters each Turn's visibility seal. A new
source cut closes the previous workspace instance. Content-addressed policy
bindings retain old registry snapshots. Planning projection accepts a sparse owner
slice only when R12 has independently verified the contiguous global tenant frontier.

CompleteAcceptance verifies the selected captured attempt and all earlier response
dispositions, renders supported assertions from actual committed owner evidence,
and atomically publishes Run success, accepted Turn/attempt, local delivery intents
and the R3 conversation companion. Both independent decision replay and normal
reads verify the complete companion membership. A pre-commit crash after DECIDED
can materialize that exact decision on restart; it does not rerun model/owner logic.

Cold reviews reproduced and closed historical-Turn omission, physical idempotency
key mismatch, partial receipt validation, uncovered prompt/schema bytes, lost
historical disclosure restrictions and automatic worker-generation rebinding.
The last witness now rejects the old loop after restart with no new publications.
These isolated reviews share a model family and are not external certification.

Base: local master `f8f0c395c318c0574cea4867984ef6001c6939be`, isolated
`feat/r13-production-loop`. Original worktree's modified TRANSCRIPTS.md and
untracked drafts/r13 belong to the user. They remain design-only v2, not executed
evidence and not inherited into this worktree.

## Plan and assumptions

Sources: IMPLEMENTATION-ROADMAP.md R13; PROJECT-ARCHITECTURE.md;
project-architecture/NORMATIVE.md model control, Run state machine, A105;
HEXAGONAL-CODE-LAYOUT.md; R8 and R12 contract freezes; TRANSCRIPTS.md and v1 compiler.

Prepare public contracts and a public consumer shape check, then implement the
durable core, adopted planning path, and hermetic scenario driver. Production and
evaluation enter the same canonical assembly. Historical R8/R12 fixtures keep
their component entrypoints. Recovery/scheduler/effect execution belongs to later
milestones; unsupported bindings fail closed. The first receipt attests only a
committed local planning result. Real provider/channel exposure remains HOLD.

Revised estimate: 40 files including tests, evidence, source inventories and the
R12 integration repairs (original estimate 35). The added surfaces are the shared
writer observer, content-addressed workspace binding, mixed-owner Planning projection,
and executable scenario/isolation fixtures. Semantic slices: contracts; durable core; adoption and
scenario; verification. Hard limit: 40 (.harness/scripts/thresholds.sh). Reassess
before exceeding the estimate; do not compress unrelated modules to evade it.
Commit was not authorized during implementation. After verification the user
explicitly authorized the R13 commit, integration into master and remote push.

Preflight passed before broad verification. Repeat after contracts, entrypoint or
fixture changes. Finish with lint, types, full tests and relevant verification
lanes over stable source bytes. Review affected source inventories before updating
their identities; a new digest is not evidence. Cold review of the revised plan
found zero critical blockers; shared-model review has an independence ceiling.

## Guarantee and requirement map

| Claim | Owned decision/data | Observable | Forbidden substitute | Boundary fixtures | Evidence surface |
|---|---|---|---|---|---|
| One loop, A09 / V2 | canonical assembly and ordered prompt/schema artifacts | equal realized owner partition and loop identity | eval-only orchestrator or delegated template | unknown tool, reordered specs, changed schema | `test_runtime_manifest.py`, `test_loop_prompts.py`, `test_loop_scenario.py` |
| Exact heads, A13/A16 / V5 | Run/Turn writer CAS and immutable events | rival/stale/terminal starts append nothing; restart retains budget | worker counters or cached joins | state matrix, rival start, budget boundary, physical identity mismatch | `test_durable_transitions.py`, `test_production_loop.py`, `test_loop_storage.py` |
| Attempt lineage, A105 / V5 | one slot lineage, immutable retry generations, monotone selector | old generation cannot emit/accept; possible emission never retries | replacing an old attempt or raw response | duplicate, stale proof/session, crossed emission boundary | `test_durable_transitions.py`, `test_production_loop.py` |
| Pre-emission visibility, A105 / V5 | accumulator and exact request/manifest seal | durable seal precedes emission; rejected raw trace influences later context | reconstruction from a smaller current projection | omitted/substituted source, wrong join, artifact-byte mismatch | `test_durable_transitions.py`, `test_loop_prompts.py`, `test_production_loop.py` |
| Adoption, A20 / V9 | immutable display and authenticated adoption through R8 | no Plan write before adoption; stale requires redisplay/new act | model output or generic assent | wrong peer, stale frontier, receipt substitution, replay | `test_loop_adoption.py`, `test_loop_scenario.py` |
| Atomic completion, A34 / V5 | writer-local exact continuation and evidence/disclosure/endpoint checks | success, accepted conversation and delivery intents appear together | schema-valid Complete or cached ancestry | semantic reject, before/after commit faults, duplicate/omitted history | `test_loop_storage.py`, `test_production_loop.py`, `test_loop_adoption.py` |
| Offline isolation, V7/V9 | registered hermetic leaves and unchanged default-HOLD entitlement | live worker network/file/process attempts denied, environment empty; local-only receipt | implementation PASS as exposure permit | unregistered provider fixture, old worker, network/secret canaries | `test_production_loop.py`, `test_runtime_manifest.py`, `test_loop_scenario.py` |

CompleteAcceptance deterministically normalizes exact origin endpoint selection,
validates assertion evidence/code entailment and exact current heads, checks the
complete disclosure closure and delivery manifest, and renders before atomic
publication. Every failed or unknown conjunct publishes no semantic success
records; definite rejection retains immutable labeled raw trace separately.
All prior sealed responses are enumerated from authoritative lineage rows and
their continuation joins recomputed inside the accepting writer transaction.

Authentication originates in provisioned peer state, never model/caller identity.
Owner-produced canonical bytes may receive transport framing only; downstream
code may not reconstruct domain authority or historical visibility.

## Reproduction and retained evidence

The new fixture is registered as `R13-LOCAL`; T01–T04 retain their identities and
scope. `fast` remains compile-only, and `stage1` remains R0–R12 component evidence.
The R13 driver is a boundary driver over the same service, not another agent loop:

```sh
mkdir -p .artifacts/r13/new-run
uv run python -m chiplog.verification.loop_scenario \
  design-docs/transcripts/local-planning-receipt.md \
  --database .artifacts/r13/new-run/loop.sqlite \
  --artifact .artifacts/r13/new-run/result.json
```

It refuses to overwrite retained databases/artifacts. The result retains source
and compiled-bundle digests, implementation catalogue and assembly identities,
reached fault observer, actual trace, immutable display/receipt and durable Run
records. `.artifacts/r13/verification-01/result.json` is PASS with deployment and
provider execution HOLD; its SHA-256 is
`2c477539ff1402c32ea383b299e23b2c08ee9318fde55dc3c2ba56ba5acbb8fd`.

Final verification:

- `uv run pytest -q --tb=short --maxfail=3`: **863 passed**, retained in
  `.artifacts/r13/verification-01/pytest.log`.
- After adding one explicit real-recipient negative parameter (no production
  code change), `uv run pytest -q tests/composition/test_production_loop.py::test_schema_or_semantic_reject_keeps_trace_without_success --tb=short`:
  **5 passed**, including the new canary. This supplements, rather than silently
  changes the count of, the full run above.
- `uv run python -m chiplog.verification stage1`: **PASS**, artifact
  `.artifacts/verification/cf34db3ca4d9b976fe2a1fdcd93e91c2781c5c880f8aeb2bead05169d481cdb2.json`.
- `.harness/scripts/test.sh --preflight`: **PASS**, including deliberately good/bad
  gate inputs and canonical corruption reproducer.
- `.harness/scripts/lint.sh`, `python3 .harness/scripts/checks/tests_layout.py --worktree`
  and `git diff --check`: **PASS**.
- Cold generation regression: old worker rejected; the entire before/after
  `LoopSnapshot` is equal. No confirmed P0/P1 remains in the reviewed slices.

Production source bytes stayed fixed throughout these final checks. The later
changes are this report and the additional negative test parameter; the Stage-1
artifact retains its original exact workspace identity, not a claim that those
report/test bytes were already present at invocation.

The preparatory 802-test core run is historical evidence only. The new adapter
mutation witness moved from
`tests/capabilities/agent_loop/test_durable_transitions.py::test_canonical_successor_cannot_drop_historical_turn_to_reset_budget`
to the same test name in `tests/platform/test_loop_storage.py`; the subsequent
targeted run passed without skip/xfail. No published predecessor fixture was removed.

## Explicit later-milestone boundaries

Scheduler/physical-root recovery, takeover/resume after possible model exposure,
provider effects and executed-effect receipts belong to R14–R18. Unsupported
states and destinations fail closed. R13 keeps typed non-authoritative text
separate from owner-rendered `LOCAL_PLANNING_COMMITTED` assertions. Its accepted
delivery intent is `PENDING_LOCAL`, not proof of provider/channel delivery. No
cohort-visible permit or deployment entitlement is created by these tests.
