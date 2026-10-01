"""Fail-closed tests for the first native H1 authorization source role."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from chiplog.capabilities.effects.contracts import ExactHead
from chiplog.capabilities.effects.lifecycle_transition_contracts import SelectedEffectsSource
from chiplog.capabilities.effects.scoped_delivery_source_contracts import (
    decode_selected_delivery_intent,
)
from chiplog.capabilities.effects.scoped_intent_record_contracts import make_scoped_intent_member
from chiplog.composition.scoped_delivery_selected_sources import (
    ScopedDeliveryFirstSendAbsent,
    ScopedDeliveryInitialAuthorizationAbsent,
    ScopedDeliverySelectedSourcesResult,
)
from tests.support.scoped_intent_records import delivery_publication_fixture


class _Gate:
    held = False

    @contextmanager
    def hold(self):
        self.held = True
        try:
            yield
        finally:
            self.held = False

    def require_held(self) -> None:
        if not self.held:
            raise RuntimeError("gate is not held")


class _Runtime:
    def __init__(self) -> None:
        self._gate = _Gate()

    def _authority_gate(self) -> _Gate:
        return self._gate


async def _selected(*, frontier: int = 1) -> ScopedDeliverySelectedSourcesResult:
    fixture = await delivery_publication_fixture()
    member = make_scoped_intent_member(fixture.intent)
    source = SelectedEffectsSource(
        owner="effects",
        subject=ExactHead(
            subject_id=member.record_id, head=member.record_id, fingerprint=member.fingerprint
        ),
        schema_id=member.schema_id,
        canonical_record_bytes=member.canonical_record_bytes,
        selected_decision=ExactHead(subject_id="decision", head="decision", fingerprint="0" * 64),
        physical_record=ExactHead(
            subject_id=member.record_id, head=member.record_id, fingerprint=member.fingerprint
        ),
    )
    return ScopedDeliverySelectedSourcesResult(
        intent=source,  # type: ignore[arg-type]  # source decoder only needs these exact fields here.
        selected_h1=SimpleNamespace(),  # type: ignore[arg-type]
        initial_authorization=ScopedDeliveryInitialAuthorizationAbsent(member.record_id),
        first_send=ScopedDeliveryFirstSendAbsent(member.record_id, None),
        frontier=SimpleNamespace(tenant_frontier=frontier),  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_locator_reader_builds_initial_cut_without_caller_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import chiplog.composition.h1_scoped_authorization_sources_v3 as subject

    selected = await _selected()

    class _SelectedReader:
        def __init__(self, runtime: object) -> None:
            assert runtime is runtime_object

        def read(self, *, intent_id: str, act_id: str | None = None) -> object:
            assert intent_id == decode_selected_delivery_intent(selected.intent).intent_id
            assert act_id is None
            return selected

    runtime_object = _Runtime()
    monkeypatch.setattr(subject, "CommonCliExecutionRuntime", _Runtime)
    monkeypatch.setattr(subject, "ScopedDeliverySelectedSources", _SelectedReader)
    reader = subject.H1ScopedAuthorizationEffectsHistoryReaderV1(runtime_object)
    capture = reader.capture(
        subject.H1ScopedAuthorizationLocatorV1(
            decode_selected_delivery_intent(selected.intent).intent_id
        )
    )

    with runtime_object._authority_gate().hold():
        observed = reader.observed_held(capture)
    assert observed.state == "INTENT_RECORDED"
    assert observed.current_parent == observed.selected_intent
    assert observed.authorization.__class__.__name__ == "Absent"


@pytest.mark.asyncio
async def test_locator_reader_holds_when_native_cut_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import chiplog.composition.h1_scoped_authorization_sources_v3 as subject

    runtime = _Runtime()
    results = [await _selected(frontier=1), await _selected(frontier=2)]
    locator = subject.H1ScopedAuthorizationLocatorV1(
        decode_selected_delivery_intent(results[0].intent).intent_id
    )

    class _SelectedReader:
        def __init__(self, runtime: object) -> None:
            pass

        def read(self, **_: object) -> object:
            return results.pop(0)

    monkeypatch.setattr(subject, "CommonCliExecutionRuntime", _Runtime)
    monkeypatch.setattr(subject, "ScopedDeliverySelectedSources", _SelectedReader)
    reader = subject.H1ScopedAuthorizationEffectsHistoryReaderV1(runtime)
    capture = reader.capture(locator)
    with runtime._authority_gate().hold(), pytest.raises(
        subject.H1ScopedAuthorizationSourceHold, match="complete Effects cut changed"
    ):
        reader.recheck_held(capture)


@pytest.mark.asyncio
async def test_locator_reader_requires_explicit_initial_authorization_absence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import chiplog.composition.h1_scoped_authorization_sources_v3 as subject

    selected = await _selected()
    wrong_absence = selected.initial_authorization.__class__("another-intent")
    selected = selected.__class__(
        intent=selected.intent,
        selected_h1=selected.selected_h1,
        initial_authorization=wrong_absence,
        first_send=selected.first_send,
        frontier=selected.frontier,
    )

    class _SelectedReader:
        def __init__(self, runtime: object) -> None:
            pass

        def read(self, **_: object) -> object:
            return selected

    runtime = _Runtime()
    monkeypatch.setattr(subject, "CommonCliExecutionRuntime", _Runtime)
    monkeypatch.setattr(subject, "ScopedDeliverySelectedSources", _SelectedReader)
    reader = subject.H1ScopedAuthorizationEffectsHistoryReaderV1(runtime)
    locator = subject.H1ScopedAuthorizationLocatorV1(
        decode_selected_delivery_intent(selected.intent).intent_id
    )
    with pytest.raises(subject.H1ScopedAuthorizationSourceHold, match="initial authorization cut"):
        reader.capture(locator)


def test_locator_reader_rejects_forged_capture(monkeypatch: pytest.MonkeyPatch) -> None:
    import chiplog.composition.h1_scoped_authorization_sources_v3 as subject

    monkeypatch.setattr(subject, "CommonCliExecutionRuntime", _Runtime)
    monkeypatch.setattr(subject, "ScopedDeliverySelectedSources", lambda _: None)
    runtime = _Runtime()
    reader = subject.H1ScopedAuthorizationEffectsHistoryReaderV1(runtime)
    forged = object.__new__(subject.H1ScopedAuthorizationEffectsHistoryCaptureV1)
    with runtime._authority_gate().hold(), pytest.raises(
        subject.H1ScopedAuthorizationSourceHold, match="not reader-issued"
    ):
        reader.observed_held(forged)


def test_historical_resource_roles_verify_signed_bytes_without_live_resource(
    tmp_path: Path,
) -> None:
    """Historical R16 verification uses durable custody, never a current resource."""
    from chiplog.composition.h1_scoped_authorization_sources_v3 import (
        H1ScopedAuthorizationResourceHold,
        verify_historical_scoped_authorization_resource_signature_v1,
    )
    from chiplog.composition.r16_dispatch_custody import load_existing_historical_custody
    from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources
    from chiplog.platform.authority_gate import AuthorityGate

    custody_path = tmp_path / "custody.json"
    resources = HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=custody_path
    )
    resources.bind(AuthorityGate(tmp_path / "authority.db"))
    observation = resources.observe()
    custody = load_existing_historical_custody(custody_path)
    resources.revoke()

    roles = verify_historical_scoped_authorization_resource_signature_v1(custody, observation)
    assert roles.endpoint.canonical_value == observation.endpoint_bytes
    assert roles.clock_epoch == observation.clock_epoch
    assert roles.endpoint.head.fingerprint != roles.credential_lifecycle.head.fingerprint

    with pytest.raises(H1ScopedAuthorizationResourceHold, match="signature differs"):
        verify_historical_scoped_authorization_resource_signature_v1(
            custody, replace(observation, clock_epoch="forged-epoch")
        )


@pytest.mark.asyncio
async def test_locator_reader_revokes_retained_capture_when_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import chiplog.composition.h1_scoped_authorization_sources_v3 as subject

    selected = await _selected()

    class _SelectedReader:
        def __init__(self, runtime: object) -> None:
            pass

        def read(self, **_: object) -> object:
            return selected

    runtime = _Runtime()
    monkeypatch.setattr(subject, "CommonCliExecutionRuntime", _Runtime)
    monkeypatch.setattr(subject, "ScopedDeliverySelectedSources", _SelectedReader)
    reader = subject.H1ScopedAuthorizationEffectsHistoryReaderV1(runtime)
    capture = reader.capture(
        subject.H1ScopedAuthorizationLocatorV1(
            decode_selected_delivery_intent(selected.intent).intent_id
        )
    )
    reader.close()
    with runtime._authority_gate().hold(), pytest.raises(
        subject.H1ScopedAuthorizationSourceHold, match="reader is closed"
    ):
        reader.observed_held(capture)


def test_initial_authorization_absence_contract_rejects_exact_owner_authorization_row() -> None:
    """A canonical selected authorization row cannot be relabelled as initial."""
    from chiplog.composition.h1_scoped_authorization_issuance_v3 import (
        decode_scoped_authorization_issuance_v3,
    )
    from chiplog.composition.scoped_delivery_selected_sources import (
        ScopedDeliverySelectedSourcesError,
        _require_initial_authorization_absent,
    )

    raw = (
        Path(__file__).with_name("fixtures") / "h1_scoped_authorization_issuance_v3.json"
    ).read_bytes()
    issuance = decode_scoped_authorization_issuance_v3(raw)
    intent_id = issuance.original_cut.observed.original_intent.intent_id
    cut = SimpleNamespace(rows=(SimpleNamespace(record=issuance.output_records[0]),))
    with pytest.raises(ScopedDeliverySelectedSourcesError, match="already has authorization"):
        _require_initial_authorization_absent(cut=cut, intent_id=intent_id)


@pytest.mark.asyncio
async def test_mounted_locator_capture_reopens_exact_initial_h1_v3_cut(tmp_path: Path) -> None:
    """The one native role is read from mounted SQL/journal state, never a caller DTO."""
    from chiplog.composition.common_cli_execution_runtime import (
        open_installed_h1_scoped_v21_runtime,
    )
    from chiplog.composition.h1_completion_issuance import H1CompletionIssuanceV3
    from chiplog.composition.h1_historical_selected_sources import bind_selected_h1_completion
    from chiplog.composition.h1_launch_enrollment import _open_installed_h1_launch
    from chiplog.composition.h1_scoped_authorization_sources_v3 import (
        H1ScopedAuthorizationLocatorV1,
        H1ScopedAuthorizationResourceReaderV1,
    )
    from tests.support.h1_installed_launch import installed_slot, prepare_installed_slot
    from tests.support.h1_scoped_first_b import scoped_resources, write_scoped_pins
    from tests.support.h1_v3_recovery import (
        _issue_reconstructed_grant,
        _recover_b_prefix,
        _selected_scoped_root,
    )

    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    write_scoped_pins(slot.database_path)
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_scoped_v21_runtime(
            launch, resources=scoped_resources(tmp_path)
        ) as runtime:
            request, seal, state = await _selected_scoped_root(runtime)
            state = await _recover_b_prefix(runtime, request, seal, state)
            # The mounted path includes multiple owner-process recoveries.  Its
            # test-only horizon exceeds that real run while preserving the
            # signed policy/grant route and all current-time validation.
            await _issue_reconstructed_grant(
                runtime,
                request,
                seal,
                authorization_horizon_ns=3_600_000_000_000,
            )
            await runtime._h1_postseal_recovery_coordinator.resume_selected(
                request.identity, request.original_driver_command_fingerprint(), seal
            )
            await runtime.finalize_execution(
                request.identity, request.original_driver_command_fingerprint()
            )
            decision = next(
                item
                for item in runtime._owner_decisions().snapshot().decisions
                if item.prepared.request.__class__.__name__ == "CompleteDeliveryBatchV2"
            )
            historical = bind_selected_h1_completion(decision.prepared.request, runtime)
            assert type(historical.issuance) is H1CompletionIssuanceV3
            # The selected original H1 publication names the only intent locator.
            locator = H1ScopedAuthorizationLocatorV1(
                historical.issuance.effects_result.intent_result.intent.intent_id
            )
            reader = runtime._h1_scoped_authorization_effects_history_reader_v3
            capture = reader.capture(locator)
            with runtime._authority_gate().hold():
                observed = reader.observed_held(capture)
            assert observed.state == "INTENT_RECORDED"
            assert observed.authorization.__class__.__name__ == "Absent"
            registry = runtime._h1_completion_exchange_registry
            assert registry is not None
            prepared = registry.capture_recovered_prepared_delivery_historical(
                original_identity=request.identity,
                original_fingerprint=request.original_driver_command_fingerprint(),
                selected_seal=seal,
            )
            authority_reader = runtime._h1_scoped_delivery_authority_reader
            authority_capture = await authority_reader.read_reconstructed(
                historical_b_capture=prepared
            )
            resource_reader = runtime._h1_scoped_authorization_resource_reader_v3
            assert type(resource_reader) is H1ScopedAuthorizationResourceReaderV1
            resource_capture = resource_reader.read(
                effects_capture=capture, authority_capture=authority_capture
            )
            with runtime._authority_gate().hold():
                roles = resource_reader.roles_held(resource_capture)
                resource_reader.recheck_held(resource_capture)
            observation = runtime._require_dispatch_resources().observe()
            assert roles.endpoint.canonical_value == observation.endpoint_bytes
            assert roles.credential_lifecycle.canonical_value == observation.credential_bytes
            assert roles.deployment_entitlement.canonical_value == observation.grant_bytes
            assert roles.clock_epoch == observation.clock_epoch
            resource_reader.release(resource_capture)
            reader.release(capture)

    with _open_installed_h1_launch(slot) as reopened_launch:
        async with open_installed_h1_scoped_v21_runtime(
            reopened_launch, resources=scoped_resources(tmp_path)
        ) as reopened:
            reader = reopened._h1_scoped_authorization_effects_history_reader_v3
            assert type(reopened._h1_scoped_authorization_resource_reader_v3) is (
                H1ScopedAuthorizationResourceReaderV1
            )
            capture = reader.capture(locator)
            with reopened._authority_gate().hold():
                assert reader.observed_held(capture).original_intent.intent_id == locator.intent_id
            reader.release(capture)
