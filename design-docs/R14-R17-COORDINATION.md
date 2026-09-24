# R14–R17 parallel implementation

Status: HISTORICAL COORDINATION PLAN, superseded 2026-09-24 by
[R14–R17 integrated plan](R14-R17-INTEGRATED-PLAN.md). The current work has one
joint mechanism and the order contracts → tests → implementation → detail expansion.
The old per-milestone worktree/retro sequence below is retained as history, not an
active instruction. The user excluded retros; existing commit/merge/push authorization
persists subject to integrity checks. No milestone or deployment claim.

## Plan

1. Inspect normative requirements and existing owner/broker boundaries; freeze common
   identities, public DTOs, failure variants and consumer-side contract checks before logic.
2. Work in `chiplog-r14` through `chiplog-r17`, branches `feat/r14` through `feat/r17`,
   initially based on verified commit `6f34dafc750ba894bbc7dd8a5a2659f61c87675e`;
   all four now include approved shared contract commit `0168d61`.
   Root owns R14, shared contracts and final integration; delegates own R15–R17.
3. Distribute root-owned shared paths as versioned binary patches, including added files,
   with path/content SHA-256 manifests. Check applicability before applying. Freeze these
   paths for delegates; revisions require renewed consumer checks. Deduplicate shared
   changes at integration. No temporary unapproved commits.
4. Establish an R14 recovery integration baseline, then integrate downstream writers and
   ingress paths. R14 A99 convergence and R17 ingress coverage require actual downstream
   executable paths, bidirectional discovery and reached negative mutants. Shape tests
   establish a usable interface, not milestone completion.
5. Execute each full roadmap DoD, preflight after boundary changes, lint/types and relevant
   verification over stable bytes. Inspect source-bound catalogs before any digest update.
6. Run a separate `retro` for each worktree with unique milestone ID. Preserve script inputs
   verbatim, attach current diff evidence when implementation is uncommitted, run the swarm
   and cold review, and serialize shared harness repairs. Retros remain incomplete until
   their required commits are explicitly authorized and made.

## Assumptions

- Original master's modified TRANSCRIPTS.md and untracked drafts/r13 are user-owned;
  they remain untouched and do not become executed evidence.
- Real provider/channel exposure remains HOLD; fake adapters exercise effect boundaries.
- Budget is the UNION of dirty paths per worktree, including common contracts, evidence,
  tests, handoff and retro. Initial target is at most 35, hard guard is 40. Reassess from
  actual inventories and request approval for a ready semantic commit before exhausting
  the limit; do not shrink acceptance criteria to fit it.
- Commits require explicit approval after concrete results and handoff are presented.
- R14 core readiness does not close R14 before downstream writer coverage; R15–R17
  recovery completion requires integrated R14 evidence.

## Cold plan review

An isolated reviewer read the roadmap and process scripts. The initial review found
file-budget double counting, unspecified contract transfer, downstream writer/ingress
coverage gaps and precommit retro evidence gaps. This plan incorporates all five fixes.
The subsequent review returned no remaining critical blockers of the coordination plan.
This is same-model review, not independent certification or implementation evidence.

## Responsibility

Root retains ownership, reads and repairs shared artifacts, verifies executable integration
and reconciles conflicts. Delegate reports alone do not establish completion. Each milestone
contract document must map every requirement and mutation family to inspectable evidence.
