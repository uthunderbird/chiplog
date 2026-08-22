# Plan / Fact / Journal — examples

> Status: non-normative explanation of the proposed model. Authority and provenance are defined in [README.md](README.md).

## Worked examples

### Did not swim Tuesday

Scheduling creates `occ-tue`, an exact snapshot, and
`PLAN_EXPECTED(expect-swim, occ-tue, rev-7)`. On Wednesday the user says they did not
swim. The journal appends an `UNCONFIRMED` outcome claim on
`OccurrenceSubject(occ-tue)`; policy may later confirm it. Neither state closes,
reschedules, or edits the plan. Trying Thursday requires a new command and new
revision/occurrence, with typed lineage if classified as rescheduling.

### Swam at 10, not 8

The 08:00 seed remains an expectation on its scheduled revision. A later claim says
the swim occurred at 10:00 on `OccurrenceSubject(occ-tue)`, because it reports the
realization of that existing planned occurrence; it does not rewrite the seed.
The expectation/fact divergence is visible but is not an evidence conflict. Only if
an independent admissible fact claim says the swim occurred at 08:00 do equal-
strength 08:00 and 10:00 fact claims project `DISPUTED`. A provider check-in confirms
only the check-in claim type, not that the swim happened.

### Delayed invocation after replanning

An invocation queued under `rev-12` reaches dispatch after `rev-13` changes time and
audience. It stores both revisions. Compatibility fails and the attempt remains held;
it cannot use old authority. A new command may authorize a new attempt. If a
non-idempotent attempt already crossed `DISPATCHING_NONIDEMPOTENT`, ambiguity forbids
takeover/resend; repetition requires a new effect and duplicate-risk acknowledgement.

## Task specialization traces

### Mark done, then correct the report

Prestate: `task-rev-4` is `IN_PROGRESS`. Request `req-9` atomically appends
`FactClaim(USER_REPORTED_OUTCOME, COMPLETION, value=DONE, RESULTANT_STATE,
TaskSubject(task-1, task-rev-4))` without `occurred_at` and an independently
authorized command whose CAS target is `task-rev-4` and successor is
`task-rev-5/CLOSED`. The closed card explicitly names `task-rev-4` as its outcome
subject, so its projection is `CLOSED × REPORTED_DONE`. A later correction says
the report was mistaken by appending a same-key replacement with `value=NOT_DONE`:
projection becomes `CLOSED × REPORTED_NOT_DONE` (or
`DISPUTED` under policy), never reopened. Only a new exact-revision reopen command can
create `task-rev-6/PLANNED`. Forbidden: treating either atomic record as authority or
evidence for the other. Vectors: TV-36–38.

### Deadline passes untouched

Prestate: exact open `task-rev-7/PLANNED`, due at 17:00 in its recorded timezone. At
17:01 the view reads `PLANNED × UNKNOWN × OVERDUE`. No claim, command, attempt, or new
revision is appended. Forbidden: inferring not done, attempted, cancelled, satisfied,
or closed. Vector: TV-39.

### Provider checkbox checked, user says not done

The provider snapshot confirms only `PROVIDER_CHECKBOX_CHECKED` on the exact item.
The user appends `USER_REPORTED_OUTCOME × COMPLETION, value=NOT_DONE` on the exact
occurrence, so the checkbox
alone does not contest that outcome and the view projects `REPORTED_NOT_DONE`. Only
a versioned mapping policy may derive an outcome claim from provider state; if it
does, it must emit the same canonical outcome claim type/slot with polarity in
`value=DONE|NOT_DONE|PARTIAL`; if the two outcome claims are equal-strength,
projection is `DISPUTED`.
Planning is unchanged in either case. Forbidden: checkbox-to-completion promotion or
latest-arrival precedence. Vectors: TV-40–41.

### Partial subtask

A child occurrence has an admissible `PARTIAL` outcome claim. Its view may show
`IN_PROGRESS × PARTIAL`; the parent revision and all siblings remain unchanged.
Closing the child still requires its own command, and closing the parent requires a
parent-scoped command and satisfaction policy. Forbidden: part-to-whole discharge.
Vector: TV-42.

### Recurring occurrence skipped

An authorized skip command CAS-targets one exact occurrence revision and appends its
`SKIPPED` `TaskOccurrenceRevision` successor under the same occurrence identity. The
`TaskSeries`, its active `RuleRevision`, and other occurrences stay
open and unchanged. Forbidden: one skipped occurrence closing/cancelling the series
or rewriting the rule. Vector: TV-43.

### Delegate reports complete

The delegate appends a typed completion report on the delegated exact subject. The
delegated task projects that report under evidence policy, but the delegator
obligation and structural parent remain open. Separate exact-scope commands and any
required counterparty acceptance are needed to close either. Forbidden: evidence-
driven delegation discharge. Vector: TV-44.

### Spontaneous atemporal completion

For an existing `task-8/task-rev-2`, the user appends a completion claim on
`TaskSubject(task-8, task-rev-2)` with `RESULTANT_STATE` semantics and no
`occurred_at`. Projection may become `PLANNED × REPORTED_DONE`,
but planning remains open until a separate authorized close command. If no task
or planned occurrence existed, or if the report explicitly distinguishes an episode
from its planned slot, the event receives a journal-native `WorkOccurrence`; a
linked episode may evidence the task-state claim but never closes the task. An event
that instead realizes one existing planned occurrence uses its `OccurrenceSubject`.
The system does not back-create a task or expectation.
Forbidden: retroactive normative history or event fields on `TaskSubject`.
Vector: TV-45.

<!-- Copied payload ends above; see MIGRATION-MANIFEST.md. -->
