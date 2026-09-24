"""Prepare one native v2 Run from a selected admitted CLI inbox.

This owner-local adapter validates relationships already carried by the typed
initialization request.  It neither authenticates the source nor publishes the
Run; the broker owns both operations and rechecks the selected source at its cut.
"""

from __future__ import annotations

from pydantic import TypeAdapter, ValidationError

from .call_acceptance_contracts import CallPreparationRejected
from .call_acceptance_preparation import _failure, _proposal_digest, _Rejected, _require
from .execution_initialization_contracts import (
    AdmittedExecutionBinding,
    ExecutionInitializationPreparationPort,
    ExecutionInitializationRequest,
    ExecutionInitializationResult,
    PreparedExecutionInitialization,
    PrepareInboxExecution,
    PrepareScheduledExecution,
)
from .execution_transition_contracts import CreateExecutionRun, ExecutionTransitionProposal
from .execution_transitions import prepare_execution_transition
from .recovery_contracts import Absent, Present

type _Result = PreparedExecutionInitialization | CallPreparationRejected


def _unsupported(command_id: object, reason: str) -> CallPreparationRejected:
    return CallPreparationRejected(
        command_id=command_id if isinstance(command_id, str) and command_id else "invalid-command",
        code="UNSUPPORTED",
        reason=reason,
    )


def _admitted_create_join(request: PrepareInboxExecution) -> None:
    """Check the complete typed join before the native Create interpreter runs."""
    create, admitted, cut = request.create, request.admitted, request.cut
    _require(
        admitted.source_class == "CLI",
        "admitted source lacks registered CLI route",
        "UNSUPPORTED",
    )
    _require(
        isinstance(cut.initial_run_absence, Absent),
        "initial execution requires an exact absent Run observation",
        "STALE",
    )
    _require(
        create.tenant == admitted.tenant_id == cut.tenant_id,
        "Create, admitted input and initialization cut have different tenants",
        "STALE",
    )
    _require(
        admitted.database_id == cut.database_id,
        "admitted input and initialization cut have different databases",
        "STALE",
    )
    _require(
        create.principal == admitted.principal_id,
        "Create principal differs from admitted principal",
        "STALE",
    )
    _require(
        create.origin == admitted.origin,
        "Create origin differs from admitted origin",
        "STALE",
    )
    ingress = admitted.origin.ingress_binding
    _require(
        admitted.inbox.subject_id == ingress.identity
        and admitted.inbox.revision == Present(head=ingress.head, fingerprint=ingress.fingerprint),
        "admitted inbox reference differs from execution origin ingress binding",
        "STALE",
    )
    _require(
        create.contour_head == admitted.contour_head,
        "Create contour differs from admitted contour",
        "STALE",
    )
    _require(
        create.worker_session == cut.worker_session_id,
        "Create worker session differs from initialization cut",
        "STALE",
    )
    _require(
        admitted.normalization == admitted.inbox
        and admitted.normalization_schema == admitted.inbox_schema
        and admitted.canonical_normalization_record == admitted.canonical_inbox_record,
        "CLI normalization is not the selected embedded inbox carrier",
    )
    prompt = admitted.raw_input_bytes.decode("utf-8")
    _require(
        prompt == admitted.normalized_prompt == create.prompt,
        "retained CLI bytes, normalized prompt and Create prompt differ",
    )


def prepare_inbox_execution(request: ExecutionInitializationRequest) -> _Result:
    """Return a proposal for the supported admitted CLI → native-v2 Create route."""
    command_id = getattr(getattr(request, "create", None), "command_id", None)
    try:
        # Reparse the whole carrier so unchecked model_copy values cannot bypass joins.
        request = TypeAdapter(ExecutionInitializationRequest).validate_json(
            request.canonical_bytes()
        )
        if isinstance(request, PrepareScheduledExecution):
            return _unsupported(
                request.create.command_id, "scheduled initialization is not mounted here"
            )
        assert isinstance(request, PrepareInboxExecution)
        if not isinstance(request.create, CreateExecutionRun):
            return _unsupported(
                request.create.command_id, "native v3 Create has no registered producer"
            )
        _admitted_create_join(request)
        native = prepare_execution_transition(request.create)
        if isinstance(native, CallPreparationRejected):
            return native
        assert isinstance(native, ExecutionTransitionProposal)
        proposal = PreparedExecutionInitialization(
            source_request_fingerprint=request.digest(),
            run=native.run,
            input_binding=AdmittedExecutionBinding(
                original_inbox=request.admitted.inbox,
                original_custody=request.admitted.custody,
                normalization=request.admitted.normalization,
            ),
            proposal_fingerprint="0" * 64,
        )
        return proposal.model_copy(update={"proposal_fingerprint": _proposal_digest(proposal)})
    except (_Rejected, ValidationError, UnicodeDecodeError, ValueError, TypeError) as error:
        return _failure(command_id, error)


class ExecutionInboxInitialization(ExecutionInitializationPreparationPort):
    """Concrete implementation of the existing initialization preparation port."""

    async def prepare_initial_execution(
        self, request: ExecutionInitializationRequest
    ) -> ExecutionInitializationResult:
        return prepare_inbox_execution(request)
