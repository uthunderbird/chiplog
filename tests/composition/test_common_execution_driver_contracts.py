"""Public decoding checks for the common execution driver wire."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Literal

import pytest
from pydantic import TypeAdapter, ValidationError

from chiplog.adapters.driven.ingress_retained_source import RetainedSourceAdapter
from chiplog.capabilities.agent_loop.contracts import RunState
from chiplog.capabilities.agent_loop.recovery_contracts import Absent, Present
from chiplog.composition import common_execution_driver_contracts as driver
from chiplog.platform import ingress_source_contracts as source_contracts
from chiplog.platform import ingress_transition_contracts as ingress
from chiplog.platform._ingress_contracts import Head, SourceBinding, UnknownEndpoint


def _head(name: str) -> Head:
    return Head(identity=name, head=f"{name}/head", fingerprint="a" * 64)


def _binding(
    source_class: Literal["CLI", "TELEGRAM_PUSH", "TELEGRAM_POLL"] = "CLI",
) -> SourceBinding:
    return SourceBinding(
        manifest_row=_head("source-manifest"),
        source_class=source_class,
        tenant_id="tenant",
        database_id="database",
        source_identity="source",
        endpoint_account_binding=UnknownEndpoint(),
        broker_epoch="epoch",
        broker_session="session",
        admission_epoch=_head("admission"),
        admission_fence=4,
        transport_version="transport.v1",
    )


def _ingress_identity(command_id: str = "ingress-command") -> ingress.IngressCommandIdentity:
    return ingress.IngressCommandIdentity(
        tenant_id="tenant", database_id="database", command_id=command_id
    )


def _identity(command_id: str = "driver-command") -> driver.DriverCommandIdentityV1:
    return driver.DriverCommandIdentityV1(
        tenant_id="tenant",
        database_id="database",
        driver_command_id=command_id,
        original_ingress_identity=_ingress_identity(command_id),
        original_ingress_request_fingerprint="b" * 64,
    )


def _observed_source(
    observation: source_contracts.IngressSourceObservationV1,
) -> ingress.RetainedIngressSource:
    return ingress.RetainedIngressSource(
        source=observation.source_head,
        reader_id=observation.reader_id,
        schema_id=observation.schema_id,
        canonical_source_bytes=observation.canonical_bytes(),
    )


def _selected_source(kind: str, tmp_path: Path) -> driver.DriverSelectedSourceV1:
    identity = _ingress_identity()
    if kind == "CLI_PEER":
        retained = _observed_source(
            source_contracts.CliPeerObservation(
                source_binding=_binding(),
                source_head=_head("CLI/source"),
                original_identity="original",
                original_bytes=b"same-text-input",
                proof_head=_head("CLI/proof"),
            )
        )
        return driver.CliPeerSelectedSourceV1(
            original_ingress_identity=identity,
            source_binding=_binding(),
            selected_ingress_decision=_head("ingress-decision"),
            source_head=retained.source,
            retained_source=retained,
        )
    if kind == "TELEGRAM_PUSH":
        retained = _observed_source(
            source_contracts.TelegramPushObservation(
                source_binding=_binding("TELEGRAM_PUSH"),
                source_head=_head("TELEGRAM_PUSH/source"),
                original_identity="original",
                original_bytes=b"same-text-input",
                proof_head=_head("TELEGRAM_PUSH/proof"),
            )
        )
        return driver.TelegramPushSelectedSourceV1(
            original_ingress_identity=identity,
            source_binding=_binding("TELEGRAM_PUSH"),
            selected_ingress_decision=_head("ingress-decision"),
            source_head=retained.source,
            retained_source=retained,
        )
    if kind == "TELEGRAM_POLL":
        retained = _observed_source(
            source_contracts.TelegramPollObservation(
                source_binding=_binding("TELEGRAM_POLL"),
                source_head=_head("TELEGRAM_POLL/source"),
                original_identity="original",
                original_bytes=b"same-text-input",
                proof_head=_head("TELEGRAM_POLL/proof"),
            )
        )
        return driver.TelegramPollSelectedSourceV1(
            original_ingress_identity=identity,
            source_binding=_binding("TELEGRAM_POLL"),
            selected_ingress_decision=_head("ingress-decision"),
            source_head=retained.source,
            retained_source=retained,
        )
    directory = tmp_path / "retained-source"
    adapter = RetainedSourceAdapter(
        directory, "tenant", "database", secret=b"r" * 32, allow_create=True
    )
    adapter.provision("retained-slot", b"same-text-input")
    observation = adapter.observe("retained-slot")
    retained = ingress.RetainedIngressSource(
        source=observation.proof,
        reader_id="deployed-retained-cli-reader.v1",
        schema_id="chiplog.ingress.retained-source-observation.v1",
        canonical_source_bytes=observation.canonical_bytes(),
    )
    return driver.CliRetainedSelectedSourceV1(
        original_ingress_identity=identity,
        source_binding=_binding(),
        selected_ingress_decision=_head("ingress-decision"),
        source_head=retained.source,
        retained_source=retained,
        expected_reader_id="deployed-retained-cli-reader.v1",
    )


def _request(
    kind: str, tmp_path: Path, command_id: str = "driver-command"
) -> driver.DriveInputRequestV1:
    selected = _selected_source(kind, tmp_path)
    return driver.DriveInputRequestV1(
        identity=_identity(command_id),
        selected_source=selected.model_copy(
            update={"original_ingress_identity": _ingress_identity(command_id)}
        ),
    )


def _selected(
    request: driver.DriveInputRequestV1,
    phase: Literal["INITIALIZED", "RUNNING", "SUSPENDED", "TERMINAL"],
    detail: driver.TerminalDetailV1 | None = None,
    selected_run_state: RunState | None = None,
) -> driver.SelectedExecutionReceiptV1:
    phase_state: dict[Literal["INITIALIZED", "RUNNING", "SUSPENDED", "TERMINAL"], RunState] = {
        "INITIALIZED": "CREATED",
        "RUNNING": "ACTIVE",
        "SUSPENDED": "SUSPENDED",
        "TERMINAL": "SUCCEEDED",
    }
    return driver.SelectedExecutionReceiptV1(
        disposition="COMMITTED",
        identity=request.identity,
        original_driver_command_fingerprint=request.original_driver_command_fingerprint(),
        selected_ingress_decision=_head("ingress-decision"),
        selected_custody=_head("custody"),
        selected_admitted_input=_head("admitted"),
        stable_run_lineage_id="run-lineage",
        selected_run_head=_head("run"),
        selected_run_state=selected_run_state or phase_state[phase],
        selected_journal_decision=_head("journal-decision"),
        commit_sequence=7,
        phase=phase,
        terminal_detail=detail,
    )


@pytest.mark.parametrize("kind", ("CLI_PEER", "CLI_RETAINED", "TELEGRAM_PUSH", "TELEGRAM_POLL"))
def test_drive_request_public_union_retains_each_registered_original_source_route(
    kind: str, tmp_path: Path
) -> None:
    request = _request(kind, tmp_path)
    adapter: TypeAdapter[driver.DriveInputRequestV1] = TypeAdapter(driver.DriveInputRequestV1)
    assert adapter.validate_json(request.model_dump_json()) == request
    assert request.selected_source.retained_source.canonical_source_bytes
    assert request.selected_source.source_head == request.selected_source.retained_source.source


def test_identical_input_bytes_with_distinct_original_ingress_identities_have_distinct_driver_keys(
    tmp_path: Path,
) -> None:
    first = _request("CLI_PEER", tmp_path, "first")
    second = _request("CLI_PEER", tmp_path, "second")
    assert first.selected_source.retained_source.canonical_source_bytes == (
        second.selected_source.retained_source.canonical_source_bytes
    )
    assert first.identity != second.identity
    assert (
        first.original_driver_command_fingerprint() != second.original_driver_command_fingerprint()
    )


@pytest.mark.parametrize("phase", ("INITIALIZED", "RUNNING", "SUSPENDED"))
def test_selected_receipt_requires_terminal_detail_only_at_terminal(
    phase: Literal["INITIALIZED", "RUNNING", "SUSPENDED"], tmp_path: Path
) -> None:
    request = _request("CLI_PEER", tmp_path)
    receipt = _selected(request, phase)
    adapter: TypeAdapter[driver.CommonExecutionResultV1] = TypeAdapter(
        driver.CommonExecutionResultV1
    )
    assert adapter.validate_json(receipt.model_dump_json()) == receipt
    wire = receipt.model_dump()
    wire["terminal_detail"] = {
        "kind": "ACCEPTED",
        "acceptance_head": _head("acceptance").model_dump(),
        "delivery_manifest_head": _head("manifest").model_dump(),
        "committed_conversation_projection_head": _head("projection").model_dump(),
    }
    with pytest.raises(ValidationError):
        adapter.validate_python(wire)


def test_terminal_acceptance_and_semantic_rejection_preserve_their_distinct_projection_contracts(
    tmp_path: Path,
) -> None:
    request = _request("CLI_PEER", tmp_path)
    accepted = _selected(
        request,
        "TERMINAL",
        driver.AcceptedTerminalDetailV1(
            acceptance_head=_head("acceptance"),
            delivery_manifest_head=_head("delivery-manifest"),
            committed_conversation_projection_head=_head("conversation-projection"),
        ),
    )
    adapter: TypeAdapter[driver.CommonExecutionResultV1] = TypeAdapter(
        driver.CommonExecutionResultV1
    )
    assert adapter.validate_json(accepted.model_dump_json()) == accepted
    for projection in (Absent(), Present(head="projection/head", fingerprint="c" * 64)):
        rejected = _selected(
            request,
            "TERMINAL",
            driver.SemanticRejectedTerminalDetailV1(
                preserved_trace_head=_head("preserved-trace"),
                conversation_projection_before_and_after=projection,
                no_conversation_change_commitment="d" * 64,
            ),
            "ABORTED",
        )
        restored = adapter.validate_json(rejected.model_dump_json())
        assert restored == rejected
        assert restored.terminal_detail is not None
        assert isinstance(restored.terminal_detail, driver.SemanticRejectedTerminalDetailV1)
        assert restored.terminal_detail.ordered_members == ()
        assert "conversation_record" not in restored.terminal_detail.model_dump()
        fabricated_record = rejected.model_dump()
        fabricated_record["terminal_detail"]["conversation_record_head"] = _head(
            "fabricated-conversation-record"
        ).model_dump()
        with pytest.raises(ValidationError):
            adapter.validate_python(fabricated_record)
        fabricated_member = rejected.model_dump()
        fabricated_member["terminal_detail"]["ordered_members"] = ["fabricated-member"]
        with pytest.raises(ValidationError):
            adapter.validate_python(fabricated_member)
    wire = accepted.model_dump()
    wire["terminal_detail"]["kind"] = "SEMANTIC_REJECTED"
    with pytest.raises(ValidationError):
        adapter.validate_python(wire)


@pytest.mark.parametrize("kind", ("ABORTED", "CANCELLED"))
def test_generic_terminal_detail_matches_selected_terminal_run_state(
    kind: Literal["ABORTED", "CANCELLED"], tmp_path: Path
) -> None:
    request = _request("CLI_PEER", tmp_path)
    receipt = _selected(
        request,
        "TERMINAL",
        driver.OtherTerminalDetailV1(
            kind=kind,
            terminal_source_head=_head(f"{kind.lower()}-source"),
            terminal_manifest_head=(
                _head(f"{kind.lower()}-manifest") if kind == "ABORTED" else None
            ),
        ),
        kind,
    )
    adapter: TypeAdapter[driver.CommonExecutionResultV1] = TypeAdapter(
        driver.CommonExecutionResultV1
    )
    assert adapter.validate_json(receipt.model_dump_json()) == receipt
    mismatched = receipt.model_dump()
    mismatched["selected_run_state"] = "CANCELLED" if kind == "ABORTED" else "ABORTED"
    with pytest.raises(ValidationError):
        adapter.validate_python(mismatched)


@pytest.mark.parametrize(
    ("phase", "state"),
    (
        ("INITIALIZED", "ACTIVE"),
        ("RUNNING", "CREATED"),
        ("SUSPENDED", "ACTIVE"),
        ("TERMINAL", "SUPERSEDED"),
    ),
)
def test_selected_receipt_rejects_phase_and_selected_run_state_cross_products(
    phase: Literal["INITIALIZED", "RUNNING", "SUSPENDED", "TERMINAL"],
    state: RunState,
    tmp_path: Path,
) -> None:
    request = _request("CLI_PEER", tmp_path)
    valid_detail: driver.TerminalDetailV1 | None = None
    if phase == "TERMINAL":
        valid_detail = driver.AcceptedTerminalDetailV1(
            acceptance_head=_head("acceptance"),
            delivery_manifest_head=_head("delivery-manifest"),
            committed_conversation_projection_head=_head("conversation-projection"),
        )
    baseline = _selected(request, phase, valid_detail)
    wire = baseline.model_dump()
    wire["selected_run_state"] = state
    with pytest.raises(ValidationError):
        TypeAdapter(driver.CommonExecutionResultV1).validate_python(wire)


def test_pending_uncertain_and_integrity_rejection_are_closed_result_variants(
    tmp_path: Path,
) -> None:
    request = _request("CLI_PEER", tmp_path)
    fingerprint = request.original_driver_command_fingerprint()
    results: tuple[driver.CommonExecutionResultV1, ...] = (
        driver.ExecutionPendingReceiptV1(
            identity=request.identity,
            original_driver_command_fingerprint=fingerprint,
            phase="NO_RUN",
        ),
        driver.ExecutionPendingReceiptV1(
            identity=request.identity,
            original_driver_command_fingerprint=fingerprint,
            phase="RUNNING",
            selected_ingress_decision=_head("ingress-decision"),
            selected_admitted_input=_head("admitted"),
            selected_run_head=_head("run"),
        ),
        driver.ExecutionPendingReceiptV1(
            identity=request.identity,
            original_driver_command_fingerprint=fingerprint,
            phase="SUSPENDED",
            selected_ingress_decision=_head("ingress-decision"),
            selected_admitted_input=_head("admitted"),
            selected_run_head=_head("run"),
        ),
        driver.UncertainExecutionPublicationV1(
            identity=request.identity,
            original_driver_command_fingerprint=fingerprint,
            operation="publish",
            reason="writer-response-lost",
        ),
        driver.ExecutionDriverRejectedV1(
            identity=request.identity,
            original_driver_command_fingerprint=fingerprint,
            code="INTEGRITY_FAULT",
            reason="selected-bytes-corrupt",
        ),
    )
    adapter: TypeAdapter[driver.CommonExecutionResultV1] = TypeAdapter(
        driver.CommonExecutionResultV1
    )
    for result in results:
        assert adapter.validate_json(result.model_dump_json()) == result


@pytest.mark.parametrize("phase", ("RUNNING", "SUSPENDED"))
@pytest.mark.parametrize(
    "missing", ("selected_ingress_decision", "selected_admitted_input", "selected_run_head")
)
def test_pending_receipt_requires_selected_refs_after_a_run_exists(
    phase: Literal["RUNNING", "SUSPENDED"],
    missing: Literal["selected_ingress_decision", "selected_admitted_input", "selected_run_head"],
    tmp_path: Path,
) -> None:
    request = _request("CLI_PEER", tmp_path)
    baseline = driver.ExecutionPendingReceiptV1(
        identity=request.identity,
        original_driver_command_fingerprint=request.original_driver_command_fingerprint(),
        phase=phase,
        selected_ingress_decision=_head("ingress-decision"),
        selected_admitted_input=_head("admitted"),
        selected_run_head=_head("run"),
    )
    wire = baseline.model_dump()
    wire[missing] = None
    with pytest.raises(ValidationError):
        TypeAdapter(driver.CommonExecutionResultV1).validate_python(wire)


def test_no_run_pending_receipt_rejects_a_selected_run_head(tmp_path: Path) -> None:
    request = _request("CLI_PEER", tmp_path)
    baseline = driver.ExecutionPendingReceiptV1(
        identity=request.identity,
        original_driver_command_fingerprint=request.original_driver_command_fingerprint(),
        phase="NO_RUN",
    )
    wire = baseline.model_dump()
    wire["selected_run_head"] = _head("run").model_dump()
    with pytest.raises(ValidationError):
        TypeAdapter(driver.CommonExecutionResultV1).validate_python(wire)


@pytest.mark.parametrize(
    "mutate",
    (
        lambda wire: wire["selected_source"].update({"source_class": "TELEGRAM_PUSH"}),
        lambda wire: wire["selected_source"]["retained_source"].update(
            {"reader_id": "telegram-push-v1"}
        ),
        lambda wire: wire["selected_source"]["retained_source"].update(
            {"schema_id": "chiplog.ingress.source.telegram-push.v1"}
        ),
        lambda wire: wire["selected_source"]["source_binding"].update({"tenant_id": "other"}),
    ),
)
def test_public_request_decoder_rejects_source_class_reader_schema_and_binding_swaps(
    mutate: Callable[[dict[str, object]], None], tmp_path: Path
) -> None:
    request = _request("CLI_PEER", tmp_path)
    wire = request.model_dump()
    mutate(wire)
    with pytest.raises(ValidationError):
        TypeAdapter(driver.DriveInputRequestV1).validate_python(wire)


def test_public_decoders_reject_authority_fields_and_nonterminal_missing_detail(
    tmp_path: Path,
) -> None:
    request = _request("CLI_RETAINED", tmp_path)
    receipt = _selected(
        request,
        "TERMINAL",
        driver.AcceptedTerminalDetailV1(
            acceptance_head=_head("acceptance"),
            delivery_manifest_head=_head("delivery-manifest"),
            committed_conversation_projection_head=_head("conversation-projection"),
        ),
    )
    incomplete = receipt.model_dump()
    incomplete["terminal_detail"] = None
    with pytest.raises(ValidationError):
        TypeAdapter(driver.CommonExecutionResultV1).validate_python(incomplete)

    valid = _selected(request, "RUNNING")
    wire = valid.model_dump()
    wire["ack_permitted"] = True
    with pytest.raises(ValidationError):
        TypeAdapter(driver.CommonExecutionResultV1).validate_python(wire)
    with pytest.raises(ValidationError):
        TypeAdapter(ingress.IngressTransitionCommand).validate_json(valid.model_dump_json())


def test_lookup_retains_original_identity_and_driver_command_fingerprint(tmp_path: Path) -> None:
    request = _request("TELEGRAM_PUSH", tmp_path)
    lookup = driver.LookupExecutionRequestV1(
        identity=request.identity,
        original_driver_command_fingerprint=request.original_driver_command_fingerprint(),
    )
    assert (
        TypeAdapter(driver.LookupExecutionRequestV1).validate_json(lookup.model_dump_json())
        == lookup
    )


def test_retained_cli_placeholder_reader_is_never_a_wire_reader(tmp_path: Path) -> None:
    selected = _selected_source("CLI_RETAINED", tmp_path)
    wire = selected.model_dump()
    wire["expected_reader_id"] = "<root-deployed-retained-reader>"
    wire["retained_source"]["reader_id"] = "<root-deployed-retained-reader>"
    with pytest.raises(ValidationError):
        TypeAdapter(driver.DriverSelectedSourceV1).validate_python(wire)
