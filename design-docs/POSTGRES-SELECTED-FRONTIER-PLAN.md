# Selected provider-query frontier repair

## Plan

1. Mount the ratified unrelated-Run regression in a typed repository test. Configure
   the durable provider with the required scenarios at construction, without
   mutating its private scenario array. Run A resolves its actual effect and
   accepts Complete; Run B independently resolves another effect. Run A's real
   delivery commit/claim must remain usable and replay safely after reopen.
2. Reproduce the actual delivery commit RED before changing production code.
3. In provider_queries keep current_effect_result and exact receipt/closure/
   reduction checks unchanged. Collect only the actual current reduction heads
   used by the returned Run query set. Compute the shared frontier from publication
   sequences containing those heads, then apply that aggregate to all returned
   queries. No unrelated Run publication belongs to this cut.
4. Run actual delivery commit/claim/reconcile and reopen/replay without extra SEND;
   retain selected old-seal RIVAL negatives and the two-effect completion witness.
   Record red/green, focused typing and checkpoint; root owns independent rerun.

## Assumptions

- prepare_completion requires query frontiers to match the observation aggregate;
  per-query independent frontiers would change that existing semantic contract.
- Actual latest selected reduction heads are already verified by the existing
  effect reader; record IDs are immutable and unique inside the user database.
- Selected late evidence changes the current reduction/inventory and must still
  HOLD stale acceptance or unresolved original work. This fix only removes
  interference from unrelated Run reductions.
- SQLite sources, existing bytes, deployment gate and transport custody semantics
  remain untouched. No new backend/configuration/verification framework is needed.
