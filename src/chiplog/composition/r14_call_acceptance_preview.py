"""Closed preview interpretation; supplied bytes never establish current authority."""

import hashlib
import json

from chiplog.capabilities.agent_loop.contracts import LoopRejected
from chiplog.capabilities.effects.dispatch_v2 import require_mandate
from chiplog.capabilities.effects.dispatch_v2_contracts import (
    DispatchMandateV2,
    InitializedCallOrigin,
)
from chiplog.composition.r14_call_acceptance_port import (
    CallAcceptancePreview,
    CallAcceptanceTarget,
)


def _hash(domain: bytes, body: dict[str, object]) -> str:
    return hashlib.sha256(
        domain + json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def _display(target: CallAcceptanceTarget, mandate: DispatchMandateV2) -> bytes:
    # Show the entire original mandate, including recipient, payload, horizon and
    # source references. No hidden values are filled in after explicit adoption.
    return json.dumps(
        {
            "kind": "CALL_ACCEPTANCE_DISPLAY_V2",
            "target": json.loads(target.canonical_bytes()),
            "mandate": json.loads(mandate.canonical_bytes()),
        },
        sort_keys=True,
        indent=2,
        ensure_ascii=False,
    ).encode()


def build_call_acceptance_preview(
    target: CallAcceptanceTarget, mandate: DispatchMandateV2
) -> CallAcceptancePreview:
    """Bind a supplied original mandate to its initialized subject, not authorize it.

    The caller must separately authenticate the actual selected initialization,
    policy/tool interpretation and current Run/fence before issuing this display.
    """
    target = CallAcceptanceTarget.model_validate_json(target.canonical_bytes())
    mandate = DispatchMandateV2.model_validate_json(mandate.canonical_bytes())
    require_mandate(mandate)
    origin = mandate.origin
    if not isinstance(origin, InitializedCallOrigin) or (
        target.original_call_id != target.initialized.subject_id
        or origin.original_call_id != target.original_call_id
        or origin.initialized_head.subject_id != target.initialized.subject_id
        or origin.initialized_head.head != target.initialized.revision.head
        or origin.initialized_head.fingerprint != target.initialized.revision.fingerprint
    ):
        raise LoopRejected("preview mandate does not bind the exact initialized call")
    preview = CallAcceptancePreview(
        preview_id="unhashed",
        target=target,
        display_bytes=_display(target, mandate),
        mandate_bytes=mandate.canonical_bytes(),
        preview_fingerprint="0" * 64,
    )
    body = json.loads(preview.canonical_bytes())
    del body["preview_id"]
    del body["preview_fingerprint"]
    preview = preview.model_copy(
        update={
            "preview_id": "call-acceptance-preview:"
            + _hash(b"chiplog.call.acceptance-preview-identity.v2\x00", body)
        }
    )
    body = json.loads(preview.canonical_bytes())
    del body["preview_fingerprint"]
    return preview.model_copy(
        update={"preview_fingerprint": _hash(b"chiplog.call.acceptance-preview.v2\x00", body)}
    )


def decode_call_acceptance_preview(raw: bytes) -> tuple[CallAcceptancePreview, DispatchMandateV2]:
    """Reject changed/noncanonical display bytes; return no invocation credential."""
    preview = CallAcceptancePreview.model_validate_json(raw)
    mandate = DispatchMandateV2.model_validate_json(preview.mandate_bytes)
    if (
        preview.canonical_bytes() != raw
        or mandate.canonical_bytes() != preview.mandate_bytes
        or preview != build_call_acceptance_preview(preview.target, mandate)
    ):
        raise LoopRejected("acceptance preview differs from its registered exact display")
    return preview, mandate
