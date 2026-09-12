# R12 — Stage-1 workspace integration

Status: implementation and regression checks passed; final cold review has zero
confirmed P0/P1. The content-addressed Stage1 artifact records the final profile
verdict for this frozen workspace; deployment eligibility remains HOLD.

Base: local master `db21a4edf00b7c2fa3c128e8cf1834c7a677eb1f` in an isolated
`feat/r12-workspace` worktree. Dirty master files belong to the existing session
and are not inherited. Scope estimate: 25 files including tests, inventories and
this evidence artifact; hard limit is 40 (`.harness/scripts/thresholds.sh`).

Sources: IMPLEMENTATION-ROADMAP.md R12; project-architecture/NORMATIVE.md Stage 1;
R9-R11-CONTRACT-FREEZE.md and the individual R9/R10/R11 freezes; src/AGENTS.md,
tests/AGENTS.md and HEXAGONAL-CODE-LAYOUT.md.

## Plan and assumptions

The canonical component entry is `composition.r12.open_r12_workspace`. Stage1
fixtures consume that public entry. Historical R8 CLI fixtures continue to use R8;
no R13 agent loop or real-provider exposure is introduced. Existing R9 dashboard
semantics and owner public ports remain the component contracts. R12 adds the
transaction-scoped read composition and journal/calendar family bridges.

Prepare public immutable batch/proposal contracts and a public consumer shape
check, then implement the shared SQLite read scope, component bridges, closed
composition inventory and Stage1 verifier. Run preflight, component boundary
mutants, Stage0/Stage1 and full lint/type/test checks. Cold review reads actual
result; repair confirmed blockers. No commit is authorized yet.

Authentication is independently provisioned hermetic transport peer state, not
caller DTO fields. Internal reads share a pinned SQLite transaction including the
tenant head, deletion fence and broker-authored durable policy binding. The binding
is compared with independently provisioned current peer/policy/source metadata;
it does not invent a policy decision or grant production authority. Release checks current durable state and exact
peer binding; stale work returns no content and never obtains a replacement cut.
External calendar reads retain their broker context and observation provenance;
they are evidence, never an internal authority upgrade. Journal retains ownership
of canonical records, statuses, exact displays and admission decisions.

## Guarantee and requirement map

| Claim / requirements | Owned data/decision | Independent observable | Forbidden substitute | Boundary fixtures | Evidence |
| --- | --- | --- | --- | --- | --- |
| Shared cut, A24 | transaction-scoped connection, physical file identity, tenant head, fence and peer policy | writer committed between family reads; internal readers retain one snapshot; release rejects change | identical caller frontier strings, reacquisition | stale/race, substituted path/context/head, omission/extra query | tests/composition/test_workspace_batch.py; HOLD |
| Read before proposal, A19/A21 | complete bounded history and exact issued batch | proposal unavailable before read, on incomplete history or after invalidation | model-attested history, latest-tail truncation, forged batch | absent/foreign/modified/replayed cut, N/N+1 | tests/composition/test_workspace_batch.py; HOLD |
| Separation, A22/A30–A32 | owner JournalPort and JournalQueryPort | aligned T02 writes journal, unchanged durable Plan; provider remains observation | journal-to-Plan, provider-to-Fact, stale display wording | negation/direct affirmative/confirmation, current-head race | tests/composition/test_workspace_journal.py; HOLD |
| Disclosure, A27 | canonical owner payload and complete source envelope | restricted marker preserved in history, family screens and proposal context | omitted source, alias, digest substitution, weaker label | omission/addition/substitution/duplicate/reorder/foreign identity | tests/composition/test_workspace_batch.py; HOLD |
| Bounded dashboard, A21/A24 | R9 navigation/LRU/budget and immutable screens | all four families, conversation outside LRU, explicit provider lag | projection as authority, background promotion | N/N+1 and canonical row order/lineage closure | tests/composition/test_workspace_batch.py; HOLD |
| Closed executable evidence | Stage1 exact registered test slices and source identity | mutant source or omitted slice fails; exposure remains HOLD | green historical R8 alone, repinning unchecked bytes | unknown/omitted/substituted surfaces and registry | tests/verification/test_stage1.py; HOLD |

