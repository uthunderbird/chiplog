# Chiplog — planning / journal split

> This retained proposal is now a document set. The copied source blocks below are unchanged;
> status and provenance apply to the whole set. The original location remains a compatibility index.

## Document map

| Reader task | Canonical document |
|---|---|
| Understand status, provenance, boundary, open decisions, or history | this file |
| Look up record shapes and structural invariants | [SCHEMA.md](SCHEMA.md) |
| Review Plan/Fact semantics and natural-language contracts | [SEMANTICS.md](SEMANTICS.md) |
| Implement invocation and external-effect safety | [EFFECTS.md](EFFECTS.md) |
| Review platform security assurances | [ASSURANCE-SECURITY.md](ASSURANCE-SECURITY.md) |
| Review deletion, disclosure, and privacy assurances | [ASSURANCE-DATA.md](ASSURANCE-DATA.md) |
| Prepare operational adoption | [ADOPTION.md](ADOPTION.md) |
| Read worked examples and task traces | [EXAMPLES.md](EXAMPLES.md) |
| Run or inspect conformance vectors and triage | [CONFORMANCE.md](CONFORMANCE.md) |

## Status

**SOURCE FOR A CORE SELECTIVELY ADOPTED INTO THE LONG-TERM VISION — 2026-08-21.**
`VISION.md` reproduces the exact adopted product baseline and is its sole normative
source. This standalone is retained design rationale and a proposed detailed
realization. Later edits here do not alter product direction unless the baseline in
`VISION.md` is explicitly revised.

The material incorporated there originated in: `Boundary`; the two adoption
manifests; P1-01–P1-02 at product-semantic level; the Core natural-language
interaction contract; `Did not swim Tuesday`; `Swam at 10, not 8`; and the
task-specialization traces. `Delayed invocation after replanning` was excluded
because its dispatch, ambiguity, retry, and effect behavior depends on proposed
P0-05–P0-06 machinery. This list records provenance; it does not dynamically import
later standalone text.

P0-05–P0-06 implementation/effect machinery, adoption-assurance annexes A–C,
operational feasibility profiles, rollout gates, and conformance claims remain
**PROPOSED — NOT OPERATIONALLY ADOPTED** until their named external policies and an
approved `OperationalAdoptionProfile` exist. The Boundary's logical-deletion rule is
represented in the vision only at product-semantic level; deletion enforcement and
assurance remain proposed. All storage, CAS,
transaction, serialization, policy-registry, enforcement, effect, and
operational-realization clauses remain proposed even when they share a paragraph
with an outcome later incorporated into the vision. No same-context polish or terminal zero is
external verification of implementation, safety, economy, or legal compliance.

The hypothesis is layered, not bidirectional. Planning is the sole authoritative
normative state. The epistemic history preserves typed, fallible claims. A fact claim never
directly projects into, mutates, closes, cancels, or otherwise changes planning.
Evidence can affect planning only by becoming input to a new explicit authorized
planning command which passes validation and commits a new planning revision: the
command-appropriate member(s) of the closed `PlanningRevision` union. This includes
task, occurrence, series/rule, authority/grant, delegation/acceptance,
commitment/arrangement families enumerated by the proposed `PlanningRevision` union.
A claim never mutates any revision family directly. Adding another normative family
changes this proposal but has no adopted product effect until `VISION.md` is
explicitly revised; registration alone cannot expand the vision's closed vocabulary.

“Sole normative authority” concerns product intent and obligation state. Security,
deletion, audit, and operational records in the assurance annexes are control-plane
preconditions on safe execution; they do not express user intent, satisfy an
obligation, or derive a plan from a claim.

## Boundary

A plan describes an expected, desired, permitted, or obligated future. A fact claim
describes what a source asserts occurred or was observed. Planning owns current
authority, acceptance, constraints, commitments, schedules, and normative state.
The epistemic claim history owns immutable claims, receipts, observations,
corrections, disputes, and artifacts. The journal is a disposable narrative view
that combines them with labeled plan context only for presentation.

The permitted causal path is:

```text
claim → proposed planning command → validation → new planning revision
```

There is no reverse projector. No claim arrival, timestamp, confidence, provider
receipt, or narrative computation selects the current plan. No later plan can
create, delete, re-subject, or redefine a historical occurrence or claim.

Privacy deletion is the sole qualified exception to payload preservation: it may
irreversibly erase authorized semantic payload but cannot rewrite, re-subject, or
replace it with another historical assertion. The survivor is only a policy-minimal,
non-reconstructive redaction proof and ordering placeholder.

## How to read this document

The product-semantic proposal begins with the Plan/Fact boundary, the two manifests,
P1-01–P1-02, the Core natural-language interaction contract, the two named Plan/Fact
correction examples, and the task-specialization traces. Their adoption scope is
defined only by what `VISION.md` reproduces. P0-05–P0-06 and `Delayed invocation
after replanning` are proposed effect-safety machinery. Platform security, deletion,
external disclosure, and operational rollout are adoption-assurance annex contracts:
they constrain implementation without adding product-state authorities. They remain
inline so this standalone artifact is self-contained, but are not part of the
Plan/Fact ontology.

## Proposed product-semantic schema manifest

This closed manifest—not the physical extent of `Canonical schema`—defines the
standalone's proposed typed model. It records provenance for, but does not expand,
the smaller type-role baseline reproduced in `VISION.md`:

