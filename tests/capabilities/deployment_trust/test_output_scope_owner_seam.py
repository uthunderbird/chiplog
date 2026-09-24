"""Contract checks, deliberately no claim of issuance or H1 acceptance."""

import base64
import hashlib
import json
import sqlite3
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from typing import Any, Literal

import pytest
from pydantic import TypeAdapter, ValidationError

from chiplog.adapters.driven.deployment_trust import (
    SQLiteTrustMaterializer,
)
from chiplog.capabilities.agent_loop.delivery_contracts import ExactHead
from chiplog.capabilities.deployment_trust import (
    IssueHermeticOutputScopeResultV1,
    IssueHermeticOutputScopeV1,
    ReadCurrentHermeticExecutionScopeV1,
    SelectedHermeticResourceObservationRefV1,
    TrustReference,
)
from chiplog.capabilities.deployment_trust._output_scope_profile import (
    VerifiedH1SelectedSources,
    configured_h1_effects_origin_slot,
)
from chiplog.capabilities.deployment_trust._r7_process import dispatch
from chiplog.capabilities.deployment_trust.h1_broker_evidence_contracts import (
    H1_CALL_MAX_BYTES,
    H1OwnerCandidateCallV1,
)
from chiplog.capabilities.deployment_trust.hermetic_output_scope_contracts import (
    HermeticOutputScopeAnchorV1,
    HermeticTrustObservationV1,
)
from chiplog.domain_primitives import PrincipalId, TenantId
from chiplog.platform.r7_trust import TrustOwnerCall, TrustOwnerResult


def head(identity: str) -> ExactHead:
    return ExactHead(
        identity=identity, head=identity, fingerprint=hashlib.sha256(b"inert").hexdigest()
    )


def request() -> IssueHermeticOutputScopeV1:
    return IssueHermeticOutputScopeV1(
        slot_id="h1-cli-effects-origin",
        database_id="db",
        scope_id="scope",
        expected_trust_observation=HermeticTrustObservationV1(
            physical_journal_head=head("physical-journal"),
            logical_snapshot_head="logical-snapshot",
        ),
        expected_scope_predecessor=None,
        expected_revision=0,
        authenticated_cli_ref=TrustReference(
            TenantId("hermetic-tenant"),
            PrincipalId("hermetic-principal"),
            "CLI",
            "credential",
            "session",
            "source",
            hashlib.sha256(b"trust-binding").hexdigest(),
            "materialization",
            0,
            "peer",
        ),
        admitted_authentication_ref=head("selected-r17"),
        worker_session_id="worker",
        selected_resource_observation_ref=SelectedHermeticResourceObservationRefV1(
            signature_domain="dispatch-resources.v1",
            selected_initialization=head("initialization"),
            signed_observation_fingerprint=hashlib.sha256(b"inert signed observation").hexdigest(),
        ),
    )


class UnimplementedVerifier:
    def resolve_selected_current(
        self,
        resource_ref: SelectedHermeticResourceObservationRefV1,
        admitted_authentication_ref: ExactHead,
        authenticated_cli_ref: TrustReference,
    ) -> VerifiedH1SelectedSources | None:
        raise AssertionError("contract-only seam must not pretend source verification exists")


def read_request() -> ReadCurrentHermeticExecutionScopeV1:
    issue = request()
    return ReadCurrentHermeticExecutionScopeV1(
        expected_trust_observation=issue.expected_trust_observation,
        source_anchor=HermeticOutputScopeAnchorV1(
            owner_id="deployment_trust",
            decision=head("physical-scope-decision"),
            record_ordinal=1,
            record_type_id="chiplog.deployment_trust.hermetic_output_scope",
            schema_id="chiplog.deployment_trust.record.v1",
            record=head("scope-record"),
            scope_revision=0,
            predecessor=None,
            selected_resource_observation_ref=issue.selected_resource_observation_ref,
        ),
        expected_revision=0,
        admitted_authentication_ref=issue.admitted_authentication_ref,
        authenticated_cli_ref=issue.authenticated_cli_ref,
        tenant_id="hermetic-tenant",
        database_id="db",
        scope_id="scope",
        expected_scope_ref=head("scope"),
        expected_worker_session_id="worker",
        selected_resource_observation_ref=issue.selected_resource_observation_ref,
    )


