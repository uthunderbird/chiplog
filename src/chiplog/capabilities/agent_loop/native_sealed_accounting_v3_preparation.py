"""Pure owner preparation for complete ordered R14 per-seal accounting V3."""

from __future__ import annotations

import hashlib

from .native_sealed_accounting_v3_contracts import (
    PreparedNativeSealedAccountingV3,
    PrepareNativeSealedAccountingV3,
)
from .recovery_domain import RecoveryIntegrityFault, sealed_call_accounting


def prepare_native_sealed_accounting_v3(
    request: PrepareNativeSealedAccountingV3,
) -> PreparedNativeSealedAccountingV3:
    """Validate and exhaustively join exactly the selected seal's ordered members.

    A successful result records terminal accounting only. In particular, an
    all-terminal frontier is not a continuation or execution credential.
    """

    request = PrepareNativeSealedAccountingV3.model_validate_json(request.canonical_bytes())
    response_id = request.selected_response.subject_id
    if any(frontier.response_id != response_id for frontier in request.complete_ordered_frontiers):
        raise RecoveryIntegrityFault("frontier substituted from a different response seal")

    accounting = sealed_call_accounting(
        response_id,
        request.sealed_manifest_fingerprint,
        request.ordered_sealed_call_ids,
        request.complete_ordered_frontiers,
    )
    return PreparedNativeSealedAccountingV3(
        source_request_fingerprint=hashlib.sha256(request.canonical_bytes()).hexdigest(),
        selected_response=request.selected_response,
        sealed_manifest_fingerprint=accounting.sealed_manifest_fingerprint,
        complete_ordered_calls=accounting.calls,
    )
