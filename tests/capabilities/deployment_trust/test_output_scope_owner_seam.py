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
    H1CurrentBrokerRouteV1,
    H1OwnerCandidateCallV1,
    H1OwnerCurrentCallV1,
    H1OwnerCurrentCandidateV1,
)
from chiplog.platform.r7_trust import TrustOwnerCall
from tests.support.capabilities.deployment_trust.output_scope_owner_seam import (
    head,
    read_request,
    request,
)


class UnimplementedVerifier:
    def resolve_selected_current(
        self,
        resource_ref: SelectedHermeticResourceObservationRefV1,
        admitted_authentication_ref: ExactHead,
        authenticated_cli_ref: TrustReference,
    ) -> VerifiedH1SelectedSources | None:
        raise AssertionError("contract-only seam must not pretend source verification exists")


def candidate_request() -> tuple[H1OwnerCandidateCallV1, bytes]:
    # Inert contract fixture: these bytes assert consistency, never provenance.
    from chiplog.platform.r7_trust import encode_trust_journal
    from tests.support.capabilities.deployment_trust.h1_broker_evidence_contracts import call

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
def test_owner_issue_candidate_and_invalid_direct_current_wire(current: bool) -> None:
    candidate, snapshot = candidate_request()
    value = read_request() if current else candidate
    operation = "read_current_hermetic_output_scope" if current else "issue_hermetic_output_scope"
    mode: Literal["READ_CURRENT_HERMETIC_OUTPUT_SCOPE_V1", "ISSUE_HERMETIC_OUTPUT_SCOPE_V1"] = (
        "READ_CURRENT_HERMETIC_OUTPUT_SCOPE_V1" if current else "ISSUE_HERMETIC_OUTPUT_SCOPE_V1"
    )
    call = TrustOwnerCall(mode=mode, snapshot_bytes=snapshot, request_bytes=value.canonical_bytes())
    result = dispatch("deployment_trust." + operation, call.canonical_bytes())
    if current or "payload" not in result:
        assert result["failure"] == "PROTOCOL_REJECTED"
        return
    assert isinstance(result["payload"], str)
    payload = json.loads(base64.b64decode(result["payload"]))
    assert payload["disposition"] == "CANDIDATE"
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


@pytest.mark.parametrize("revoked", [False, True])
def test_wrapped_current_read_checks_selected_credential(revoked: bool) -> None:
    from chiplog.platform.r7_trust import encode_trust_journal

    read = read_request()
    binding = {"tenant_id": read.tenant_id}
    ref = replace(
        read.authenticated_cli_ref,
        trust_head=hashlib.sha256(
            json.dumps(binding, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        freshness_sequence=1,
    )
    read = read.model_copy(update={"authenticated_cli_ref": ref})
    events = (
        ("INITIALIZE", {"binding": binding}),
        (
            "BOOTSTRAP",
            {
                "principal_id": ref.principal_id.value,
                "credential": {
                    "head": ref.credential_head,
                    "session_head": ref.session_head,
                    "peer_credential": ref.peer_credential,
                    "revoked": revoked,
                },
            },
        ),
    )
    entries: list[tuple[str, str | None, bytes]] = []
    predecessor = None
    for kind, payload in events:
        raw = json.dumps(
            {"kind": kind, "payload": payload, "predecessor": predecessor},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        identity = hashlib.sha256((predecessor or "GENESIS").encode() + b"\x00" + raw).hexdigest()
        entries.append((identity, predecessor, raw))
        predecessor = identity
    pinned = H1OwnerCurrentCallV1(
        route=H1CurrentBrokerRouteV1(
            tenant_id=read.tenant_id,
            broker_epoch=1,
            runtime_generation="generation",
            broker_session_id="broker-session",
            owner_session_id="owner-session",
            request_id="current-request",
        ),
        read_request_bytes=read.canonical_bytes(),
        request_digest=hashlib.sha256(read.canonical_bytes()).hexdigest(),
    )
    call = TrustOwnerCall(
        mode="READ_CURRENT_HERMETIC_OUTPUT_SCOPE_V1",
        snapshot_bytes=encode_trust_journal(tuple(entries)),
        request_bytes=pinned.canonical_bytes(),
    )
    result = dispatch("deployment_trust.read_current_hermetic_output_scope", call.canonical_bytes())
    if revoked:
        assert result["failure"] == "PROTOCOL_REJECTED"
    else:
        assert isinstance(result["payload"], str)
        candidate = H1OwnerCurrentCandidateV1.model_validate_json(
            base64.b64decode(result["payload"])
        )
        candidate.check_pinned_call(pinned)
        assert candidate.current.disposition == "CURRENT"


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
