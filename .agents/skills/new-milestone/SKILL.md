---
name: new-milestone
description: Prepare project context and contracts before implementing a new milestone. Use when a milestone begins or materially changes; do not implement business logic in this skill.
---

# New Milestone

## Invariant — gather context

This is the first stage of the default milestone workflow. Before implementation,
inspect the repository information that can change the milestone's scope, constraints,
interfaces, evidence, or acceptance criteria. Derive the relevant sources from the
requested milestone and current repository state; do not assume a particular roadmap
or earlier milestone structure.

Compose the findings into compact task-specific context containing:

- goal and boundary;
- relevant evidence and source paths;
- constraints and existing decisions;
- acceptance criteria;
- unresolved items.

Resolve anything decidable by inspection. Record decisions as required by the project's
process. Treat missing or contradictory information as a blocker only when inspection
cannot choose between named viable branches; report those branches and the human decision
required.

### Meaningful guarantees preflight

Before materializing contracts, expand every meaningful guarantee claim (for example
`exact`, `same`, `bounded`, `authenticated`, or `owner-only`) into a compact evidence row:

`claim → owned decision/data → independent observable → forbidden substitute → boundary fixture → planned evidence path`

Select every relevant mutation family: omission, addition/unknown value, substitution or
alias, duplicate/reorder, stale/race, boundary N/N+1, and logical/physical identity mismatch.
Mark a family `N/A` only with a concrete reason. For a bound, specify progress/order and
referential closure as well as the numeric limit. For ownership, name the exact canonical
bytes or decision, permitted downstream fill-ins, and forbidden reconstruction. For
authentication, name the independent identity source and a mismatch witness. For sameness,
name the identity and temporal cut shared by validation and use.

Write these rows and a requirement-to-evidence map into the milestone artifact before
business logic. Planned evidence remains `HOLD` until executed. This preflight complements,
and does not replace, the later cold promotion review.

## Invariant — define contracts before logic

This is the second stage of the default milestone workflow. Before business logic,
materialize the boundary artifacts required by the gathered context: protocols or
interfaces, DTOs, models, schemas, and structural stubs where relevant. Define their
ownership, public visibility, inputs, outputs, and failure variants without implementing
the behavior behind them.

Validate the boundary from a consumer's point of view with the smallest useful
compile, import, or shape test. That consumer must use only the intended public surface;
private imports and implementation substitutes do not prove the contract. If the
boundary cannot express a gathered constraint, revise it or surface the decision before
continuing.

Stop after the contracts and their consumer-side validation exist. Do not implement
business logic in this skill.
