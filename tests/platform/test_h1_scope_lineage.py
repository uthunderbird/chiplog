"""Authenticated currentness for durable H1 output scopes."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import pytest

from chiplog.adapters.driven.deployment_trust import (
    IndependentTenantDecisionJournal,
    SQLiteTrustMaterializer,
)
from chiplog.capabilities.agent_loop.delivery_contracts import ExactHead, ProviderRecipient
from chiplog.capabilities.deployment_trust._r7_process import _evaluate, _TrustOwnerCall
from chiplog.capabilities.deployment_trust.hermetic_output_scope_contracts import (
    H1AuthenticatedCliStateV1,
    HermeticOutputPolicyV1,
    HermeticOutputScopeAnchorV1,
    HermeticOutputScopeV1,
    HermeticOutputSourceV1,
    HermeticTrustObservationV1,
    SelectedHermeticResourceObservationRefV1,
)
from chiplog.platform.authority_gate import AuthorityGate
from chiplog.platform.r7_trust_durability import BrokerTrustDurability


def _head(identity: str, body: bytes = b"source") -> ExactHead:
    digest = hashlib.sha256(body).hexdigest()
    return ExactHead(identity=identity, head=identity + "/" + digest, fingerprint=digest)


def _resource_ref() -> SelectedHermeticResourceObservationRefV1:
    return SelectedHermeticResourceObservationRefV1(
        signature_domain="dispatch-resources.v1",
        selected_initialization=_head("selected-initialization"),
        signed_observation_fingerprint=hashlib.sha256(b"selected").hexdigest(),
    )


def _scope(
    *,
    database_id: str = "database",
    scope_id: str = "scope",
    revision: int = 0,
    predecessor: ExactHead | None = None,
    worker_session_id: str = "worker",
) -> HermeticOutputScopeV1:
    endpoint = _head("endpoint")
    resource = _resource_ref()
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
    policy_bytes = policy.canonical_bytes()
    return HermeticOutputScopeV1(
        issuer="deployment_trust",
        source_profile="chiplog.execution.h1-cli-hermetic-source-profile.v1",
        slot="h1-cli-effects-origin",
        tenant_id="hermetic-tenant",
        database_id=database_id,
        scope_id=scope_id,
        revision=revision,
        predecessor=predecessor,
        principal_id="hermetic-principal",
        worker_session_id=worker_session_id,
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
            ref=_head("policy", policy_bytes),
            canonical_source_bytes=policy_bytes,
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
        durability = BrokerTrustDurability(journal, materializer, b"operator-secret")
        yield _Bundle(durability, materializer)
    finally:
        materializer.close()


def _anchor(
    bundle: _Bundle, decision_id: str, scope: HermeticOutputScopeV1
) -> HermeticOutputScopeAnchorV1:
    raw = next(
        raw
        for actual_id, _, raw in bundle.durability._journal.entries()
        if actual_id == decision_id
    )
    record = bundle.materializer.record(decision_id, 1)
    assert record is not None
    record_identity = "trust-record:" + decision_id + ":1"
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
            identity=record_identity,
            head=record_identity + "/" + hashlib.sha256(record).hexdigest(),
            fingerprint=hashlib.sha256(record).hexdigest(),
        ),
        scope_revision=scope.revision,
        predecessor=scope.predecessor,
        selected_resource_observation_ref=scope.selected_resource_observation_ref,
    )


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


def test_current_scope_advances_only_by_exact_physical_predecessor(tmp_path: Path) -> None:
    with _bundle(tmp_path) as bundle:
        _bootstrap(bundle)
        revision_zero = _scope()
        zero_id, _, _ = bundle.durability.append_hermetic_output_scope(
            revision_zero.canonical_bytes()
        )
        zero_anchor = _anchor(bundle, zero_id, revision_zero)
        revision_one = _scope(revision=1, predecessor=zero_anchor.decision)
        one_id, _, _ = bundle.durability.append_hermetic_output_scope(
            revision_one.canonical_bytes()
        )
        one_anchor = _anchor(bundle, one_id, revision_one)

        assert bundle.durability.current_hermetic_output_scope(one_anchor).scope == revision_one
        with pytest.raises(RuntimeError, match="superseded"):
            bundle.durability.current_hermetic_output_scope(zero_anchor)
        historical = bundle.durability.historical_hermetic_output_scope(revision_zero)
        assert historical is not None
        assert historical.scope == revision_zero


@pytest.mark.parametrize(
    "candidate",
    (
        lambda zero: _scope(revision=0),
        lambda zero: _scope(revision=0, worker_session_id="divergent"),
        lambda zero: _scope(revision=2, predecessor=zero.decision),
        lambda zero: _scope(revision=1, predecessor=_head("wrong-predecessor")),
    ),
    ids=("revision-reuse", "divergent-reuse", "gap", "wrong-predecessor"),
)
def test_append_rejects_non_successor_scope_lineage(tmp_path: Path, candidate: object) -> None:
    with _bundle(tmp_path) as bundle:
        _bootstrap(bundle)
        zero = _scope()
        zero_id, _, _ = bundle.durability.append_hermetic_output_scope(zero.canonical_bytes())
        zero_anchor = _anchor(bundle, zero_id, zero)

        with pytest.raises(RuntimeError, match="lineage"):
            bundle.durability.append_hermetic_output_scope(candidate(zero_anchor).canonical_bytes())  # type: ignore[operator]


def test_lineage_isolated_by_database_and_scope_identity(tmp_path: Path) -> None:
    with _bundle(tmp_path) as bundle:
        _bootstrap(bundle)
        first = _scope(database_id="database-a", scope_id="scope-a")
        bundle.durability.append_hermetic_output_scope(first.canonical_bytes())

        other = _scope(database_id="database-b", scope_id="scope-a")
        other_id, _, _ = bundle.durability.append_hermetic_output_scope(other.canonical_bytes())

        assert bundle.durability.historical_hermetic_output_scope(other).decision_id == other_id


def test_reopened_historical_scope_accepts_persisted_json_and_rejects_invalid_scope(
    tmp_path: Path,
) -> None:
    with _bundle(tmp_path) as bundle:
        _bootstrap(bundle)
        scope = _scope()
        decision_id, _, _ = bundle.durability.append_hermetic_output_scope(
            scope.canonical_bytes()
        )
        raw = bundle.durability._journal.entries()[-1][2]
        logical_head = bundle.durability.owner_snapshot_entries()[-1][0]

    with _bundle(tmp_path) as reopened:
        record = reopened.durability.historical_record(decision_id, 1)
        assert record.record_bytes == reopened.materializer.record(decision_id, 1)
        observation = HermeticTrustObservationV1(
            physical_journal_head=ExactHead(
                identity="deployment-trust/journal",
                head=decision_id,
                fingerprint=hashlib.sha256(raw).hexdigest(),
            ),
            logical_snapshot_head=logical_head,
        )
        assert reopened.durability.historical_prefix(observation).observation == observation

        payload = json.loads(record.envelope_bytes)["payload"]
        assert payload["scope"] == scope.model_dump(mode="json")
        del payload["scope"]["issuer"]
        with pytest.raises(RuntimeError, match="historical H1 scope payload is invalid"):
            reopened.durability._validate_historical_payload(
                "HERMETIC_OUTPUT_SCOPE_V1", payload
            )
