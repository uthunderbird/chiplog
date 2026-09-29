"""Physical H1 producer effects-history source boundary."""

from __future__ import annotations

import copy
import hashlib
from dataclasses import replace
from pathlib import Path

import pytest

import chiplog.composition.h1_producer_sources as sources
from chiplog.adapters.driven.effects_queries import StoredEffectRow
from chiplog.capabilities.deployment_trust.prepared_external_delivery_contracts import (
    BoundedExternalSelfSendMandateV1,
    ExternalDeliveryResourcesV1,
    IssuePreparedExternalDeliveryGrantRequestV2,
    PreparedExternalDeliveryGrantScopeV2,
)
from chiplog.capabilities.deployment_trust.prepared_external_delivery_grant_owner_contracts import (
    prepared_delivery_basis_head_v3,
)
from chiplog.capabilities.deployment_trust.prepared_external_delivery_policy_contracts import (
    IssuePreparedExternalSelfDeliveryPolicyRequestV1,
    PreparedExternalSelfDeliveryPolicyTermsV1,
)
from chiplog.capabilities.effects.contracts import ExactHead
from chiplog.capabilities.effects.h1_normative_conflict_generation import h1_effects_history_digest
from chiplog.composition.common_cli_execution_runtime import open_installed_h1_scoped_runtime
from chiplog.composition.h1_launch_enrollment import _open_installed_h1_launch
from chiplog.composition.h1_producer_history import (
    H1ProducerEffectsHistory,
    H1ProducerHistoryMember,
)
from chiplog.composition.h1_scoped_delivery_authority import (
    H1ScopedDeliveryAuthorityCapture,
    H1ScopedDeliveryAuthorityReader,
)
from chiplog.composition.h1_selected_output_sources import H1SelectedOutputSources
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources
from chiplog.composition.r16_effects import MaterializedEffectsCut
from chiplog.platform._owner_publication_contracts import OwnerRecordBytes
from tests.composition.test_h1_prepared_delivery_basis_registry import _completed_session
from tests.composition.test_j7_mounted_external_delivery_grant import (
    _signed_grant,
    _trust_observation,
    _write_grant_pin,
)
from tests.platform.test_prepared_policy_owner_route import _authorize, _signed_source, _write_pin
from tests.support.h1_installed_launch import (
    DATABASE,
    TENANT,
    installed_slot,
    prepare_installed_slot,
)


def _resources(tmp_path: Path) -> HermeticDispatchResources:
    return HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "dispatch-custody"
    )


def _synthetic_issued_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> H1ScopedDeliveryAuthorityCapture:
    """Isolate the physical-reader test from the separately tested J7 grant route.

    The source reader still uses the real installed runtime, authority gate,
    owner journal and SQLite materialization.  The J7 authority reader has its
    own mounted integration suite; this seam only substitutes its held-record
    replay so this focused test does not recreate the policy/grant ceremony.
    """
    capture = object.__new__(H1ScopedDeliveryAuthorityCapture)
    capture._record = object()
    monkeypatch.setattr(
        H1ScopedDeliveryAuthorityReader,
        "_issued_record_held",
        lambda self, held: held._record,
    )
    monkeypatch.setattr(
        H1ScopedDeliveryAuthorityReader,
        "_verify_record_held",
        lambda self, record: None,
    )
    return capture


