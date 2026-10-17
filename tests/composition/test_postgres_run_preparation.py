import pytest

from chiplog.adapters.driven.loop_hermetic import HermeticModel
from chiplog.capabilities.agent_loop.contracts import BudgetPolicy
from chiplog.platform.postgres import Connection, PostgresServer, Record
from tests.support.composition.postgres_execution import bootstrap, opened, origin


async def test_prepared_ingress_run_and_companion_publish_all_or_none(
    postgres_server: PostgresServer,
) -> None:
    database = await bootstrap(postgres_server)
    runtime = opened(database, HermeticModel(()))
    snapshot = runtime.snapshot()
    run, records = runtime.prepare_execution_records(
        snapshot,
        run_id="inbox-run",
        prompt="exact ingress",
        policy=BudgetPolicy(),
        origin=origin(),
        worker=runtime.current_worker(),
    )
    assert runtime.snapshot() == snapshot

    def fail_after_preparation(connection: Connection) -> None:
        database.append(
            connection, "test.first-half", "input", "fingerprint", records, snapshot.head
        )
        raise RuntimeError("companion failed")

    companion = Record("inbox", "agent_loop", "test.inbox.v1", b"inbox")
    with pytest.raises(RuntimeError, match="companion failed"):
        await runtime.publish_execution_batch(
            "local-cli",
            "test.admit",
            "input",
            snapshot.head,
            (*records, companion),
            transaction_action=fail_after_preparation,
        )
    assert runtime.snapshot() == snapshot
    await runtime.publish_execution_batch(
        "local-cli", "test.admit", "input", snapshot.head, (*records, companion)
    )
    reopened = opened(database, HermeticModel(()))
    assert reopened.execution_record(run.run_id) == run
    selected = reopened.snapshot().batches[-1]
    assert {item.record_id for item in selected.records} == {
        item.record_id for item in (*records, companion)
    }