def candidate_request() -> tuple[H1OwnerCandidateCallV1, bytes]:
    # Inert contract fixture: these bytes assert consistency, never provenance.
    from chiplog.platform.r7_trust import encode_trust_journal
    from tests.capabilities.deployment_trust.test_h1_broker_evidence_contracts import call

    original = call()
    entries: list[tuple[str, str | None, bytes]] = []
    predecessor = None
    for kind in ("INITIALIZE", "BOOTSTRAP"):
        envelope = json.dumps(
            {"kind": kind, "payload": {}, "predecessor": predecessor},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        logical_id = hashlib.sha256(
            (predecessor or "GENESIS").encode() + b"\x00" + envelope
        ).hexdigest()
        entries.append((logical_id, predecessor, envelope))
        predecessor = logical_id
    snapshot = encode_trust_journal(tuple(entries))
    observation = original.evidence.trust_observation.model_copy(
        update={"logical_snapshot_head": predecessor}
    )
    intent = IssueHermeticOutputScopeV1.model_validate_json(
        original.evidence.selected_request_bytes
    ).model_copy(update={"expected_trust_observation": observation})
    evidence = original.evidence.model_copy(
        update={
            "trust_snapshot_digest": hashlib.sha256(snapshot).hexdigest(),
            "trust_observation": observation,
            "selected_request_bytes": intent.canonical_bytes(),
            "request_digest": hashlib.sha256(intent.canonical_bytes()).hexdigest(),
        }
    )
    value = H1OwnerCandidateCallV1(
        evidence=evidence, evidence_digest=hashlib.sha256(evidence.canonical_bytes()).hexdigest()
    )
    return value, snapshot


@pytest.mark.parametrize(
    "variant",
    ["bare", "bound", "digest", "head", "structure", "encoding", "noncanonical", "envelope"],
)
def test_issue_rejects_invalid_candidate_or_outer_snapshot(variant: str) -> None:
    candidate, snapshot = candidate_request()
    raw = candidate.canonical_bytes()
    if variant == "bare":
        raw = request().canonical_bytes()
    elif variant == "bound":
        raw = b" " * (H1_CALL_MAX_BYTES + 1)
    elif variant == "digest":
        snapshot += b" "
    else:
        if variant == "head":
            snapshot = snapshot.replace(
                candidate.evidence.trust_observation.logical_snapshot_head.encode(), b"another-head"
            )
        elif variant == "envelope":
            entries = json.loads(snapshot)
            envelope = base64.b64decode(entries[-1][2]).replace(b"BOOTSTRAP", b"REVOKE")
            entries[-1][2] = base64.b64encode(envelope).decode()
            snapshot = json.dumps(entries, separators=(",", ":")).encode()
        elif variant == "structure":
            snapshot = b'[["logical-snapshot"]]'
        elif variant == "encoding":
            snapshot = b'[["logical-snapshot",null,"@@@"]]'
        else:
            snapshot += b" "
        # Rehash the entire candidate: its consistency cannot conceal a bad
        # outer snapshot structure or a head different from the selected intent.
        evidence = candidate.evidence.model_copy(
            update={"trust_snapshot_digest": hashlib.sha256(snapshot).hexdigest()}
        )
        raw = H1OwnerCandidateCallV1(
            evidence=evidence,
            evidence_digest=hashlib.sha256(evidence.canonical_bytes()).hexdigest(),
        ).canonical_bytes()
    wire = TrustOwnerCall(
        mode="ISSUE_HERMETIC_OUTPUT_SCOPE_V1", snapshot_bytes=snapshot, request_bytes=raw
    )
    result = dispatch("deployment_trust.issue_hermetic_output_scope", wire.canonical_bytes())
    assert result["failure"] == "PROTOCOL_REJECTED"


@pytest.mark.parametrize("current", [False, True])
def test_real_owner_route_remains_unsupported(current: bool) -> None:
    candidate, snapshot = candidate_request()
    value = read_request() if current else candidate
    operation = "read_current_hermetic_output_scope" if current else "issue_hermetic_output_scope"
    mode: Literal["READ_CURRENT_HERMETIC_OUTPUT_SCOPE_V1", "ISSUE_HERMETIC_OUTPUT_SCOPE_V1"] = (
        "READ_CURRENT_HERMETIC_OUTPUT_SCOPE_V1" if current else "ISSUE_HERMETIC_OUTPUT_SCOPE_V1"
    )
    call = TrustOwnerCall(mode=mode, snapshot_bytes=snapshot, request_bytes=value.canonical_bytes())
    result = dispatch("deployment_trust." + operation, call.canonical_bytes())
    if not current and "payload" not in result:
        assert result["failure"] == "PROTOCOL_REJECTED"
        return
    assert isinstance(result["payload"], str)
    payload = json.loads(base64.b64decode(result["payload"]))
    assert payload["disposition"] == ("UNSUPPORTED" if current else "CANDIDATE")
    assert result["schema_id"] == (
        "chiplog.deployment-trust.current-hermetic-output-scope-result.v1"
        if current
        else "chiplog.deployment-trust.issue-hermetic-output-scope-result.v1"
    )
    for invalid in (
        b"{}",
        value.canonical_bytes() + b" ",
        b'{"schema_id":"malformed"}',
    ):
        result = dispatch(
            "deployment_trust." + operation,
            call.model_copy(update={"request_bytes": invalid}).canonical_bytes(),
        )
        assert result["failure"] == "PROTOCOL_REJECTED"
    assert (
        dispatch("deployment_trust.authenticate", call.canonical_bytes())["failure"]
        == "PROTOCOL_REJECTED"
    )


def test_legacy_wire_bytes_remain_exact() -> None:
    assert TrustOwnerCall(
        mode="AUTHENTICATE", snapshot_bytes=b"[]", request_bytes=b"{}"
    ).canonical_bytes() == (
        b'{"mode":"AUTHENTICATE","request_bytes":"e30=","snapshot_bytes":"W10="}'
    )
    assert TrustOwnerResult(
        disposition="DENIED", reference_bytes=None, reason="denied"
    ).canonical_bytes() == (b'{"disposition":"DENIED","reason":"denied","reference_bytes":null}')
    with pytest.raises(ValidationError):
        TrustOwnerCall.model_validate(
            dict(mode="FUTURE", snapshot_bytes=b"[]", request_bytes=b"{}")
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("slot_id", "h1-cli-local"),
        ("schema_id", "chiplog.deployment-trust.issue-hermetic-output-scope.v2"),
        ("expected_revision", 1),
        ("expected_scope_predecessor", head("scope-predecessor")),
        ("expected_revision", True),
        ("expected_revision", "0"),
        ("recipient", {}),
        ("endpoint", b"local://hermetic-principal"),
        ("ordered_mandates", []),
        ("policy", {}),
    ],
)
def test_issue_rejects_wrong_shape_and_output_injection(field: str, value: Any) -> None:
    data = request().model_dump()
    data["authenticated_cli_ref"] = request().authenticated_cli_ref
    data[field] = value
    with pytest.raises(ValidationError):
        IssueHermeticOutputScopeV1.model_validate(data)