All mutation families apply somewhere in this map. Numeric bounds include strict
order, cursor progress and complete lineage; a consequential proposal requires
complete history within its bound. Owner bytes are retained; adapters may add only
typed framing and a digest/envelope for that framing. R8 source inventory updates
follow source review and precede final verification; final runs read stable bytes.

Cold plan review: zero critical/blocking findings; clarified canonical entry,
source-bound inventories, total diff estimate and actual transaction/release cut.
This is bounded same-model review, not independent deployment entitlement.

## Implemented boundary and review repairs

The batch carries its internal context, exact policy-binding digest, complete
history and owner-framed planning/journal rows, plus an independently issued external
calendar context. Calendar dashboard framing is always explicitly LAGGING; its
original context remains available in the batch. History tools are channel-scoped;
proposal evidence reads the complete tenant conversation through the R9 agent port
and rejects a history exceeding the requested bound. Calendar detail reuses the
issued external cut. Navigation and configurable budgets use the R9 registry and
builder, and background refresh preserves focus/navigation.

Cold mechanism review reproduced two blockers: an unchecked bound calendar port
could omit observations, and a supplied planning source map could replace the owner
publication closure with a conversation source. Repairs check the port's class,
instance, broker and peer, and compare every planning source with the complete
actual publication record set, physical bytes, version and digest. Reached tests
exercise both witnesses and omission/duplicate/reorder/source substitutions.
Module-level semantic/disclosure helpers and public journal wrappers are also
checked. Journal framing retains the full owner row and joins all lineage sources
and labels, including stricter ancestors.

R3 conversation reads now distinguish the deletion-fence frontier from the tenant
commit frontier. The canonical workspace uses the actual fence epoch (initially
zero), not an artificially large deletion frontier. `read_connection` joins the
active transaction for conversation, journal and planning, while historical callers
retain standalone transactions. An SQL trace fixture observes policy and all three
internal readers on the same physical connection; a separate fixture commits an
independent writer between reads and observes the original snapshot.

External calendar derivatives retain their complete envelopes in immutable display
storage. They have no local canonical record IDs and are not registered as fabricated
R3 sources. Every public R12 reuse/read checks the current local fence and external
ledger state; no unguarded screen replay is exported by this composition.

Requirement scope: A19/A21/A22/A24/A26/A27/A30–A32 are exercised by the composed
fixtures. A25 uses the current-fence rejection plus inherited R3/R9 deletion evidence.
A23's existing single canonical EventAppender is reused; late-evidence lease
behavior remains inherited Stage0 evidence. A29 adds only the fixed
`workspace.policy` broker metadata publication and existing owner command paths.
A28 introduces no new transport acknowledgement surface: the calendar leaf is
hermetic and read-only. Production Run/Turn orchestration, model prose, real
provider/channel exposure and deployment eligibility remain outside R12.

Verification completed on the implementation: `sh .harness/scripts/test.sh`
passed all 778 tests; `sh .harness/scripts/lint.sh` passed Ruff, formatting and
strict mypy checks. Preflight, resource-context and test-layout checks passed.
The boundary fixtures in the requirement map above have executed successfully;
their initial HOLD markers refer to the final aggregate profile, whose verdict is
recorded separately rather than inferred from component tests.

Final cold review read the actual staged code and normative requirements without
author context: zero confirmed P0 and zero confirmed P1. Its focused runs passed
43 tests and 5 tests (overlapping selections). The earlier two confirmed blockers
were repaired and their reproductions remain in the regression suite. This review
is bounded evidence for the hermetic component, not production isolation proof.

Final evidence commands: `sh .harness/scripts/test.sh --preflight`,
`sh .harness/scripts/lint.sh`, `sh .harness/scripts/test.sh`, and
`uv run python -m chiplog.verification stage1`. Stage1 includes the complete Stage0
aggregation and exact R9–R12 slices; artifacts are content-addressed under
`.artifacts/verification/` and bind the actual workspace/index/source bytes.
