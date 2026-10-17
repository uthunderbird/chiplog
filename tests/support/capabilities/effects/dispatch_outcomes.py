"""Shared builders extracted without changing their behavior."""

from chiplog.capabilities.effects.contracts import CommandIdentity
from chiplog.capabilities.effects.dispatch_outcome_contracts import (
    DispatchEvidenceV2,
    DispatchOutcomeCommandV2,
    DispatchOutcomePreparationV2,
    DispatchOutcomeRecordV2,
)
from chiplog.capabilities.effects.dispatch_v2 import DispatchRecordV2, digest, reference


def request(
    previous: DispatchRecordV2 | DispatchOutcomeRecordV2,
    send: DispatchRecordV2,
    positive: tuple[str, ...] = (),
    negative: tuple[str, ...] = (),
    *,
    close: bool = False,
    name: str = "receipt",
) -> DispatchOutcomePreparationV2:
    evidence = (
        None
        if close
        else DispatchEvidenceV2(
            evidence_id=name,
            source_identity="source",
            transmission=send.snapshot.transmissions[0].transmission,
            raw_bytes=name.encode(),
            raw_digest=digest(name.encode()),
            observation="PROVIDER_RECEIPT",
            occurred_members=positive,
            permanently_incapable_members=negative,
        )
    )
    command = DispatchOutcomeCommandV2(
        schema_id="chiplog.effects.dispatch-outcome-command.v2",
        identity=CommandIdentity(
            command_id=name, fingerprint=digest(name.encode()), expected_tenant_head=1
        ),
        operation="RESOLVE_OBLIGATION" if close else "APPEND_EVIDENCE",
        intent=reference(send.snapshot.intent.intent_id, send.snapshot.intent.canonical_bytes()),
        original_send=send.record,
        expected_attempt=previous.snapshot.attempt,
        complete_children=tuple(c.transmission for c in send.snapshot.transmissions),
        prior_evidence=previous.snapshot.evidence_heads
        if isinstance(previous, DispatchOutcomeRecordV2)
        else (),
        evidence=evidence,
        resolver=reference("principal", b"principal") if close else None,
    )
    return DispatchOutcomePreparationV2(
        schema_id="chiplog.effects.dispatch-outcome-preparation.v2",
        original_send=send,
        previous=previous,
        command=command,
    )
