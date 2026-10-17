import base64

import pytest
from tests.support.scheduler_execution import origin

from chiplog.capabilities.agent_loop.contracts import (
    BudgetPolicy,
    LoopRejected,
    SchedulerRootReference,
)
from chiplog.capabilities.agent_loop.execution_client_transitions import create, current
from chiplog.capabilities.agent_loop.recovery_contracts import IndividualSubject


def test_scheduled_creation_retains_real_root_and_rejects_another_initial_run() -> None:
    subject = IndividualSubject(occurrence_id="occurrence")
    root = SchedulerRootReference(
        root_id="native-root",
        subject_canonical_base64=base64.b64encode(subject.canonical_bytes()).decode(),
        subject_schema_version="chiplog.execution-lineage-subject.v1",
        root_fingerprint="a" * 64,
        initial_run_id="run",
    )
    args = dict(
        tenant="t",
        principal="p",
        run_id="run",
        prompt="scheduled",
        policy=BudgetPolicy(),
        origin=origin(),
        worker="worker",
    )
    run = create(version="v3", root_binding=root, **args)
    assert current(run).root_binding == root
    assert run.state == "CREATED"
    with pytest.raises(LoopRejected, match="initial Run"):
        create(
            version="v3", root_binding=root.model_copy(update={"initial_run_id": "other"}), **args
        )
    with pytest.raises(LoopRejected, match="V3"):
        create(version="v2", root_binding=root, **args)
