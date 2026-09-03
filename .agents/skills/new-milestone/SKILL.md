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