async def _real_authority(runtime: object) -> tuple[H1ScopedDeliveryAuthorityReader, object]:
    """Issue a real J7 grant and return its reader-issued authority capture."""
    request, seal, _session, _exchange = await _completed_session(runtime)
    registry = runtime._h1_completion_exchange_registry
    assert registry is not None
    historical = registry.capture_prepared_delivery_historical(
        original_identity=request.identity,
        original_fingerprint=request.original_driver_command_fingerprint(),
        selected_seal=seal,
    )
    current = registry.capture_prepared_delivery_current(
        original_identity=request.identity,
        original_fingerprint=request.original_driver_command_fingerprint(),
        selected_seal=seal,
    )
    projection = registry.replay_prepared_delivery_current(current)
    selected = H1SelectedOutputSources(runtime).capture_scope_selected_current(
        projection.selected_scope.scope
    )
    assert selected is not None
    epoch, now_ns = runtime._require_dispatch_resources().clock()
    terms = PreparedExternalSelfDeliveryPolicyTermsV1(
        principal_id=projection.principal_id,
        channel_id="hermetic-local",
        recipient=projection.delivery.selection.recipient,
        communication_permission="PREPARED_EXTERNAL_SEND",
        disclosure_permission="EXACT_RENDERED_PAYLOAD",
        self_recipient_semantics="OPERATOR_ATTESTED_SELF",
        payload_class="NonAuthoritativeText",
        payload_digest=projection.delivery.render_digest,
        payload_byte_length=len(projection.delivery.rendered_bytes),
        source_classes=("CLI",),
        selection_modes=("ORIGIN_EXACT",),
        external_delivery=True,
        max_calls=1,
        clock_contract="chiplog.dispatch.monotonic.v2",
        clock_epoch=epoch,
        not_before_ns=now_ns - 1,
        expires_at_ns=now_ns + 300_000_000_000,
    )
    policy_request = IssuePreparedExternalSelfDeliveryPolicyRequestV1(
        operation="ISSUE_PREPARED_EXTERNAL_SELF_DELIVERY_POLICY",
        command_id="policy-for-grant",
        tenant_id=TENANT,
        database_id=DATABASE,
        policy_id="policy",
        expected_trust_observation=_trust_observation(runtime),
        expected_policy=None,
        terms=terms,
    )
    policy = await _authorize(runtime, _signed_source(policy_request), request_id="grant-policy")
    scope = PreparedExternalDeliveryGrantScopeV2(
        principal_id=projection.principal_id,
        worker_session_id=projection.worker_session_id,
        contour_head=projection.selected_scope.scope.contour_head,
        authenticated_credential_head=projection.selected_scope.scope.authenticated_cli_state.credential_head,
        authenticated_session_head=projection.selected_scope.scope.authenticated_cli_state.session_head,
        selected_source=selected.selected_source,
        resources=ExternalDeliveryResourcesV1(
            signature_domain="dispatch-resources.v1",
            signed_observation_fingerprint=(
                projection.selected_scope.scope.selected_resource_observation_ref.signed_observation_fingerprint
            ),
            resource_grant=selected.resource_grant,
            recipient=projection.delivery.selection.recipient,
            clock_epoch=epoch,
        ),
        mandate=BoundedExternalSelfSendMandateV1(
            purpose="PREPARED_EXTERNAL_SELF_SEND",
            external_delivery=True,
            selection="ORIGIN_EXACT",
            payload_class="NonAuthoritativeText",
            communication_authority=policy.anchor.policy,
            disclosure_authority=policy.anchor.policy,
            self_recipient_binding=policy.anchor.policy,
            original_run=projection.original_run,
            captured_attempt=projection.captured_attempt,
            preparation_basis=prepared_delivery_basis_head_v3(projection.basis),
            delivery_id=projection.delivery.delivery_id,
            payload_digest=projection.delivery.render_digest,
            payload_byte_length=len(projection.delivery.rendered_bytes),
            max_calls=1,
            clock_contract="chiplog.dispatch.monotonic.v2",
            clock_epoch=epoch,
            not_before_ns=terms.not_before_ns,
            expires_at_ns=terms.expires_at_ns,
        ),
    )
    issue = IssuePreparedExternalDeliveryGrantRequestV2(
        operation="ISSUE_PREPARED_EXTERNAL_DELIVERY_GRANT",
        command_id="grant-issue",
        tenant_id=TENANT,
        database_id=DATABASE,
        grant_id="grant",
        policy_id="policy",
        expected_trust_observation=_trust_observation(runtime),
        expected_grant=None,
        selected_policy_anchor=policy.anchor,
        canonical_selected_policy_bytes=policy.policy.canonical_bytes(),
        proposed_scope=scope,
    )
    await runtime.authorize_prepared_external_delivery_grant(
        _signed_grant(issue),
        request_id="grant-issue",
        original_identity=request.identity,
        original_fingerprint=request.original_driver_command_fingerprint(),
        selected_seal=seal,
    )
    reader = H1ScopedDeliveryAuthorityReader(runtime)
    return reader, await reader.read(historical_b_capture=historical, grant_id="grant")


