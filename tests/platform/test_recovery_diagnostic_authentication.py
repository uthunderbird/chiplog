"""Structural publication boundary for broker-issued recovery diagnostics."""

from typing import Literal, get_args

import pytest
from pydantic import TypeAdapter, ValidationError

from chiplog.platform import operation_registry_contracts as registry
from chiplog.platform._owner_publication_contracts import (
    AuthoritativeReadManifest,
    BrokerOperation,
    BrokerRecoveryDiagnosticAuthenticationV1,
    ExactRecordHead,
    InvocationProofRef,
    OwnerCommandBytes,
    OwnerRecordBytes,
    PlanEffectBatch,
    PublicationAuthentication,
    PublicationIdentity,
    SingleOwnerBatch,
    WorkerAuthentication,
)
from chiplog.platform.recovery_diagnostic_refs import BrokerDiagnosticSourceRefV1
from chiplog.platform.runtime_surface_contracts import ExecutableSymbol

SourceKind = Literal[
    "FAULT_CAPTURE", "FAULT_OBSERVATION", "FAULT_OBSERVER_REGISTRY", "FAULT_ISSUANCE"
]


def _source(kind: SourceKind) -> BrokerDiagnosticSourceRefV1:
    schemas: dict[SourceKind, str] = {
        "FAULT_CAPTURE": "chiplog.broker.fault-capture.v1",
        "FAULT_OBSERVATION": "chiplog.broker.fault-observation.v1",
        "FAULT_OBSERVER_REGISTRY": "chiplog.broker.fault-observer-registry.v1",
        "FAULT_ISSUANCE": "chiplog.broker.fault-diagnostic-issuance.v1",
    }
    return BrokerDiagnosticSourceRefV1(
        source_kind=kind, schema_id=schemas[kind], source_id=kind.lower(), fingerprint="a" * 64
    )


def _authentication() -> BrokerRecoveryDiagnosticAuthenticationV1:
    return BrokerRecoveryDiagnosticAuthenticationV1(
        invocation=InvocationProofRef(
            issuance_id="issuance-id",
            issuance_fingerprint="b" * 64,
            broker_epoch="epoch",
            broker_session="session",
            runtime_generation="generation",
            operation_subject="f" * 64,
        ),
        observer_registry=_source("FAULT_OBSERVER_REGISTRY"),
        diagnostic_observation=_source("FAULT_OBSERVATION"),
        issuance=_source("FAULT_ISSUANCE"),
        original_run=ExactRecordHead(
            owner="agent_loop",
            record_kind="Run",
            subject_id="run",
            record_id="run:head",
            fingerprint="c" * 64,
        ),
        diagnostic_cut_fingerprint="d" * 64,
        source_capture_inventory_fingerprint="e" * 64,
        observation_subject_fingerprint="f" * 64,
        final_request_fingerprint="1" * 64,
    )


def _expected() -> AuthoritativeReadManifest:
    return AuthoritativeReadManifest(
        tenant_id="tenant",
        tenant_frontier=0,
        expected_materialization_commitment="2" * 64,
        registry_head="registry:head",
        registry_fingerprint="3" * 64,
        ordered_heads=(),
        complete_manifest_fingerprint="4" * 64,
    )


def _fault_batch() -> SingleOwnerBatch:
    digest = "1" * 64
    return SingleOwnerBatch(
        operation="recovery.publish_terminal_fault",
        identity=PublicationIdentity(
            tenant_id="tenant",
            command_id="command",
            command_fingerprint=digest,
            canonicalization_version="chiplog.owner-publication.v1",
        ),
        authentication=_authentication(),
        expected=_expected(),
        command=OwnerCommandBytes(
            owner="agent_loop",
            schema_id="chiplog.execution.prepare-terminal-recovery-fault.v1",
            canonical_bytes=b"fault-request",
            fingerprint=digest,
        ),
        complete_records=(
            OwnerRecordBytes(
                owner="agent_loop",
                record_kind="TERMINAL_RECOVERY_FAULT",
                record_id="fault:record",
                schema_id="chiplog.execution.terminal-recovery-fault.v1",
                canonical_bytes=b"fault-record",
                fingerprint="5" * 64,
            ),
        ),
        complete_batch_fingerprint="6" * 64,
    )


def _symbol(name: str) -> ExecutableSymbol:
    return ExecutableSymbol(
        module="fixture", qualified_name=name, source_path="fixture.py", source_fingerprint="a" * 64
    )


def test_broker_diagnostic_authentication_is_closed_and_roundtrips() -> None:
    authentication = _authentication()
    adapter: TypeAdapter[PublicationAuthentication] = TypeAdapter(PublicationAuthentication)
    assert adapter.validate_json(authentication.model_dump_json()) == authentication
    assert TypeAdapter(BrokerOperation).validate_python("recovery.publish_terminal_fault") == (
        "recovery.publish_terminal_fault"
    )
    with pytest.raises(ValidationError):
        BrokerDiagnosticSourceRefV1.model_validate(
            {
                "source_owner": "agent_loop",
                "source_kind": "FAULT_ISSUANCE",
                "schema_id": "chiplog.broker.fault-diagnostic-issuance.v1",
                "source_id": "issuance",
                "fingerprint": "a" * 64,
            }
        )


