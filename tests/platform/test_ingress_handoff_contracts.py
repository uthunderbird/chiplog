"""Consumer evidence for retained CLI and selected handoff record wires."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Literal

import pytest

from chiplog.adapters.driven.ingress_retained_source import (
    RetainedSourceAdapter,
    RetainedSourceObservation,
)
from chiplog.platform._ingress_contracts import (
    Head,
    SourceBinding,
    SourceClass,
    UnknownEndpoint,
)
from chiplog.platform.ingress_custody_records import reference
from chiplog.platform.ingress_record_contracts import (
    HandoffAttemptRecord,
    HandoffAuthorizationRecord,
    HandoffObservationRecord,
    decode_ingress_member,
    encode_ingress_member,
)
from chiplog.platform.ingress_source_contracts import (
    ProviderCallbackObservation,
    decode_ingress_source,
)
from chiplog.platform.ingress_transition_contracts import (
    IngressCommandIdentity,
    RetainedIngressSource,
)


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def head(name: str) -> Head:
    return reference(name, ("handoff-fixture:" + name).encode())


_PROVIDER_CUSTODY = head("provider-custody")


def binding(source_class: SourceClass, name: str) -> SourceBinding:
    return SourceBinding(
        manifest_row=head("manifest-" + name),
        source_class=source_class,
        tenant_id="tenant",
        database_id="database",
        source_identity="source-" + name,
        endpoint_account_binding=UnknownEndpoint(),
        broker_epoch="epoch-" + name,
        broker_session="session-" + name,
        admission_epoch=head("epoch-" + name),
        admission_fence=7,
        transport_version="transport-v1",
    )


def retained_cli(tmp_path: Path) -> tuple[RetainedIngressSource, SourceBinding]:
    adapter = RetainedSourceAdapter(
        tmp_path / "retained-cli",
        "tenant",
        "database",
        secret=b"retained-cli-handoff-fixture-secret",
        allow_create=True,
    )
    adapter.provision("cli-slot", b"\x00actual cli payload\xff")
    observed = adapter.observe("cli-slot")
    return (
        RetainedIngressSource(
            source=observed.proof,
            reader_id="root-deployed-retained-reader",
            schema_id="chiplog.ingress.retained-source-observation.v1",
            canonical_source_bytes=observed.canonical_bytes(),
        ),
        binding("CLI", "retained-cli"),
    )


def test_hermetic_cli_retained_file_observation_decodes_real_schema_and_rejects_substitution(
    tmp_path: Path,
) -> None:
    retained, cli_binding = retained_cli(tmp_path)

    decoded = decode_ingress_source(
        retained,
        expected_class="CLI",
        expected_binding=cli_binding,
        registered_reader_id="root-deployed-retained-reader",
    )
    assert isinstance(decoded, RetainedSourceObservation)
    assert decoded.proof == retained.source
    assert decoded.canonical_bytes() == retained.canonical_source_bytes
    assert decoded.raw_digest == digest(b"\x00actual cli payload\xff")

    with pytest.raises(ValueError, match="expected source class"):
        decode_ingress_source(
            retained,
            expected_class="CLI",
            expected_binding=binding("TOOL_RESULT", "wrong-binding"),
            registered_reader_id="root-deployed-retained-reader",
        )
    for field in ("tenant_id", "database_id"):
        with pytest.raises(ValueError, match="retained source scope"):
            decode_ingress_source(
                retained,
                expected_class="CLI",
                expected_binding=cli_binding.model_copy(update={field: "substituted-" + field}),
                registered_reader_id="root-deployed-retained-reader",
            )
    with pytest.raises(ValueError, match="retained source head"):
        decode_ingress_source(
            retained.model_copy(update={"source": head("substituted-source-head")}),
            expected_class="CLI",
            expected_binding=cli_binding,
            registered_reader_id="root-deployed-retained-reader",
        )
    with pytest.raises(ValueError, match="retained reader"):
        decode_ingress_source(
            retained.model_copy(update={"reader_id": "substituted-reader"}),
            expected_class="CLI",
            expected_binding=cli_binding,
            registered_reader_id="root-deployed-retained-reader",
        )


def command(name: str) -> IngressCommandIdentity:
    return IngressCommandIdentity(tenant_id="tenant", database_id="database", command_id=name)


def record_base(name: str, command_kind: str, source_heads: tuple[Head, ...]) -> dict[str, Any]:
    command_bytes = ("command-bytes:" + name).encode()
    return {
        "record_id": "record-" + name,
        "tenant_id": "tenant",
        "database_id": "database",
        "command": command("command-" + name),
        "command_kind": command_kind,
        "command_fingerprint": digest(command_bytes),
        "predecessor": head("predecessor-" + name),
        "source_heads": source_heads,
    }


def receipt_source() -> tuple[ProviderCallbackObservation, Head]:
    source = binding("PROVIDER_CALLBACK", "receipt")
    observation = ProviderCallbackObservation(
        source_binding=source,
        source_head=head("receipt-source"),
        original_identity="provider-receipt-identity",
        original_bytes=b"provider receipt bytes\x00",
        proof_head=head("receipt-proof"),
    )
    return observation, observation.source_head


def test_handoff_members_preserve_selected_refs_and_independent_receipt_evidence() -> None:
    receipt, receipt_head = receipt_source()
    auth = HandoffAuthorizationRecord(
        **record_base("authorization", "ingress.authorize_local_ack", (head("auth-source"),)),
        handoff_id="handoff-1",
        source_class="CLI",
        authorization_kind="ingress.authorize_local_ack",
        token=head("token"),
        custody=head("custody"),
        source_boundary=head("source-boundary"),
    )
    authorization_head = head("authorization-record")
    attempt = HandoffAttemptRecord(
        **record_base("attempt", "ingress.issue_cli_response_attempt", (head("attempt-source"),)),
        handoff_id=auth.handoff_id,
        attempt_id="attempt-1",
        authorization=authorization_head,
        endpoint=head("cli-endpoint"),
        payload=b"ack payload\x00",
    )
    attempt_head = head("attempt-record")
    unknown = HandoffObservationRecord(
        **record_base("unknown", "ingress.observe_cli_local_completion", (head("unknown-source"),)),
        handoff_id=auth.handoff_id,
        issued_attempt=attempt_head,
        authorization=None,
        result="UNKNOWN",
        observation=head("unknown-observation"),
        raw_observation_bytes=b"unknown transport outcome\xff",
        authenticated_source=None,
    )
    receipt_observation = HandoffObservationRecord(
        **record_base("receipt", "ingress.observe_authenticated_provider_receipt", (receipt_head,)),
        handoff_id="handoff-provider-receipt",
        issued_attempt=head("provider-attempt-record"),
        authorization=None,
        result="AUTHENTICATED_RECEIPT",
        observation=receipt_head,
        raw_observation_bytes=receipt.original_bytes,
        authenticated_source=receipt_head,
        selected_raw_custody=head("receipt-raw-custody"),
    )
    release = HandoffObservationRecord(
        **record_base(
            "release", "ingress.complete_reconciliation_release", (head("release-source"),)
        ),
        handoff_id="handoff-release",
        issued_attempt=None,
        authorization=head("release-authorization"),
        result="RELEASED",
        observation=head("release-observation"),
        raw_observation_bytes=b"",
        authenticated_source=None,
    )
    records = (auth, attempt, unknown, receipt_observation, release)
    members = tuple(encode_ingress_member(record) for record in records)

    decoded = tuple(decode_ingress_member(member) for member in members)
    assert decoded == records
    _, decoded_attempt, decoded_unknown, decoded_receipt, decoded_release = decoded
    assert decoded_attempt.authorization == authorization_head
    assert decoded_unknown.issued_attempt == attempt_head
    assert decoded_unknown.authorization is None
    assert decoded_unknown.raw_observation_bytes == b"unknown transport outcome\xff"
    assert decoded_receipt.authenticated_source == receipt_head
    assert decoded_receipt.selected_raw_custody == head("receipt-raw-custody")
    assert decoded_receipt.observation == receipt_head
    assert decoded_receipt.raw_observation_bytes == receipt.original_bytes
    assert decoded_receipt.source_heads == (receipt_head,)
    assert decoded_release.authorization == head("release-authorization")
    assert decoded_release.raw_observation_bytes == b""

    member = members[0]
    for mutant in (
        member.model_copy(update={"record_kind": "HANDOFF_ATTEMPT"}),
        member.model_copy(update={"schema_id": "chiplog.ingress.handoff-attempt-record.v1"}),
        member.model_copy(update={"record_id": "substituted-record-id"}),
        member.model_copy(update={"fingerprint": "0" * 64}),
    ):
        with pytest.raises(ValueError):
            decode_ingress_member(mutant)

    # The independently retained receipt source itself is canonical source evidence.
    retained_receipt = RetainedIngressSource(
        source=receipt_head,
        reader_id="provider-callback-v1",
        schema_id=receipt.schema_id,
        canonical_source_bytes=receipt.canonical_bytes(),
    )
    assert decode_ingress_source(
        retained_receipt,
        expected_class="PROVIDER_CALLBACK",
        expected_binding=receipt.source_binding,
        registered_reader_id="provider-callback-v1",
    ) == receipt


def test_handoff_observation_shape_matrix_rejects_invalid_serialized_rows() -> None:
    def local(
        result: Literal["LOCAL_COMPLETE", "FAILED", "UNKNOWN"] = "UNKNOWN",
    ) -> HandoffObservationRecord:
        return HandoffObservationRecord(
            **record_base(
                "local-" + result,
                "ingress.observe_cli_local_completion",
                (head("local-source"),),
            ),
            handoff_id="handoff-local",
            issued_attempt=head("local-attempt"),
            authorization=None,
            result=result,
            observation=head("local-observation-" + result),
            raw_observation_bytes=b"local bytes",
            authenticated_source=None,
        )

    assert local("LOCAL_COMPLETE").result == "LOCAL_COMPLETE"
    assert local("FAILED").result == "FAILED"
    valid = local()
    member = encode_ingress_member(valid)

    def serialized_mutant(**updates: object) -> None:
        raw = json.loads(valid.canonical_bytes())
        raw.update(updates)
        canonical = json.dumps(raw, sort_keys=True, separators=(",", ":")).encode()
        malformed = member.model_copy(
            update={
                "canonical_record_bytes": canonical,
                "fingerprint": digest(canonical),
            }
        )
        with pytest.raises(ValueError):
            decode_ingress_member(malformed)

    serialized_mutant(result="RELEASED")
    serialized_mutant(result="AUTHENTICATED_RECEIPT")
    serialized_mutant(authorization=head("unsupported-local-authorization").model_dump())
    serialized_mutant(authenticated_source=head("unsupported-local-source").model_dump())

    with pytest.raises(ValueError):
        HandoffObservationRecord(
            **record_base(
                "bad-provider",
                "ingress.observe_authenticated_provider_receipt",
                (head("p"),),
            ),
            handoff_id="provider",
            issued_attempt=head("provider-attempt"),
            authorization=None,
            result="AUTHENTICATED_RECEIPT",
            observation=head("provider-source"),
            raw_observation_bytes=b"receipt",
            authenticated_source=None,
            selected_raw_custody=head("provider-custody"),
        )
    def provider(
        name: str,
        *,
        selected_raw_custody: Head | None = _PROVIDER_CUSTODY,
        result: Literal["AUTHENTICATED_RECEIPT", "UNKNOWN"] = "AUTHENTICATED_RECEIPT",
        raw_observation_bytes: bytes = b"receipt",
    ) -> HandoffObservationRecord:
        source = head("provider-source-" + name)
        return HandoffObservationRecord(
            **record_base(
                "bad-provider-" + name,
                "ingress.observe_authenticated_provider_receipt",
                (head("p-" + name),),
            ),
            handoff_id="provider-" + name,
            issued_attempt=head("provider-attempt-" + name),
            authorization=None,
            result=result,
            observation=source,
            raw_observation_bytes=raw_observation_bytes,
            authenticated_source=source,
            selected_raw_custody=selected_raw_custody,
        )

    with pytest.raises(ValueError):
        provider("missing-custody", selected_raw_custody=None)
    with pytest.raises(ValueError):
        provider("unknown", result="UNKNOWN")
    with pytest.raises(ValueError):
        provider("empty-bytes", raw_observation_bytes=b"")
    with pytest.raises(ValueError):
        HandoffObservationRecord(
            **record_base("bad-release", "ingress.complete_reconciliation_release", (head("r"),)),
            handoff_id="release",
            issued_attempt=head("release-attempt"),
            authorization=head("release-authorization"),
            result="UNKNOWN",
            observation=head("release-observation"),
            raw_observation_bytes=b"",
            authenticated_source=None,
        )