@pytest.mark.asyncio
async def test_reader_uses_real_mounted_physical_cut_and_rejects_foreign_capture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    _write_pin(slot.database_path)
    _write_grant_pin(slot.database_path)
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_scoped_runtime(
            launch, resources=_resources(tmp_path)
        ) as runtime:
            authority_reader = H1ScopedDeliveryAuthorityReader(runtime)
            reader = sources.H1ProducerSourceReader(runtime, authority_reader)
            forged = object.__new__(H1ScopedDeliveryAuthorityCapture)
            forged._record = object()
            with pytest.raises(sources.H1ProducerSourceViolation, match="not current"):
                await reader.read(authority_capture=forged, intent_id="new-intent")

            authority_reader, authority = await _real_authority(runtime)
            reader = sources.H1ProducerSourceReader(runtime, authority_reader)
            captured = await reader.read(authority_capture=authority, intent_id="new-intent")

            assert captured.history.tenant_id == runtime._tenant_id
            assert captured.history.digest
            assert captured.physical_cut.subject_id == "chiplog.h1-producer.physical-cut.v1"
            assert captured.physical_cut.head == (
                captured.physical_cut.subject_id + "/" + captured.physical_cut.fingerprint
            )
            assert captured.effects_history.canonical_value == captured.history.canonical_bytes()
            assert (
                captured.normative_conflict_generation.clock_epoch
                == captured.effects_history.clock_epoch
            )
            epoch, observed, horizon = captured.clock
            assert epoch == captured.effects_history.clock_epoch
            assert observed < horizon
            with runtime._authority_gate().hold():
                assert reader.recheck_held(captured) is captured
            with pytest.raises(TypeError, match="cannot be copied"):
                copy.copy(captured)


