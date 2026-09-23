"""Public consumer shape checks; these establish no authentication or SEND right."""

import hashlib
import json

from tests.support.dispatch import head, mandate_wire

from chiplog.capabilities.effects.dispatch_authority_contracts import DispatchSourceInventory
from chiplog.capabilities.effects.dispatch_v2 import DispatchRecordV2
from chiplog.capabilities.effects.dispatch_v2_contracts import (
    DispatchAcquisitionV2,
    DispatchAdoptionV2,
    DispatchMandateV2,
    DispatchPrecursorRequestV2,
    DispatchPrecursorResultV2,
    ExternalActionIntentV2,
)


def original_intent(members: tuple[str, ...] = ("subject",)) -> ExternalActionIntentV2:
    # Each object is constructed only from earlier objects. None needs a placeholder
    # for its own fingerprint or any future acquisition/publication output.
    value = mandate_wire()
    value["bundle_members"] = tuple(head(member) for member in members)
    value["effect_fingerprint"] = hashlib.sha256(b"\xff\x00payload").hexdigest()
    mandate = DispatchMandateV2.model_validate(value)
    mandate_digest = hashlib.sha256(mandate.canonical_bytes()).hexdigest()
    unavailable = {"kind": "UNAVAILABLE", "reason": "UNIMPLEMENTED", "detail": "shape only"}
    request = DispatchPrecursorRequestV2.model_validate(
        {
            "schema_id": "chiplog.effects.dispatch-precursor-request.v2",
            "request_id": "precursor",
            "mandate_digest": mandate_digest,
            "interpretation_policy": head(),
            "preexisting_source_heads": (head(),),
        }
    )
    result = DispatchPrecursorResultV2.model_validate(
        {
            "schema_id": "chiplog.effects.dispatch-precursor-result.v2",
            "request_digest": hashlib.sha256(request.canonical_bytes()).hexdigest(),
            "mandate_digest": mandate_digest,
            "interpretation_policy": head(),
            "evaluation_evidence": (head(),),
        }
    )
    display = b"Adopt exact mandate SHA256=" + mandate_digest.encode()
    display_head = {**head(), "fingerprint": hashlib.sha256(display).hexdigest()}
    adoption = DispatchAdoptionV2.model_validate(
        {
            "schema_id": "chiplog.effects.dispatch-adoption.v2",
            "adoption_act_id": "exact-act",
            "display": display_head,
            "display_bytes": display,
            "mandate_bytes": mandate.canonical_bytes(),
            "ingress": {**head(), "fingerprint": hashlib.sha256(b"accept exact-act").hexdigest()},
            "ingress_bytes": b"accept exact-act",
        }
    )
    acquisition = DispatchAcquisitionV2(
        schema_id="chiplog.effects.dispatch-acquisition.v2",
        adoption=adoption,
        authenticated_invocation=b"retained authenticated precursor IPC frames",
        precursor_request=request,
        precursor_result=result,
        original_sources=DispatchSourceInventory.model_validate(
            {name: unavailable for name in DispatchSourceInventory.model_fields}
        ),
    )
    body = {
        "schema_id": "chiplog.effects.external-action-intent.v2",
        "intent_id": "intent",
        "mandate": mandate.model_dump(mode="json"),
        "acquisition": acquisition.model_dump(mode="json"),
    }
    preimage = json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    fingerprint = hashlib.sha256(b"chiplog.effects.intent.v2\x00" + preimage).hexdigest()
    intent = ExternalActionIntentV2.model_validate_json(
        json.dumps({**body, "fingerprint": fingerprint})
    )
    return intent


def original_send(members: tuple[str, ...] = ("subject",)) -> DispatchRecordV2:
    from chiplog.capabilities.effects.contracts import CommandIdentity, TransmissionAttempt
    from chiplog.capabilities.effects.dispatch_v2 import (
        DispatchAttemptV2,
        DispatchRecordV2,
        reference,
    )
    from chiplog.capabilities.effects.dispatch_v2_contracts import CommitFirstSendV2
    from chiplog.capabilities.effects.fences import NonSchedulerFence, NotApplicable

    intent = original_intent(members)
    ref = reference("send", b"send")
    intent_ref = reference(intent.intent_id, intent.canonical_bytes())
    fence = NonSchedulerFence(
        lineage=NotApplicable(),
        physical_root=NotApplicable(),
        lease=NotApplicable(),
        clock_proof=NotApplicable(),
        run_id="original-run",
        run_head="run-head",
        worker_session_id="session",
        runtime_generation="generation",
    )
    command = CommitFirstSendV2(
        schema_id="chiplog.effects.commit-first-send.v2",
        identity=CommandIdentity(command_id="send", fingerprint="a", expected_tenant_head=0),
        intent=intent_ref,
        expected_attempt=ref,
        authorization=ref,
        immutable_mandate=reference(intent.mandate.mandate_id, intent.mandate.canonical_bytes()),
        semantics=intent.mandate.semantics,
        fence=fence,
        ordinal=0,
    )
    child = TransmissionAttempt(
        transmission=reference(intent.intent_id + "/transmission/0", b"child"),
        intent=intent_ref,
        ordinal=0,
        semantics=intent.mandate.semantics,
        dispatch_time_ns=2,
        payload_fingerprint=intent.mandate.effect_fingerprint,
        recipient=intent.mandate.recipient,
        idempotency_fence_key=intent.mandate.idempotency_fence_key,
        send_commit=ref,
        coverage_proof=None,
    )
    return DispatchRecordV2(
        schema_id="chiplog.effects.dispatch-record.v2",
        record=ref,
        predecessor=ref,
        kind="SEND_COMMITTED",
        command=command,
        snapshot=DispatchAttemptV2(
            intent=intent,
            attempt=ref,
            state="SEND_COMMITTED",
            authorizations=(),
            transmissions=(child,),
        ),
    )
