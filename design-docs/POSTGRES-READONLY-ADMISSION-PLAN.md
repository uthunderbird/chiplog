# Carried original-work checks on new read-only admission

## Plan

1. Mount a real PG regression: first seal performs an actual effect, authenticated
   receipt resolves CONFIRMED; second seal selects a history call whose first read
   fails definitely retryable. Append a verified late RIVAL for the older effect.
2. Exercise successor retry and explicit same-Run retry separately. Each must HOLD
   without a new attempt/counter, history read, successor or publication. First
   run the assertions against current code and retain the actual RED result.
3. If confirmed, call existing snapshot-based `_crossed_work_closed` for the selected
   executing Run before NEW admission, including the guarded writer snapshot.
   Successor retains its target-seal sibling check. Avoid calling this from the
   outcome-retention path: an already accepted read may retain its observed result.
4. Add a positive control that a late RIVAL arriving after acceptance does not
   discard the accepted read outcome. Run only the bounded regression and existing
   readonly/successor/lifecycle consumers, update status and checkpoint the fix.

5. Add the requested narrow completion convergence witnesses: successful retry
   and max3 exhaustion via successor, pending continuation HOLD before terminal,
   actual model CompleteAcceptance, then reopen/replay with no new read, counter,
   publication or SEND. These exercise the second continuation consumer without
   adding new runtime behavior unless a concrete failure appears.

## Assumptions

- Carried `original_obligations` and native no-retry references identify crossed
  effects; `_crossed_work_closed` already checks their current closure/reduction.
- Existing completed/replay inspection is historical and grants no new read.
- Outcome retention consumes an existing accepted attempt; it is not admission.
- Current user-row CAS/guard is the publication boundary. No new proof DTO,
  backend abstraction, retry configuration or broad matrix is required.
- Root retains integration ownership; this work changes only chiplog-postgres.
