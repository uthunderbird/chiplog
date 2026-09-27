"""Live, preselection H1 writer authority.

The authority deliberately starts fail-closed.  The public H1 issuance DTO is
historical evidence and cannot admit its own first selection.  Its only future
positive input is the nonserializable capability owned by
``H1CompletionPreparationSession``; that interface is intentionally not
guessed while the session producer is being assembled.
"""

from __future__ import annotations

from typing import Literal

from chiplog.platform._owner_publication_contracts import (
    ExactReplayQuery,
    PublicationRejected,
    RegisteredPublication,
)
from chiplog.platform._sqlite import (
    PhysicalPublicationCommand,
    PublicationVerificationMode,
    StoreAdmissionError,
    VerifiedOwnerPublication,
)
from chiplog.platform.owner_publications import PreparedOwnerPublication, SelectedOwnerDecision


class H1LivePublicationAuthority:
    """Fail closed until given a capability issued by the live H1 session.

    This class must never use ``validate_h1_completion_issuance`` or
    ``bind_selected_h1_completion`` for a new selection: both require an
    already selected decision and would make writer admission circular.
    """

    def __init__(self) -> None:
        # The B/P capability path will populate only private admission state.
        # Until then this resolver is deliberately safe to mount at store-open
        # time: every physical V2 verification fails through the normal store
        # admission path.
        self._revoked = False

    def verify(
        self, command: PhysicalPublicationCommand, mode: PublicationVerificationMode
    ) -> VerifiedOwnerPublication:
        """Reject every physical H1 V2 check until the B/P path exists."""
        del command, mode
        if self._revoked:
            raise StoreAdmissionError("live H1 publication authority is revoked")
        raise StoreAdmissionError("live H1 completion capability is not mounted")

    def _revoke_all(self) -> None:
        """Invalidate this installation before its evidence-root issuer unbinds."""
        self._revoked = True

    def authenticate_replay(self, query: ExactReplayQuery) -> PublicationRejected:
        return PublicationRejected(
            kind="DENIED",
            tenant_id=query.identity.tenant_id,
            command_id=query.identity.command_id,
            reason="live H1 completion capability is not mounted",
        )

    async def prepare(
        self, request: RegisteredPublication
    ) -> PreparedOwnerPublication | PublicationRejected:
        return PublicationRejected(
            kind="DENIED",
            tenant_id=request.identity.tenant_id,
            command_id=request.identity.command_id,
            reason="unissued live H1 completion publication",
        )

    def check_prepared(
        self, prepared: PreparedOwnerPublication
    ) -> Literal["DENIED", "STALE", "INDETERMINATE"]:
        del prepared
        return "DENIED"

    def materialization_state(
        self, decision: SelectedOwnerDecision
    ) -> Literal["ABSENT", "EXACT_PREFIX", "COMPLETE", "CONFLICT"]:
        del decision
        return "CONFLICT"

    def check_selected_predecessor(self, decision: SelectedOwnerDecision) -> bool:
        del decision
        return False