@pytest.mark.asyncio
async def test_recheck_detects_a_changed_physical_cut(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_scoped_runtime(
            launch, resources=_resources(tmp_path)
        ) as runtime:
            authority_reader = H1ScopedDeliveryAuthorityReader(runtime)
            reader = sources.H1ProducerSourceReader(runtime, authority_reader)
            authority = _synthetic_issued_authority(monkeypatch)
            captured = await reader.read(authority_capture=authority, intent_id="new-intent")
            record = captured._record
            changed_cut = replace(
                record.physical_cut,
                tenant_frontier=record.physical_cut.tenant_frontier + 1,
            )

            # The selected effects history and its digest do not cover the
            # physical frontier.  The domain-separated physical cut head does.
            assert sources._physical_cut_head(changed_cut, record.history) != captured.physical_cut
            assert record.history.digest == captured.history.digest
            original = sources._read_materialized_effects_with_history

            def appended(runtime: object, journal: object, *, run_id: str | None = None) -> object:
                cut, snapshot = original(runtime, journal, run_id=run_id)
                return replace(cut, tenant_frontier=cut.tenant_frontier + 1), snapshot

            with monkeypatch.context() as context:
                context.setattr(sources, "_read_materialized_effects_with_history", appended)
                with (
                    runtime._authority_gate().hold(),
                    pytest.raises(
                        sources.H1ProducerSourceViolation, match="physical effects source changed"
                    ),
                ):
                    reader.recheck_held(captured)

            second_reader = sources.H1ProducerSourceReader(runtime, authority_reader)
            foreign = await second_reader.read(authority_capture=authority, intent_id="new-intent")
            with (
                runtime._authority_gate().hold(),
                pytest.raises(sources.H1ProducerSourceViolation, match="not reader-issued"),
            ):
                reader.recheck_held(foreign)
            reader.release(captured)
            with (
                runtime._authority_gate().hold(),
                pytest.raises(sources.H1ProducerSourceViolation, match="not reader-issued"),
            ):
                reader.recheck_held(captured)
            forged = object.__new__(sources.H1ProducerSourceCapture)
            forged._record = record
            with (
                runtime._authority_gate().hold(),
                pytest.raises(sources.H1ProducerSourceViolation, match="not reader-issued"),
            ):
                reader.recheck_held(forged)


def test_conversion_preserves_selected_bytes_and_rejects_duplicate_intent() -> None:
    raw = b'{"record":"exact"}'
    member = H1ProducerHistoryMember(
        selected_decision=ExactHead(
            subject_id="selected", head="selected/head", fingerprint="a" * 64
        ),
        tenant_commit_sequence=1,
        publication_ordinal=0,
        record_id="record",
        record_kind="effects.INTENT_RECORDED",
        schema_id="chiplog.effects.record.v1",
        fingerprint=hashlib.sha256(raw).hexdigest(),
        canonical_record_bytes=raw,
        intent_id="existing-intent",
    )
    history_member = sources.H1EffectsHistoryMemberV1(
        selected_decision=member.selected_decision,
        tenant_commit_sequence=member.tenant_commit_sequence,
        publication_ordinal=member.publication_ordinal,
        record_id=member.record_id,
        record_kind=member.record_kind,
        schema_id=member.schema_id,
        fingerprint=member.fingerprint,
        canonical_record_bytes=member.canonical_record_bytes,
        intent_id=member.intent_id,
    )
    digest = h1_effects_history_digest("tenant", "head", (history_member,))
    interpreted = H1ProducerEffectsHistory(
        tenant_id="tenant", owner_journal_head="head", ordered_members=(member,), digest=digest
    )
    converted = sources._effects_history(interpreted)
    assert converted.ordered_members[0].canonical_record_bytes == raw
    with pytest.raises(ValueError, match="already present"):
        converted.require_intent_absent("existing-intent")
    with pytest.raises(ValueError, match="fingerprint"):
        sources.H1EffectsHistoryMemberV1(
            selected_decision=member.selected_decision,
            tenant_commit_sequence=member.tenant_commit_sequence,
            publication_ordinal=member.publication_ordinal,
            record_id=member.record_id,
            record_kind=member.record_kind,
            schema_id=member.schema_id,
            fingerprint=member.fingerprint,
            canonical_record_bytes=raw + b" ",
            intent_id=member.intent_id,
        )


def test_physical_cut_head_commits_frontier_outside_history_digest() -> None:
    raw = b'{"record":"exact"}'
    fingerprint = hashlib.sha256(raw).hexdigest()
    selected = ExactHead(subject_id="selected", head="selected/head", fingerprint="a" * 64)
    history_member = sources.H1EffectsHistoryMemberV1(
        selected_decision=selected,
        tenant_commit_sequence=1,
        publication_ordinal=0,
        record_id="record",
        record_kind="effects.INTENT_RECORDED",
        schema_id="chiplog.effects.record.v1",
        fingerprint=fingerprint,
        canonical_record_bytes=raw,
        intent_id="intent",
    )
    history = sources.H1EffectsHistoryV1(
        tenant_id="tenant",
        owner_journal_head="owner-head",
        ordered_members=(history_member,),
        digest=h1_effects_history_digest("tenant", "owner-head", (history_member,)),
    )
    row = StoredEffectRow(
        commit_sequence=1,
        publication_ordinal=0,
        batch_record_ids=("record",),
        record=OwnerRecordBytes(
            owner="effects",
            record_kind="effects.INTENT_RECORDED",
            record_id="record",
            schema_id="chiplog.effects.record.v1",
            canonical_bytes=raw,
            fingerprint=fingerprint,
        ),
    )
    cut = MaterializedEffectsCut(
        tenant_id="tenant",
        tenant_frontier=1,
        materialization_commitment="commitment",
        owner_journal_head="owner-head",
        physical_path="/authenticated/database.sqlite3",
        physical_device=1,
        physical_inode=2,
        rows=(row,),
        worker=None,
        latest_runs=(),
    )

    head = sources._physical_cut_head(cut, history)
    assert head == sources._physical_cut_head(cut, history)
    assert head.subject_id == "chiplog.h1-producer.physical-cut.v1"
    assert head.head == head.subject_id + "/" + head.fingerprint
    assert sources._physical_cut_head(replace(cut, tenant_frontier=2), history) != head
    assert history.digest == h1_effects_history_digest("tenant", "owner-head", (history_member,))