def test_issue_strictly_checks_preconstructed_trust_reference() -> None:
    original = request()
    data = original.model_dump()
    data["authenticated_cli_ref"] = replace(original.authenticated_cli_ref, freshness_sequence=True)
    with pytest.raises(ValidationError):
        IssueHermeticOutputScopeV1.model_validate(data)
    assert IssueHermeticOutputScopeV1.model_validate_json(original.canonical_bytes()) == original


@pytest.mark.parametrize("disposition", ["STALE", "DENIED", "UNSUPPORTED"])
def test_issue_negative_results_cannot_carry_authority(disposition: str) -> None:
    adapter: TypeAdapter[IssueHermeticOutputScopeResultV1] = TypeAdapter(
        IssueHermeticOutputScopeResultV1
    )
    assert (
        adapter.validate_json(json.dumps({"disposition": disposition})).disposition == disposition
    )
    with pytest.raises(ValidationError):
        adapter.validate_json(
            json.dumps({"disposition": disposition, "canonical_scope_bytes": "x"})
        )
    for malformed in [
        {"disposition": "ISSUED"},
        {"disposition": "REPLAY"},
        {"disposition": "VALID"},
    ]:
        with pytest.raises(ValidationError):
            adapter.validate_json(json.dumps(malformed))


def test_protected_slot_has_no_caller_output_fields() -> None:
    slot = configured_h1_effects_origin_slot(UnimplementedVerifier())
    assert slot.slot_id == "h1-cli-effects-origin"
    assert slot.ordered_mandates == ()
    with pytest.raises(FrozenInstanceError):
        slot.slot_id = "h1-cli-local"  # type: ignore[misc, assignment]
    with pytest.raises(TypeError):
        configured_h1_effects_origin_slot(None)  # type: ignore[arg-type]


def test_materialized_locator_is_exact_and_restartable(tmp_path: Path) -> None:
    database = tmp_path / "trust.sqlite3"
    adapter = SQLiteTrustMaterializer(database)
    adapter.materialize("d1", (b"decision one", b"record one"))
    adapter.materialize("d2", (b"decision two", b"record two"))
    assert adapter.record("d1", 1) == b"record one"
    assert adapter.record("d2", 1) == b"record two"
    assert adapter.record("d1", 2) is None
    assert adapter.record("absent", 1) is None
    adapter.close()
    reopened = SQLiteTrustMaterializer(database)
    assert reopened.record("d1", 1) == b"record one"
    reopened.close()


