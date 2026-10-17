# R15–R17 functional verification

2026-10-17. Functional acceptance verified on product source `4866099` plus
the Plan recovery test extension. Acceptance is the user-authorized scope in
[R15-R17-FUNCTIONAL-PLAN.md](R15-R17-FUNCTIONAL-PLAN.md), not the exhaustive
historical roadmap DoD. R15, R16 and R17 are CLOSED under that functional scope.

## Requirement and observable evidence

| Requirement | Current implementation | Executed evidence |
|---|---|---|
| R15 finite interval decisions; stable individual/coalesced/skipped occurrence identities; no duplicate Run | `composition/postgres_scheduler.py`, `postgres_scheduler_records.py`; existing agent-loop scheduler semantics | `test_postgres_scheduler.py`: due members, reopen replay, cutoff/boundary substitution |
| Atomic disposition/boundary/root/Run and recoverable overflow | Scheduler publication uses execution preparation in the same PG transaction | Same module: injected physical append failure rolls back Runs and disposition; insufficient resolution retains hold; sufficient bound resolves once |
| Lease expiry/takeover fences actual actions | `postgres_execution.py`, `postgres_model_recovery.py`, scheduler/effects claim guards | `test_postgres_scheduler_actions.py`: expired/taken-over SEND has zero transfers; expired model start has zero requests; late captured response remains but cannot seal |
| Scheduled path uses shared driver through completion and delivery | `postgres_application.py`, `postgres_completion.py` | Scheduler tests plus scheduler-actions positive case: adopted effect, lost response, reconciliation, Complete, actual delivery transfer; fresh runtime reuses claim, no model or transfer repeat |
| R16 authorized Plan/effect publication is atomic | `postgres_effect_work.py`, Planning transaction helper in `postgres_planning.py` | `test_plan_and_effect_share_transaction_and_keep_non_tool_origin`: failure between owner publications rolls back both; stale/denied publication leaves neither half; lost commit response replays the same intent after runtime/provider reconstruction |
| Durable bounded worker discovers sends and unresolved claims; no blind resend | `postgres_effect_work.py`, `postgres_effects.py` | Effects tests: fresh worker discovers committed Plan SEND, transfers once; claimed work reopens into reconciliation; pretransport claim fault remains unknown without send; gate/actor revalidated at claim |
| Compensation requires fresh authority and current original evidence | Effects action lifecycle and native compensation origin | `test_compensation_has_separate_origin_fresh_adoption_and_current_original_evidence`: distinct intent, stale evidence refusal, original uncertainty retained |
| R17 exact authenticated custody before ACK; replay selects original inbox/Run | `postgres_ingress.py`, `native_ingress_cli.py`, `native_ingress_telegram.py` | `test_postgres_ingress.py`: actual Unix peer and webhook authentication; forged witness/changed bytes rejected; rollback/lost ACK; source-change at commit cut; reopen exact bytes |
| Both channels share application/effect/completion/delivery | `postgres_application.py`, existing completion lifecycle | `test_native_channel_delivery.py`, CLI/Telegram × direct/effect: lost response and fresh-runtime recovery preserve exact selected payload with no repeated model/SEND |
| Usable native user command with actual trust and durable local delivery | `native_cli.py`, `postgres_native_cli.py`, `postgres_local_delivery.py`, `delivery_local_cli.py` | Actual subprocess `test_native_chat.py`: exact output, second process with empty model, one retained inbox entry; forged principal/session rejected before local inbox creation; changed input and wrong key rejected. Local-delivery composition/platform tests cover durable ticket/reconcile/reopen |

All source paths above are under `src/chiplog`; composition tests are under
`tests/composition`. The command test is under `tests/cli`, and the local provider
test under `tests/platform`. These tests run the mounted implementations, not
only contract DTOs. Channel/provider fixtures are hermetic; the native CLI also
executes the real local filesystem delivery transport.

## Observed combined run

Run from the repository root against the dedicated local PostgreSQL test instance:

```sh
CHIPLOG_TEST_POSTGRES_DSN='host=127.0.0.1 port=55439 user=postgres dbname=postgres' \
uv run pytest -q -p no:timeout \
 tests/composition/test_postgres_scheduler.py \
 tests/composition/test_postgres_scheduler_actions.py \
 tests/composition/test_postgres_effects.py \
 tests/composition/test_postgres_ingress.py \
 tests/composition/test_native_channel_delivery.py \
 tests/composition/test_postgres_application.py \
 tests/composition/test_postgres_planning.py \
 tests/composition/test_postgres_execution.py \
 tests/composition/test_postgres_model_recovery.py \
 tests/composition/test_postgres_completion.py \
 tests/capabilities/effects/test_action_lifecycle_contracts.py \
 tests/capabilities/effects/test_effect_work_contracts.py \
 tests/capabilities/agent_loop/test_scheduler_client_contracts.py \
 tests/capabilities/agent_loop/test_ingress_client_contracts.py \
 tests/cli/test_native_chat.py \
 tests/composition/test_postgres_local_delivery.py \
 tests/platform/test_delivery_local_cli.py
```

Observed terminal result: **103 passed in 47.80s**. Fixtures allocate isolated
per-user test databases. Targeted Ruff on the final Plan test also passed.
Earlier full preflight on product source `4866099` exited 0.

Cold source/result review of the native CLI independently ran its ten actual-PG
and local-provider tests and found no concrete P0/P1. A separate cold closure-plan
review inspected the retained criteria and mounted witnesses without finding a
critical gap. Both share the model family and do not establish external proof.

The final cold result audit read this map and the actual source/test bodies. It
found no concrete P0/P1 or incorrect factual claim, including the mounted
scheduled and ordinary effect/delivery witnesses. It did not repeat the combined
run; its 103-test result is the root agent's direct observation above.

## Remaining boundary

The functional plan's B-R15-01, B-R16-01, B-R17-01 and B-VERIFY-01 remain open.
They include exhaustive rollover/race/clock-rollback assurance, broad compensation
permutations, universal source/FIFO/quarantine closure and stress/global
certification. Supported scheduling assumes a nondecreasing clock. Live Telegram
or Calendar deployment, external credentials and main/master integration are not
verified here. `native-chat` requires real existing PG trust and an independently
signed operator entitlement; legacy `chat` remains a separate conversational path.
