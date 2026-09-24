"""Focused owner common-frontier representation checks."""

import hashlib
from typing import Literal

import pytest
from tests.support.fault_observer_sources import full_fixture
from tests.support.terminal_fault_exchange import (
    canonical_recovery_cut,
    prepared_result,
    refreshed_request,
    registered_finding,
)

from chiplog.capabilities.agent_loop.terminal_recovery_fault_contracts import (
    DecodedRecoveryFaultFrontierV1,
    FaultRecoveryRecordIntegrityError,
    UndecodableRecoveryFaultFrontierV1,
    validate_terminal_recovery_fault_exchange,
)


@pytest.mark.parametrize("version", ("v2", "v3"))
async def test_observer_fixture_retains_malformed_frontier_bytes_in_captured_slot(
    version: Literal["v2", "v3"],
) -> None:
    request, _retained, _rules = await full_fixture(version)
    observation = request.cut.frontier_observation
    assert isinstance(observation, UndecodableRecoveryFaultFrontierV1)
    assert any(
        item.capture == observation.capture and item.observed_bytes == observation.raw_bytes
        for item in request.cut.ordered_observations
    )


@pytest.mark.parametrize("version", ("v2", "v3"))
async def test_worker_rejects_canonical_recovery_cut_labeled_undecodable(
    version: Literal["v2", "v3"],
) -> None:
    request, _observer_sources, selected_rules = await full_fixture(version)
    canonical_cut = canonical_recovery_cut(request)
    raw = canonical_cut.canonical_bytes()
    observed = request.cut.ordered_observations[0].model_copy(
        update={
            "observed_bytes": raw,
            "observed_bytes_fingerprint": hashlib.sha256(raw).hexdigest(),
        }
    )
    mislabeled = UndecodableRecoveryFaultFrontierV1(
        expected_schema_id="chiplog.execution.recovery-cut.v1",
        raw_bytes=raw,
        capture=observed.capture,
    )
    worker_request = refreshed_request(
        request,
        request.cut.model_copy(
            update={"ordered_observations": (observed,), "frontier_observation": mislabeled}
        ),
        worker=True,
    )
    result = prepared_result(worker_request, registered_finding(selected_rules))

    with pytest.raises(
        FaultRecoveryRecordIntegrityError,
        match="canonical valid frontier cannot be marked undecodable",
    ):
        validate_terminal_recovery_fault_exchange(worker_request, result, selected_rules)


@pytest.mark.parametrize("version", ("v2", "v3"))
async def test_valid_decoded_frontier_accepts_for_worker(
    version: Literal["v2", "v3"],
) -> None:
    request, _observer_sources, selected_rules = await full_fixture(version)
    recovery_cut = canonical_recovery_cut(request)
    decoded = DecodedRecoveryFaultFrontierV1(
        cut=recovery_cut,
        canonical_cut_bytes=recovery_cut.canonical_bytes(),
    )
    worker_request = refreshed_request(
        request,
        request.cut.model_copy(update={"frontier_observation": decoded}),
        worker=True,
    )

    validate_terminal_recovery_fault_exchange(
        worker_request,
        prepared_result(worker_request, registered_finding(selected_rules)),
        selected_rules,
    )


@pytest.mark.parametrize("version", ("v2", "v3"))
async def test_valid_malformed_frontier_accepts_for_observer(
    version: Literal["v2", "v3"],
) -> None:
    request, observer_sources, selected_rules = await full_fixture(version)

    validate_terminal_recovery_fault_exchange(
        request,
        prepared_result(request, registered_finding(selected_rules)),
        selected_rules,
        observer_sources=observer_sources,
    )


@pytest.mark.parametrize("version", ("v2", "v3"))
@pytest.mark.parametrize("worker", (False, True))
@pytest.mark.parametrize(
    "coordinate",
    (
        "tenant_id",
        "database_id",
        "tenant_commit_sequence",
        "materialization_commitment",
        "current_run",
        "frontier_tenant_id",
        "frontier_run_id",
        "frontier_tenant_commit_sequence",
    ),
)
async def test_decoded_frontier_coordinates_must_match_diagnostic_cut(
    version: Literal["v2", "v3"],
    worker: bool,
    coordinate: str,
) -> None:
    request, observer_sources, selected_rules = await full_fixture(version)
    recovery_cut = canonical_recovery_cut(request)
    if coordinate == "current_run":
        recovery_cut = recovery_cut.model_copy(update={coordinate: request.cut.suspension_pair})
    elif coordinate.startswith("frontier_"):
        frontier_coordinate = coordinate.removeprefix("frontier_")
        value = (
            99
            if frontier_coordinate == "tenant_commit_sequence"
            else "foreign-" + frontier_coordinate
        )
        recovery_cut = recovery_cut.model_copy(
            update={
                "frontier": recovery_cut.frontier.model_copy(update={frontier_coordinate: value})
            }
        )
    else:
        value = (
            99
            if coordinate == "tenant_commit_sequence"
            else "e" * 64
            if coordinate == "materialization_commitment"
            else "foreign-" + coordinate
        )
        recovery_cut = recovery_cut.model_copy(update={coordinate: value})
    decoded = DecodedRecoveryFaultFrontierV1(
        cut=recovery_cut,
        canonical_cut_bytes=recovery_cut.canonical_bytes(),
    )
    changed = refreshed_request(
        request,
        request.cut.model_copy(update={"frontier_observation": decoded}),
        worker=worker,
    )
    result = prepared_result(changed, registered_finding(selected_rules))

    with pytest.raises(FaultRecoveryRecordIntegrityError, match="frontier differs"):
        validate_terminal_recovery_fault_exchange(
            changed,
            result,
            selected_rules,
            observer_sources=None if worker else observer_sources,
        )