@pytest.mark.parametrize("corruption", ["bytes", "missing", "ordinal", "orphan"])
def test_materialized_locator_rejects_inconsistent_decision(
    tmp_path: Path, corruption: str
) -> None:
    database = tmp_path / "trust.sqlite3"
    adapter = SQLiteTrustMaterializer(database)
    adapter.materialize("d", (b"decision", b"scope"))
    with sqlite3.connect(database) as connection:
        if corruption == "bytes":
            connection.execute(
                "UPDATE trust_records SET canonical_bytes = ? WHERE ordinal = 1", (b"forged",)
            )
        elif corruption == "missing":
            connection.execute("DELETE FROM trust_records WHERE ordinal = 1")
        elif corruption == "ordinal":
            connection.execute("UPDATE trust_records SET ordinal = 4 WHERE ordinal = 1")
        else:
            connection.execute("DELETE FROM trust_decisions")
    with pytest.raises(RuntimeError, match="materialization"):
        adapter.record("d", 1)
    adapter.close()


@pytest.mark.parametrize("ordinal", [-1, True, "1", 2**63])
def test_materialized_locator_rejects_invalid_ordinals(tmp_path: Path, ordinal: Any) -> None:
    adapter = SQLiteTrustMaterializer(tmp_path / "trust.sqlite3")
    with pytest.raises(ValueError, match="ordinal"):
        adapter.record("d", ordinal)
    adapter.close()


@pytest.mark.parametrize("current", [False, True])
async def test_mounted_owner_contract_route_does_not_write(tmp_path: Path, current: bool) -> None:
    from chiplog.composition.common_cli_execution_runtime import open_common_cli_execution_runtime
    from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources
    from chiplog.platform.broker import PublicPortRejected, PublicPortSuccess

    resources = HermeticDispatchResources(scenarios=("CONFIRM",), cap=1)
    async with open_common_cli_execution_runtime(
        tmp_path / "mounted.sqlite", resources=resources
    ) as runtime:
        before = runtime._trust.owner_snapshot_entries()
        candidate, snapshot = candidate_request()
        value = read_request() if current else candidate
        operation = (
            "read_current_hermetic_output_scope" if current else "issue_hermetic_output_scope"
        )
        mode: Literal["READ_CURRENT_HERMETIC_OUTPUT_SCOPE_V1", "ISSUE_HERMETIC_OUTPUT_SCOPE_V1"] = (
            "READ_CURRENT_HERMETIC_OUTPUT_SCOPE_V1" if current else "ISSUE_HERMETIC_OUTPUT_SCOPE_V1"
        )
        wire = TrustOwnerCall(
            mode=mode, snapshot_bytes=snapshot, request_bytes=value.canonical_bytes()
        )
        template = runtime._trust_call_request("AUTHENTICATE", {})
        call = template.model_copy(
            update={
                "operation_id": "deployment_trust." + operation,
                "canonical_payload": wire.canonical_bytes(),
            }
        )
        response = await runtime._supervisor.runtime().call(call)
        if not current:
            assert isinstance(response, PublicPortRejected)
            assert runtime._trust.owner_snapshot_entries() == before
            return
        assert isinstance(response, PublicPortSuccess)
        payload = json.loads(response.canonical_payload)
        assert payload["disposition"] == "UNSUPPORTED"
        assert runtime._trust.owner_snapshot_entries() == before


async def test_planning_only_r7_rejects_h1_route(tmp_path: Path) -> None:
    from chiplog.composition.r7_planning import open_r7_runtime
    from chiplog.platform.broker import PublicPortRejected

    async with open_r7_runtime(
        tmp_path / "planning.sqlite", tenant_id="hermetic-tenant", operator_secret=b"test"
    ) as runtime:
        await runtime.bootstrap(
            database_instance_id="db",
            principal_id="hermetic-principal",
            credential_id="credential",
            session_id="session",
            token="bootstrap",
        )
        before = runtime._trust.owner_snapshot_entries()
        wire = TrustOwnerCall(
            mode="ISSUE_HERMETIC_OUTPUT_SCOPE_V1",
            snapshot_bytes=b"[]",
            request_bytes=request().canonical_bytes(),
        )
        template = runtime._trust_call_request("AUTHENTICATE", {})
        call = template.model_copy(
            update={
                "operation_id": "deployment_trust.issue_hermetic_output_scope",
                "canonical_payload": wire.canonical_bytes(),
            }
        )
        response = await runtime._supervisor.runtime().call(call)
        assert isinstance(response, PublicPortRejected)
        assert runtime._trust.owner_snapshot_entries() == before


def test_current_request_rejects_anchor_mismatch_and_policy_injection() -> None:
    original = read_request()
    for field, value in (
        ("schema_id", "future"),
        ("expected_revision", 1),
        ("policy", {}),
        ("ordered_mandates", []),
    ):
        data = json.loads(original.canonical_bytes())
        data[field] = value
        with pytest.raises(ValidationError):
            ReadCurrentHermeticExecutionScopeV1.model_validate_json(json.dumps(data))