- normative identities and revisions: `Task`, `TaskRevision`, `TaskOccurrence`,
  `TaskOccurrenceRevision`, `TaskSeries`, `TaskSeriesRevision`, `RuleRevision`,
  `PlanExpectation`, `AuthorityGrant`, `AcceptanceRequirement`,
  `AcceptanceSatisfaction`, `Delegation`, `DelegationSetRevision`,
  `CommitmentRevision`, and `ArrangementRevision`;
- normative union and subjects: `PlanningRevision`, `PlanSubject`,
  `DelegationSubject`, and `AcceptanceSubject`;
- recurrence and transformation identity: `DueSpec`, `RecurrenceCoordinate`,
  `RecurrenceBinding`, `RecurrenceSlotDisposition`, `OccurrenceLineage`, and
  `OccurrenceTransformation` at the product-semantic level;
- epistemic identity and history: `ClaimSubject`, `WorkOccurrence`, `FactClaim`,
  and `ClaimDisposition`;
- conversational governance: `NaturalLanguageInteraction`,
  `NLCommittedResult`, `NaturalLanguageInterpretationRevision`,
  `PlanningProposal`, `ProactivityRule`, and `ProactivityRuleRevision`;
- reliance classification: `Commitment`, `Arrangement`, and
  `RelianceAssessment`;
- typed graph and narrative: `StructuralParentEdge`,
  `StructuralParentEdgeRevision`, `NodeRelation`, `NarrativeEpistemicStatus`, and
  `NarrativeView`, with the endpoint, lifetime, status, ordering, and presentation
  meanings specified by P1-01–P1-02.

The names, closed variants, stable identity/revision relationships, subject
boundaries, state meanings, and provenance meanings of the displayed fields on
those types are proposed here. Physical pointers and indexes, `commit_seq` allocation,
CAS/transaction strategy, deletion epochs, policy-registry records, and enforcement
algorithms are proposed realizations. Every schema type not listed above—including
security, dispatch/effect, deletion, disclosure, and operational-adoption records—is
outside this proposed semantic manifest. A reference from a listed type to a proposed policy
record pins proposed semantics or provenance; it does not adopt that record's
storage or enforcement design.

## Limitations and open decisions

The core incorporated into the vision does not choose claim registries, source-admissibility, strength or
conflict policies, the exact `DUE` boundary convention, recurrence horizons,
compatibility details, partial-
outcome aggregation, parent/delegator satisfaction policy, retention periods,
propagation SLAs, privacy budgets, recovery operators, audit audiences, or user
vocabulary. Selective incorporation into the product vision does not prove economy, implementation,
migration feasibility, operational adoption, or external verification. Missing
policy fails closed; assurance adoption remains an author decision.

## Revision ledger

| Sections changed | Rules encoded | Vectors added | Contradictions removed | Residual open policy knobs |
|---|---|---|---|---|
| Status; Boundary; Schema; P0-01 | `Task`/`TaskRevision` authority, total task identity mapping, exact-revision CAS | TV-36 | Generic and task identities no longer compete; greatest-sequence no longer permits stale concurrent transitions | Migration feasibility; command authorization policy |
| Schema; P0-03/04/05 | Resultant-state-only `TaskSubject`, stable `TaskOccurrence` plus audited append-only `TaskOccurrenceRevision`, canonical journal-native `WorkOccurrence` only for unresolved/independent/additional episodes, same-task `TaskSeries`/`RuleRevision`/`PlanExpectation` bindings | TV-08–09, TV-43, TV-45–50 | Event claims can no longer masquerade as task state; planned and journal-native episode identity have a total allocation rule; occurrence state is no longer mutable; ad-hoc/cross-task work cannot receive plan expectations | Recurrence horizon/logical keys |
| P0-02/07/10 | Append-only correction, conflict precedence, source-specific evidence ceiling | TV-40–41 | Provider state no longer means outcome; arrival order no longer resolves conflict | Claim registry; admissibility/strength/mapping policies |
| P0-09 | `planning_state × epistemic_outcome × deadline_projection`; explicit outcome subject; atomic-but-independent Mark done in one visibility authority; explicit reopen | TV-37–39, TV-46, TV-53–54 | Claim correction no longer reopens planning; predecessor claims are not rebound to current revisions; deadline no longer asserts outcome or state transition; partial dual-write visibility and expired-ID replay fail closed | Due-boundary/timezone UX; chosen dedupe retention bound |
| P0-08/09; P1-01 | Occurrence/child/delegate scope isolation | TV-42–44 | Child, recurring occurrence, and delegate evidence no longer discharge broader obligations | Partial aggregation; parent/delegator satisfaction and acceptance policies |
| Task traces; Finding matrix | Seven executable state/evidence traces mapped to contracts | TV-36–45 | Abstract lifecycle examples now expose forbidden inferences and exact subjects/revisions | Vocabulary and eventual adoption decision |
| P0-05/06; P1-03/04 | Existing receipt ladder, non-idempotent effect safety, deletion and privacy contours retained | Existing TV-17–22, TV-29–33 | No task shortcut bypasses effect ambiguity, deletion epochs, or disclosure limits | Provider contracts; retention SLAs; privacy budgets/schemas |

This ledger records the evolution of the product-semantic proposal and the subset later incorporated into the vision; its
still-proposed assurance contracts. It is not evidence that the model is implemented,
feasible, operationally adopted, or externally verified.
