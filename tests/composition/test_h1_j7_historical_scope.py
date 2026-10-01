"""Historical J7 verification uses only an authenticated durable prefix."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from chiplog.adapters.driven.deployment_trust import (
    IndependentTenantDecisionJournal,
    SQLiteTrustMaterializer,
)
from chiplog.architecture.r7_runtime import R14_R17_H1_PRODUCTION_MANIFEST
from chiplog.capabilities.agent_loop.delivery_contracts import ExactHead, ProviderRecipient
from chiplog.capabilities.deployment_trust import TrustReference
from chiplog.capabilities.deployment_trust._r7_process import _H1_ROUTES, _evaluate, _TrustOwnerCall
from chiplog.capabilities.deployment_trust.h1_broker_evidence_contracts import (
    H1CurrentBrokerRouteV1,
    H1OwnerCurrentCallV1,
    H1OwnerCurrentCandidateV1,
)
from chiplog.capabilities.deployment_trust.hermetic_output_scope_contracts import (
    CurrentHermeticExecutionScopeV1,
    H1AuthenticatedCliStateV1,
    HermeticOutputPolicyV1,
    HermeticOutputScopeAnchorV1,
    HermeticOutputScopeV1,
    HermeticOutputSourceV1,
    HermeticTrustObservationV1,
    ReadCurrentHermeticExecutionScopeV1,
    SelectedHermeticResourceObservationRefV1,
)
from chiplog.composition.common_cli_execution_runtime import open_installed_h1_scoped_runtime
from chiplog.composition.h1_j7_current_scope_exchange import H1J7CurrentScopeExchangeV1
from chiplog.composition.h1_j7_historical_scope import verify_h1_j7_historical_scope
from chiplog.composition.h1_launch_enrollment import _open_installed_h1_launch
from chiplog.composition.h1_scoped_delivery_authority import H1ScopedDeliveryAuthorityReader
from chiplog.domain_primitives import PrincipalId, TenantId
from chiplog.platform.authority_gate import AuthorityGate
from chiplog.platform.broker import BrokerSession, CallBudget, PublicPortCall, PublicPortSuccess
from chiplog.platform.r7_trust import TrustOwnerCall
from chiplog.platform.r7_trust_durability import BrokerTrustDurability
from tests.support.h1_installed_launch import installed_slot, prepare_installed_slot
from tests.support.h1_scoped_first_b import (
    issue_real_h1_scoped_authority,
    scoped_resources,
    write_scoped_pins,
)


def _head(identity: str, body: bytes = b"source") -> ExactHead:
    digest = hashlib.sha256(body).hexdigest()
    return ExactHead(identity=identity, head=identity + "/" + digest, fingerprint=digest)


def _live_call_forbidden(*_args: object, **_kwargs: object) -> None:
    raise AssertionError("live scope authority called")


def _mounted_current_scope_result_schema() -> str:
    operation = "deployment_trust.read_current_hermetic_output_scope"
    owner_schema = next(route[4] for route in _H1_ROUTES if route[0] == operation)
    manifest_schema = next(
        route.result_schema_id
        for route in R14_R17_H1_PRODUCTION_MANIFEST.routes
        if route.operation_id == operation
    )
    assert owner_schema == manifest_schema
    return owner_schema


def _scope() -> HermeticOutputScopeV1:
    endpoint = _head("endpoint")
    resource = SelectedHermeticResourceObservationRefV1(
        signature_domain="dispatch-resources.v1",
        selected_initialization=_head("selected-initialization"),
        signed_observation_fingerprint=hashlib.sha256(b"selected").hexdigest(),
    )
    policy = HermeticOutputPolicyV1(
        endpoint_ref=endpoint,
        selected_resource_observation_ref=resource,
        selection="ORIGIN_EXACT",
        ingress_class="AUTHENTICATED_R17_CLI",
        payload_class="NonAuthoritativeText",
        purpose="H1_LOCAL_COMMENTARY",
        external_delivery=False,
        attempt_ordinal=0,
        call_count=0,
    )
    return HermeticOutputScopeV1(
        issuer="deployment_trust",
        source_profile="chiplog.execution.h1-cli-hermetic-source-profile.v1",
        slot="h1-cli-effects-origin",
        tenant_id="hermetic-tenant",
        database_id="database",
        scope_id="scope",
        revision=0,
        predecessor=None,
        principal_id="hermetic-principal",
        worker_session_id="worker",
        contour_head="contour",
        admitted_authentication=_head("admission"),
        authenticated_cli_state=H1AuthenticatedCliStateV1(
            trust_binding_digest=hashlib.sha256(b"trust").hexdigest(),
            credential_head="credential",
            session_head="session",
        ),
        recipient=ProviderRecipient(
            provider_id="hermetic-effects",
            account_id="hermetic-account",
            recipient_id="hermetic-principal",
            canonical_address=b"hermetic://effects/hermetic-principal",
            endpoint=endpoint,
            credential_binding=_head("credential-binding"),
        ),
        selected_resource_observation_ref=resource,
        disclosure_policy=HermeticOutputSourceV1(
            field_path="disclosure_policy",
            ref=_head("policy", policy.canonical_bytes()),
            canonical_source_bytes=policy.canonical_bytes(),
        ),
        mandate_applicability="HERMETIC_EFFECTS_ORIGIN_NO_EXTERNAL_ACTION_V1",
        mandate_profile="h1-cli-effects-origin-zero-call-v1",
        mandate_inventory_complete=True,
        ordered_mandates=(),
    )


@dataclass(frozen=True)
class _Bundle:
    durability: BrokerTrustDurability
    materializer: SQLiteTrustMaterializer


@contextmanager
def _bundle(tmp_path: Path) -> Iterator[_Bundle]:
    gate = AuthorityGate.for_database(tmp_path / "authority.sqlite")
    journal = IndependentTenantDecisionJournal.for_authority_bundle(
        tmp_path / "trust.journal", authority_gate=gate
    )
    materializer = SQLiteTrustMaterializer.for_authority_bundle(
        tmp_path / "trust.sqlite", authority_gate=gate
    )
    try:
        yield _Bundle(
            BrokerTrustDurability(journal, materializer, b"operator-secret"), materializer
        )
    finally:
        materializer.close()


def _bootstrap(bundle: _Bundle) -> None:
    observation = bundle.durability.capture_verified_observation()
    response = _evaluate(
        _TrustOwnerCall(
            mode="BOOTSTRAP",
            snapshot_bytes=observation.snapshot_bytes,
            request_bytes=json.dumps(
                {
                    "credential_id": "credential-1",
                    "database_instance_id": "database-1",
                    "expected_peer": "uid:test",
                    "peer": "uid:test",
                    "principal_id": "principal-1",
                    "session_id": "session-1",
                    "tenant_id": "tenant-1",
                    "token_fingerprint": "bootstrap-token",
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode(),
        )
    )
    assert response.reference_bytes is not None
    bundle.durability.apply_authorized(response.reference_bytes)


def _anchor(
    bundle: _Bundle, decision_id: str, scope: HermeticOutputScopeV1
) -> HermeticOutputScopeAnchorV1:
    raw = next(
        raw for actual, _, raw in bundle.durability._journal.entries() if actual == decision_id
    )
    record = bundle.materializer.record(decision_id, 1)
    assert record is not None
    identity = f"trust-record:{decision_id}:1"
    return HermeticOutputScopeAnchorV1(
        owner_id="deployment_trust",
        decision=ExactHead(
            identity="deployment-trust/journal",
            head=decision_id,
            fingerprint=hashlib.sha256(raw).hexdigest(),
        ),
        record_ordinal=1,
        record_type_id="chiplog.deployment_trust.hermetic_output_scope",
        schema_id="chiplog.deployment_trust.record.v1",
        record=ExactHead(
            identity=identity,
            head=identity + "/" + hashlib.sha256(record).hexdigest(),
            fingerprint=hashlib.sha256(record).hexdigest(),
        ),
        scope_revision=scope.revision,
        predecessor=scope.predecessor,
        selected_resource_observation_ref=scope.selected_resource_observation_ref,
    )


def _exchange(bundle: _Bundle) -> tuple[H1J7CurrentScopeExchangeV1, HermeticOutputScopeV1]:
    _bootstrap(bundle)
    scope = _scope()
    decision_id, _, _ = bundle.durability.append_hermetic_output_scope(scope.canonical_bytes())
    raw = next(
        raw for actual, _, raw in bundle.durability._journal.entries() if actual == decision_id
    )
    observation = HermeticTrustObservationV1(
        physical_journal_head=ExactHead(
            identity="deployment-trust/journal",
            head=decision_id,
            fingerprint=hashlib.sha256(raw).hexdigest(),
        ),
        logical_snapshot_head=bundle.durability.owner_snapshot_entries()[-1][0],
    )
    prefix = bundle.durability.historical_prefix(observation)
    anchor = _anchor(bundle, decision_id, scope)
    scope_ref = _head(scope.scope_id, scope.canonical_bytes())
    request = ReadCurrentHermeticExecutionScopeV1(
        expected_trust_observation=observation,
        source_anchor=anchor,
        expected_revision=scope.revision,
        admitted_authentication_ref=scope.admitted_authentication,
        authenticated_cli_ref=TrustReference(
            tenant_id=TenantId(scope.tenant_id),
            principal_id=PrincipalId(scope.principal_id),
            contour="CLI",
            credential_head=scope.authenticated_cli_state.credential_head,
            session_head=scope.authenticated_cli_state.session_head,
            source_head="source",
            trust_head=scope.authenticated_cli_state.trust_binding_digest,
            materialization_head="materialization",
            freshness_sequence=0,
            peer_credential="peer",
        ),
        tenant_id=scope.tenant_id,
        database_id=scope.database_id,
        scope_id=scope.scope_id,
        expected_scope_ref=scope_ref,
        expected_worker_session_id=scope.worker_session_id,
        selected_resource_observation_ref=scope.selected_resource_observation_ref,
    )
    route = H1CurrentBrokerRouteV1(
        tenant_id="hermetic-tenant",
        broker_epoch=1,
        runtime_generation="generation",
        broker_session_id="broker-session",
        owner_session_id="owner-session",
        request_id="request",
    )
    call = H1OwnerCurrentCallV1(
        route=route,
        read_request_bytes=request.canonical_bytes(),
        request_digest=hashlib.sha256(request.canonical_bytes()).hexdigest(),
    )
    current = CurrentHermeticExecutionScopeV1(
        disposition="CURRENT",
        scope_ref=scope_ref,
        source_anchor=anchor,
        selector_generation=0,
        ordered_current_source_refs=(scope.admitted_authentication, anchor.decision, anchor.record),
    )
    candidate = H1OwnerCurrentCandidateV1(
        route=route, request_digest=call.request_digest, current=current
    )
    owner_wire = TrustOwnerCall(
        mode="READ_CURRENT_HERMETIC_OUTPUT_SCOPE_V1",
        snapshot_bytes=prefix.snapshot_bytes,
        request_bytes=call.canonical_bytes(),
    )
    caller = BrokerSession(
        tenant_id="hermetic-tenant",
        broker_epoch=1,
        generation_id="generation",
        owner_id="broker",
        session_id="broker-session",
    )
    callee = BrokerSession(
        tenant_id="hermetic-tenant",
        broker_epoch=1,
        generation_id="generation",
        owner_id="deployment_trust",
        session_id="owner-session",
    )
    return H1J7CurrentScopeExchangeV1(
        request=request,
        result=current,
        sent=PublicPortCall(
            operation_id="deployment_trust.read_current_hermetic_output_scope",
            request_id="request",
            caller=caller,
            callee=callee,
            schema_id="chiplog.deployment-trust.owner-call.v1",
            canonical_payload=owner_wire.canonical_bytes(),
            budget=CallBudget(
                remaining_calls=1,
                remaining_depth=1,
                absolute_deadline_ns=30,
                policy_version=1,
            ),
        ),
        returned=PublicPortSuccess(
            request_id="request",
            responder=callee,
            schema_id=_mounted_current_scope_result_schema(),
            canonical_payload=candidate.canonical_bytes(),
        ),
        sent_at_ns=10,
        returned_at_ns=20,
    ), scope


def test_historical_j7_exchange_uses_real_prefix_without_live_reader(tmp_path: Path) -> None:
    with _bundle(tmp_path) as bundle:
        exchange, scope = _exchange(bundle)
        gate = bundle.durability.authority_gate
        assert gate is not None
        evidence = verify_h1_j7_historical_scope(
            exchange, scope, trust_reader=bundle.durability, authority_gate=gate
        )

    assert evidence.exchange == exchange
    assert evidence.scope == scope


def test_historical_j7_verifier_rejects_wire_route_timestamp_record_and_lineage_mutations(
    tmp_path: Path,
) -> None:
    with _bundle(tmp_path) as bundle:
        exchange, scope = _exchange(bundle)
        gate = bundle.durability.authority_gate
        assert gate is not None
        prefix = bundle.durability.historical_prefix(exchange.request.expected_trust_observation)
        record = bundle.durability.historical_record(
            exchange.request.source_anchor.decision.head, 1
        )
        bad_record = replace(record, record_bytes=b"{}")
        duplicate_prefix = replace(
            prefix, physical_entries=(*prefix.physical_entries, prefix.physical_entries[-1])
        )

        class _Reader:
            authority_gate = gate

            def historical_prefix(self, _observation: object) -> object:
                return duplicate_prefix

            def historical_record(self, _decision: str, _ordinal: int) -> object:
                return bad_record

        mutations = (
            exchange.model_copy(
                update={"sent": exchange.sent.model_copy(update={"canonical_payload": b"{}"})}
            ),
            exchange.model_copy(
                update={
                    "returned": exchange.returned.model_copy(
                        update={"schema_id": "chiplog.deployment-trust.owner-result.v1"}
                    )
                }
            ),
            exchange.model_copy(update={"returned_at_ns": 1}),
            exchange.model_copy(
                update={"returned_at_ns": exchange.sent.budget.absolute_deadline_ns}
            ),
            exchange.model_copy(
                update={
                    "request": exchange.request.model_copy(
                        update={
                            "source_anchor": exchange.request.source_anchor.model_copy(
                                update={"record_ordinal": 0}
                            )
                        }
                    )
                }
            ),
        )
        for mutated in mutations:
            with pytest.raises(ValueError):
                verify_h1_j7_historical_scope(
                    mutated, scope, trust_reader=bundle.durability, authority_gate=gate
                )
        with pytest.raises(ValueError):
            verify_h1_j7_historical_scope(
                exchange, scope, trust_reader=_Reader(), authority_gate=gate
            )


@pytest.mark.asyncio
async def test_reopened_mounted_j7_exchange_uses_no_live_scope_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A retained J7 exchange remains historical after current resources change."""
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    write_scoped_pins(slot.database_path)
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_scoped_runtime(
            launch, resources=scoped_resources(tmp_path)
        ) as runtime:
            reader, capture = await issue_real_h1_scoped_authority(runtime)
            retained_exchange = reader.export_current_scope_exchange(capture).canonical_bytes()
            retained_scope = capture._record.projection.selected_scope.scope.canonical_bytes()

    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_scoped_runtime(
            launch, resources=scoped_resources(tmp_path)
        ) as runtime:
            exchange = H1J7CurrentScopeExchangeV1.model_validate_json(retained_exchange)
            scope = HermeticOutputScopeV1.model_validate_json(retained_scope)
            runtime._require_dispatch_resources().revoke()
            monkeypatch.setattr(
                H1ScopedDeliveryAuthorityReader,
                "read",
                _live_call_forbidden,
            )
            monkeypatch.setattr(
                runtime._trust,
                "capture_verified_observation",
                _live_call_forbidden,
            )
            monkeypatch.setattr(
                runtime,
                "_read_current_hermetic_output_scope_with_wire",
                _live_call_forbidden,
            )
            with pytest.raises(AssertionError, match="live scope authority called"):
                await runtime._read_current_hermetic_output_scope_with_wire(exchange.request)

            evidence = verify_h1_j7_historical_scope(
                exchange,
                scope,
                trust_reader=runtime._trust,
                authority_gate=runtime._authority_gate(),
            )
            assert evidence.scope == scope
            with pytest.raises(ValueError):
                verify_h1_j7_historical_scope(
                    exchange.model_copy(
                        update={
                            "sent": exchange.sent.model_copy(update={"canonical_payload": b"{}"})
                        }
                    ),
                    scope,
                    trust_reader=runtime._trust,
                    authority_gate=runtime._authority_gate(),
                )
