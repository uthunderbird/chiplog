# R14–R17: continuation in a fresh thread

Prepared 2026-09-24 at the user's request after the thread exhausted its agent
creation limit. **R14–R17 remain OPEN; Phase C (contracts) remains OPEN.**
This is a continuation pointer, not replacement requirements or completion evidence.

## User instructions to carry forward

- Complete R14–R17 fully, without retrospective work.
- Preserve the order: all shared contracts → joint behavioral tests → one integrated
  implementation → detailed verification and extensions. Do not close individual
  milestones from isolated DTOs or happy-path examples.
- Common contracts/interfaces before delegation: `gpt-6-astra`, effort `low`
  (the API spelling corresponding to the user's “light”).
- Task design, test planning and additional problem analysis: a subagent running
  the project `$swarm-mode` skill with `gpt-5.6-sol`, effort `low`.
- Implementation: `gpt-5.6-terra`, effort `medium`, in subagents with detailed
  instructions. Parallelize only disjoint, dependency-safe work after shared
  interfaces are settled. Root owns and inspects integration.
- Intermediate feature commits run new tests only; no full regression, red-team
  or retro. Before merging into main/master, run full regression/integrity checks
  and cold red-team of the complete integrated diff. Existing authorization to
  commit, merge after checks and push persists.
- Use hermetic transports at real boundaries; no live provider sends.
- The user explicitly chose a fresh thread to preserve the specified models;
  do not silently substitute existing agents of unknown/different models.

## Exact starting state

Repository/worktree: `/Users/thunderbird/Projects/chiplog`.
Branch: `feat/r14-r17-integrated`.
Code checkpoint: `e982c72f8f02a1bf61280d34ec9a92a17189f0f7`
(`feat: join scoped dispatch with full effects lifecycle`). It was pushed and
matched `origin/feat/r14-r17-integrated` before this handoff was added.
The handoff commit, if present, follows that code checkpoint.

Commands used: `git rev-parse --verify HEAD`, `git branch --show-current`,
`git status --short`, `git worktree list --porcelain`, and
`git ls-remote origin refs/heads/feat/r14-r17-integrated`.
The tree was clean before this document. Other worktrees exist and must be
preserved; their work is not automatically verified or integrated.

## Read first

1. `AGENTS.md` (especially the user's checkpoint policy in section 7).
2. `design-docs/R14-R17-INTEGRATED-PLAN.md` — authoritative execution order.
3. `design-docs/R14-R17-INTEGRATED-CONTRACTS.md` — concrete contract inventory
   and outstanding joins; the top table is the remaining Phase-C map.
4. `design-docs/R14-R17-COMPLETION.md` and relevant normative requirements in
   `design-docs/IMPLEMENTATION-ROADMAP.md`, `project-architecture/NORMATIVE.md`
   and `VISION.md`. Historical progress is not current completion evidence.
5. `.agents/skills/swarm-mode/SKILL.md` for the Sol design task;
   `.agents/skills/new-milestone/SKILL.md` for contract preparation.

## What already exists

The feature branch contains new contract consumers and typed boundaries for
ingress state/transitions, admitted input initialization, executable cancellation,
post-terminal work, model-attempt recovery, read-only attempts, recovery/
continuation/completion, original-stream resolution/reduction, scoped effects
v3 intents, first SEND, all-child evidence/reconciliation and retry/reduction.
These are preparation contracts, not proof of completed runtime behavior.

The latest checkpoint's targeted effects consumers passed 32 tests
(`uv run pytest -q tests/capabilities/effects/test_scoped_dispatch_contracts.py
tests/capabilities/effects/test_lifecycle_transition_contracts.py`). It joined
v2/v3 intents into lifecycle contracts, allowed obligation absence before evidence
requires it, and kept first-SEND decision/child/parent hash dependencies acyclic.
Full regression was deliberately not run on that checkpoint.

## Immediate scheduler task and confirmed design constraints

Read [the corrected Sol design and test plan](R14-R17-SCHEDULER-NEXT-DESIGN.md).
It is proposed task input, with root review requirements and remaining Phase-C
decisions, not an approved contract freeze or permission to skip Astra's interface
review. Sol separated consumer shape checks from future behavioral evidence.

Subsequent continuation successfully ran `gpt-6-astra` low for the shared interface
review, then Terra creation and Sol follow-up were again rejected by the thread
limit. Read [Astra's concrete interface corrections](R14-R17-SCHEDULER-INTERFACE-REVIEW.md)
before using the earlier sketch. It supersedes conflicting sketch details, especially
the primitive decision source, separate V2 initialization result, N-Run absence
manifest, streamed overflow branch and complete new dependency manifest.
The next model-specific step is **Sol low with swarm-mode** on the review's remaining
decisions A–E, followed by recorded decisions and Terra medium implementation.
No source or tests were implemented in that continuation.

The old one-delivery policy in `composition/r15_tick_contracts.py` explicitly
authorizes pre-root materialization only, with no execution or SEND. Preserve it.
The new automatic path needs a separate bounded system mandate and service
identity; the scheduler must never impersonate the principal.

Read owner files under `src/chiplog/capabilities/agent_loop/`:
`scheduler_contracts.py`, `scheduler_preparation.py`,
`scheduler_materialization.py`, `scheduler_configuration.py`, and
`execution_initialization_contracts.py`; also
`effects/scoped_intent_contracts.py` under capabilities.

Cold plan review and direct code inspection established:

- Materialization is one **whole interval batch**, containing zero, one or N
  executable Runs. No-work/skip/overflow create none; coalesce creates one;
  materialize-each creates N. Independent per-Run publications are insufficient.
- First publication prepares the decision together with its Runs. Only replay/
  materialization of an existing decision may require already-selected decision
  evidence. Preserve tagged pre-root dispositions and exact replay bytes.
- Finite mandate budget numbers alone are insufficient. The contract must express
  one durable consumption/CAS head across cycles, restart and takeover.
- Existing `MaterializedOccurrence.run` is legacy `RunRecord`; the new path needs
  `ExecutionRunRecord` without weakening the legacy contract.
- Existing `PrepareScheduledExecution.source.materialization` contains a finalized
  `MaterializationCommitment`, which refers to initial Run/initialization/reciprocal
  records. Do not introduce a cycle by requiring that commitment to produce the
  very Run whose bytes it commits. Specify primitive inputs, finalized members and
  envelope hash domains explicitly before implementation.
- Clock and service authentication are broker observations, not caller-supplied
  authority. Initial publication uses the pre-root fence; later workers require
  the current exact root lease.
- Scoped effects acquisition needs a registered service-authority branch for
  permitted scheduled consequential calls. Duplicate-risk authorization remains
  explicitly human; no blanket broadening of the authority matrix.

Root review also rejected an artificial scheduler-versus-loop owner split:
both are `agent_loop`. A batch may contain multiple record families without
creating a new semantic owner. Any genuinely additional participant must be
justified from its registered ownership. The expected current database commitment
in an initialization cut must not be confused with the future scheduler output
commitment. Likewise, a proposed budget debit in an atomic batch is not already
selected evidence. Keep selected preimages separate from proposed successors.

## Remaining work after scheduler contracts

Continue the Phase-C table, including source-specific ingress bindings and durable
record decoding; nonempty page/drain consumers; conversation/input companions;
versioned history-tool response/artifact registration; successor participant and
terminal/work interpretation; concrete operation/record registry rows; complete
conversation/effects/work completion assembly; common CLI/Telegram driver receipts.
Then freeze the complete graph, plan/write joint tests through Sol/Terra, implement
all mechanism families together, and execute the required detailed histories.

## Checkpoint mechanics

Stage only the intended complete change. Use `CHIPLOG_COMMIT_MODE=checkpoint`
and `CHIPLOG_CHECKPOINT_TESTS` as a JSON array of relevant new pytest selectors.
Markdown-only commits permit `[]`. Keep no tracked unstaged edits at commit.
Inspect and summarize `.harness/scripts/handoff.sh` before committing; do not
bypass hooks. Cold plan review remains distinct from waived checkpoint red-team.

`src/chiplog/inert_shared/r8-implementation-v1.json` pins source bytes. Update only
actually changed/new reviewed paths, inspect the exact catalog delta, and verify
retained entries still match. Never repin the entire catalog to silence failures.
Run relevant consumer tests and targeted static checks; full regression belongs
to final integration. Push the feature branch and verify its remote SHA.

## First message in the new thread

> Продолжай полное закрытие R14–R17 по
> `design-docs/R14-R17-RESUME.md`. Сохрани заданные модели и порядок
> контракты → тесты → общая имплементация → детали. Начни с оставшегося
> scheduler-контракта и общего графа интерфейсов; не пересоздавай сделанное.
