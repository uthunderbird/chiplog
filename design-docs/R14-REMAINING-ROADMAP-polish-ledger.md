# Polish ledger — R14 remaining roadmap

Target: `design-docs/R14-REMAINING-ROADMAP.md`
Restore point: target as created in this working tree; no pre-existing tracked
version. Cost announced: 9 angle attacks + coverage audit + final regression sweep;
initial ceiling 12. Coverage audit added one defect-class angle, so the scaled
ceiling is now 13 cold passes. Every pass is a no-parent-context `gpt-6.1-sol`/low
subagent. Scope is this roadmap and its contract/source grounding.

| Pass | Angle | Result | Repair / note |
|---|---|---|---|
| 1 | Sequence and dependency correctness | NEEDS-REPAIR: 2 P1 | Removed read-only accounting dependency cycle; kept independent model-attempt work unblocked. |
| 2 | Current source grounding and decision boundary | NEEDS-REPAIR: 1 P1 | Bound-gated allocation now explicit for all frozen lineage/pending constructors. |
| 3 | Requirement coverage / normative traceability | NEEDS-REPAIR: 3 P1, 1 P2 | Added A54 limits, A57 CAS branches, A99 pre-root algebra, A50/A55/A59 terminal atomicity, A108 selected-attempt continuity; added final evidence matrix. |
| 4 | Execution feasibility and dependency graph | SURVIVES: 0 P0/P1, 1 P2 | Cold retry confirms the limited R14.7 successor seam resolves the dependency; P2 says explicitly state whole mixed fanout HOLD/no partial publication and no lineage before bound. Recorded, not changed. Next angle: terminal Run/accounting/work atomicity. |
| 5 | Scope and authority alignment | SURVIVES: 0 P0/P1 | Cold retry confirms remaining-R14 boundary, frozen contracts and user-owned decision remain intact. |
| 6 | Factual and attribution accuracy | SURVIVES: 0 P0/P1, 2 P2 | Numbering, source paths/symbols, normative summaries and mounted-evidence descriptions verified. P2 notes: prior review provenance has no durable locator; prior commit authorization is conversational provenance. Recorded, not changed. |
| 7 | Internal consistency and terminology | NEEDS-REPAIR: 1 P1, 3 P2 | Fixed P1: the pre-bound witness preserves an existing selected ACTIVE Run while requiring no new read-only call, lineage or pending record. P2 recorded: branch-conditional dependencies, “one dependency chain” wording, and “no remaining HOLD” completion phrase. |
| 8 | Actionability and test observability | SURVIVES: 0 P0/P1, 2 P2 | Concrete source seams, positive observations and discriminating negatives verified. P2: explicitly name the mounted SUCCESSOR_PENDING witness and the horizontal scenario that returns heavy checks from backlog; recorded, not changed. |
| 9 | Original R14 / normative clause completeness | NEEDS-REPAIR: 2 P1, 1 P2 | Added explicit successor obligation classification/immutable observations/action-lineage guard (A43/A57), and authoritative current post-terminal recovery view/closure replay/admission checks (A59). Recorded P2: bind pending-creation to changed frozen/current binding. |
| 10 | Coverage audit (T1) | INCOMPLETE | Added angle: mechanism sufficiency and adversarial lifecycle composition. |
| 11 | Mechanism sufficiency / adversarial lifecycle | NEEDS-REPAIR: 1 P1, 1 P2 | Distinguished permitted absent/exact-prefix physical materialization from incomplete/unavailable decision commitment; added crash/reopen exact-prefix completion, replay and missing-commitment negative witness. P2: add authentic-but-foreign cross-owner substitution witness if relevant to each boundary; recorded. |
| 12 | Coverage audit (T1) | COMPLETE | No uncovered applicable defect class in the expanded review set. Bounded coverage judgment; not runtime proof. |
| 13 | Regression sweep (T2) | SURVIVES: 0 P0/P1, 2 P2 | Final cold sweep found no residual P0/P1 or repair regressions. P2: clarify branch-conditional dependencies; explicitly plan writer-registry startup/runtime-generation equality checks. |

Known open product decision: read-only `max_attempts`, explicitly identified in
the target as user-owned and out of scope for the roadmap to choose. This does not
count as a polish-held finding; the document preserves it as an implementation
gate. Ratified P0/P1 author-decision findings: none so far.

Terminal bar reached relative to the angles above: every listed angle covered,
coverage audit COMPLETE, final cold sweep with zero P0/P1, no polish-held author
decision, and no repair after that sweep.
