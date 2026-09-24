"""Admitted-inbox initialization delegates only the native v2 Create path."""

import base64
import hashlib
import json

import pytest
from pydantic import TypeAdapter
from tests.support.execution_fan_out import fixture
from tests.support.fan_out_shapes import head

from chiplog.capabilities.agent_loop import _execution_h0_process as process
from chiplog.capabilities.agent_loop.call_acceptance_contracts import (
    CallPreparationRejected,
    CallSubjectHead,
)
from chiplog.capabilities.agent_loop.execution_inbox_initialization import (
    ExecutionInboxInitialization,
)
from chiplog.capabilities.agent_loop.execution_initialization_contracts import (
    AdmittedExecutionBinding,
    ExecutionInitializationCut,
    ExecutionInitializationResult,
    PreparedExecutionInitialization,
    PrepareInboxExecution,
    SelectedAdmittedRunInput,
)
from chiplog.capabilities.agent_loop.execution_transition_contracts import CreateExecutionRun
from chiplog.capabilities.agent_loop.recovery_contracts import Absent, Present


async def _request() -> PrepareInboxExecution:
    captured = await fixture()
    run = captured.captured_run
    create = CreateExecutionRun(
        command_id="create",
        tenant=run.tenant,
        principal=run.principal,
        run_id=run.run_id,
        prompt=run.prompt,
        policy=run.policy,
        origin=run.origin,
        contour_head=run.contour_head,
        policy_head=run.policy_head,
        worker_session=run.worker_session,
    )
    inbox = CallSubjectHead(
        subject_id=run.origin.ingress_binding.identity,
        revision=Present(
            head=run.origin.ingress_binding.head,
            fingerprint=run.origin.ingress_binding.fingerprint,
        ),
    )
    embedded_inbox = b'{"schema_id":"chiplog.ingress.embedded-cli-inbox.v1"}'
    admitted = SelectedAdmittedRunInput(
        tenant_id=run.tenant,
        database_id="database",
        source_class="CLI",
        source_contract=head("contract"),
        token=head("token"),
        custody=head("custody"),
        inbox=inbox,
        selected_decision=head("decision"),
        physical_record=head("physical"),
        commit_sequence=1,
        raw_input_bytes=run.prompt.encode(),
        custody_schema="custody.v1",
        canonical_custody_record=b'{"schema_id":"chiplog.ingress.authenticated-record.v2"}',
        inbox_schema="chiplog.ingress.embedded-cli-inbox.v1",
        canonical_inbox_record=embedded_inbox,
        source_authentication=head("authentication"),
        authentication_schema="auth.v1",
        canonical_authentication=b"authentication",
        normalization=inbox,
        normalization_schema="chiplog.ingress.embedded-cli-inbox.v1",
        canonical_normalization_record=embedded_inbox,
        normalized_prompt=run.prompt,
        principal_id=run.principal,
        contour_head=run.contour_head,
        origin=run.origin,
    )
    return PrepareInboxExecution(
        create=create,
        admitted=admitted,
        cut=ExecutionInitializationCut(
            tenant_id=run.tenant,
            database_id="database",
            tenant_commit_sequence=1,
            materialization_commitment="a" * 64,
            initial_run_absence=Absent(),
            worker_session_id=run.worker_session,
            runtime_generation="runtime",
            authority_registry=head("registry"),
            sources=captured.request.cut.sources,
        ),
    )


def _proposal_fingerprint(value: PreparedExecutionInitialization) -> str:
    wire = json.loads(value.canonical_bytes())
    del wire["proposal_fingerprint"]
    return hashlib.sha256(
        json.dumps(wire, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


async def test_admitted_cli_input_creates_native_v2_run_with_original_binding() -> None:
    request = await _request()

    result = await ExecutionInboxInitialization().prepare_initial_execution(request)

    assert isinstance(result, PreparedExecutionInitialization)
    assert result.source_request_fingerprint == request.digest()
    assert result.run.schema_id == "chiplog.agent-loop.execution-record.v2"
    assert result.run.tenant == request.admitted.tenant_id
    assert result.run.principal == request.admitted.principal_id
    assert result.run.prompt == request.admitted.normalized_prompt
    assert result.run.origin == request.admitted.origin
    assert result.input_binding == AdmittedExecutionBinding(
        original_inbox=request.admitted.inbox,
        original_custody=request.admitted.custody,
        normalization=request.admitted.normalization,
    )
    assert result.proposal_fingerprint == _proposal_fingerprint(result)


@pytest.mark.parametrize(
    "mutation",
    [
        "tenant",
        "database",
        "principal",
        "prompt",
        "origin",
        "inbox_origin",
        "contour",
        "worker",
        "absence",
    ],
)
async def test_incoherent_admitted_create_or_cut_rejects_before_run_creation(
    mutation: str,
) -> None:
    request = await _request()
    admitted, create, cut = request.admitted, request.create, request.cut
    if mutation == "tenant":
        admitted = admitted.model_copy(update={"tenant_id": "other-tenant"})
    elif mutation == "database":
        cut = cut.model_copy(update={"database_id": "other-database"})
    elif mutation == "principal":
        create = create.model_copy(update={"principal": "other-principal"})
    elif mutation == "prompt":
        create = create.model_copy(update={"prompt": "other prompt"})
    elif mutation == "origin":
        origin = create.origin.model_copy(
            update={
                "ingress_binding": create.origin.ingress_binding.model_copy(
                    update={"identity": "other-ingress"}
                )
            }
        )
        create = create.model_copy(update={"origin": origin})
    elif mutation == "inbox_origin":
        inbox = head("other-inbox")
        admitted = admitted.model_copy(update={"inbox": inbox, "normalization": inbox})
    elif mutation == "contour":
        create = create.model_copy(update={"contour_head": "other-contour"})
    elif mutation == "worker":
        cut = cut.model_copy(update={"worker_session_id": "other-worker"})
    else:
        cut = cut.model_copy(update={"initial_run_absence": head("present").revision})
    changed = request.model_copy(update={"admitted": admitted, "create": create, "cut": cut})

    result = await ExecutionInboxInitialization().prepare_initial_execution(changed)

    assert isinstance(result, CallPreparationRejected)
    assert result.command_id == request.create.command_id
    assert result.code in {"INTEGRITY_FAULT", "STALE"}


async def test_altered_retained_raw_bytes_reparse_and_reject() -> None:
    request = await _request()
    changed = request.model_copy(
        update={"admitted": request.admitted.model_copy(update={"raw_input_bytes": b"changed"})}
    )

    result = await ExecutionInboxInitialization().prepare_initial_execution(changed)

    assert isinstance(result, CallPreparationRejected)
    assert result.command_id == request.create.command_id
    assert result.code == "INTEGRITY_FAULT"


async def test_owner_process_roundtrips_canonical_inbox_initialization_only() -> None:
    request = await _request()

    reply = process.dispatch(process.OPERATION, request.canonical_bytes())

    assert reply["schema_id"] == process.RESULT_SCHEMA
    result: ExecutionInitializationResult = TypeAdapter(
        ExecutionInitializationResult
    ).validate_json(base64.b64decode(reply["payload"]))
    assert isinstance(result, PreparedExecutionInitialization)
    assert result.source_request_fingerprint == request.digest()
    rejected = process.dispatch(process.OPERATION, request.canonical_bytes() + b" ")
    assert rejected["failure"] == "PROTOCOL_REJECTED"