def test_diagnostic_authentication_requires_exact_fault_publication_shape() -> None:
    batch = _fault_batch()
    assert batch.authentication == _authentication()
    for mutation in (
        {"operation": "recovery.suspend"},
        {"complete_records": batch.complete_records * 2},
        {"complete_records": (batch.complete_records[0].model_copy(update={"owner": "effects"}),)},
        {
            "complete_records": (
                batch.complete_records[0].model_copy(update={"record_kind": "Run"}),
            )
        },
        {
            "complete_records": (
                batch.complete_records[0].model_copy(update={"schema_id": "other.v1"}),
            )
        },
    ):
        with pytest.raises(ValidationError):
            SingleOwnerBatch.model_validate({**batch.model_dump(), **mutation})


def test_diagnostic_authentication_requires_each_final_request_digest_join() -> None:
    batch = _fault_batch()
    mismatches = (
        {"identity": batch.identity.model_copy(update={"command_fingerprint": "7" * 64})},
        {"command": batch.command.model_copy(update={"fingerprint": "7" * 64})},
        {
            "authentication": batch.authentication.model_copy(
                update={"final_request_fingerprint": "7" * 64}
            )
        },
    )
    for mutation in mismatches:
        with pytest.raises(ValidationError, match="final request fingerprint"):
            SingleOwnerBatch.model_validate({**batch.model_dump(), **mutation})


@pytest.mark.parametrize(
    ("operation_subject", "observation_subject_fingerprint", "valid"),
    (
        ("f" * 64, "f" * 64, True),
        ("a" * 64, "a" * 64, True),
        ("a" * 64, "b" * 64, False),
    ),
)
def test_diagnostic_authentication_binds_invocation_to_observation_subject(
    operation_subject: str, observation_subject_fingerprint: str, valid: bool
) -> None:
    authentication = _authentication().model_copy(
        update={
            "invocation": _authentication().invocation.model_copy(
                update={"operation_subject": operation_subject}
            ),
            "observation_subject_fingerprint": observation_subject_fingerprint,
        }
    )
    if valid:
        assert (
            BrokerRecoveryDiagnosticAuthenticationV1.model_validate(authentication.model_dump())
            == authentication
        )
    else:
        with pytest.raises(ValidationError, match="invocation subject"):
            BrokerRecoveryDiagnosticAuthenticationV1.model_validate(authentication.model_dump())


@pytest.mark.parametrize(
    ("owner", "schema_id", "valid"),
    (
        ("agent_loop", "chiplog.execution.prepare-terminal-recovery-fault.v1", True),
        ("effects", "chiplog.execution.prepare-terminal-recovery-fault.v1", False),
        ("agent_loop", "chiplog.execution.prepare-terminal-recovery-fault.v2", False),
    ),
)
def test_diagnostic_authentication_binds_fixed_fault_command_role(
    owner: str, schema_id: str, valid: bool
) -> None:
    batch = _fault_batch()
    command = batch.command.model_copy(update={"owner": owner, "schema_id": schema_id})
    if valid:
        assert SingleOwnerBatch.model_validate({**batch.model_dump(), "command": command}) == batch
    else:
        with pytest.raises(ValidationError, match="fixed terminal fault command"):
            SingleOwnerBatch.model_validate({**batch.model_dump(), "command": command})


@pytest.mark.parametrize(
    ("field", "kind"),
    (
        ("observer_registry", "FAULT_ISSUANCE"),
        ("diagnostic_observation", "FAULT_CAPTURE"),
        ("issuance", "FAULT_OBSERVATION"),
    ),
)
def test_diagnostic_authentication_binds_each_reference_role_to_its_source_kind(
    field: str, kind: SourceKind
) -> None:
    data = _authentication().model_dump()
    data[field] = _source(kind).model_dump()
    with pytest.raises(ValidationError):
        BrokerRecoveryDiagnosticAuthenticationV1.model_validate(data)


def test_diagnostic_authentication_cannot_inhabit_another_batch_variant() -> None:
    authentication = _authentication().model_dump()
    with pytest.raises(ValidationError):
        PlanEffectBatch.model_validate(
            {
                "identity": _fault_batch().identity.model_dump(),
                "authentication": authentication,
                "expected": _expected().model_dump(),
                "planning_command": _fault_batch().command.model_dump(),
                "effects_command": _fault_batch()
                .command.model_copy(update={"owner": "effects"})
                .model_dump(),
                "complete_records": _fault_batch().complete_records * 2,
                "complete_batch_fingerprint": "6" * 64,
            }
        )
    assert PlanEffectBatch.model_fields["authentication"].annotation is WorkerAuthentication


def test_registry_authentication_kind_permits_no_worker_kind() -> None:
    assert "BROKER_RECOVERY_DIAGNOSTIC" in get_args(registry.AuthenticationKindV1)
    assert (
        registry.ApplicabilityRefV1(
            authentication_kind="BROKER_RECOVERY_DIAGNOSTIC",
            worker_kind=None,
            proof=registry.WireRefV1(
                schema_id="proof.v1",
                schema_fingerprint="a" * 64,
                canonicalization_id="canonical.v1",
                decoder_symbol=_symbol("decode"),
            ),
            current_validator=_symbol("validate"),
            permitted_record_roles=("fault",),
        ).worker_kind
        is None
    )
