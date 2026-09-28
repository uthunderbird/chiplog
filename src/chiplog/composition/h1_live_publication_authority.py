"""Live, preselection H1 writer authority.

The authority deliberately starts fail-closed.  The public H1 issuance DTO is
historical evidence and cannot admit its own first selection.  Its only future
positive input is the nonserializable capability owned by
``H1CompletionPreparationSession``; that interface is intentionally not
guessed while the session producer is being assembled.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, cast

from chiplog.capabilities.agent_loop.delivery_contracts import ExactHead
from chiplog.platform._owner_publication_contracts import (
    ExactReplayQuery,
    PublicationRejected,
    RegisteredPublication,
)
from chiplog.platform._sqlite import (
    PhysicalPublicationCommand,
    PhysicalPublicationProjection,
    PublicationVerificationMode,
    StoreAdmissionError,
    VerifiedOwnerPublication,
)
from chiplog.platform.owner_publications import PreparedOwnerPublication, SelectedOwnerDecision

if TYPE_CHECKING:
    from chiplog.capabilities.agent_loop.execution_contracts import ExecutionRunRecord
    from chiplog.composition.common_cli_execution_runtime import CommonCliExecutionRuntime
    from chiplog.composition.common_execution_driver_contracts import DriverCommandIdentityV1
    from chiplog.composition.h1_live_completion_enrollment import _H1LiveCompletionIssuance
    from chiplog.composition.h1_recovery_execution_fence import _H1RecoveryExecutionLease
    from chiplog.composition.r14_execution_inbox_records import RetainedInboxExecutionInitialization
    from chiplog.platform._owner_publication_contracts import BrokerPublicationResult


@dataclass(frozen=True, slots=True)
class _H1SelectedTerminalReadback:
    """Exact, selected physical completion evidence for A's receipt projection.

    This private value is deliberately made only after the owner journal and
    SQLite agree.  It is neither a publication capability nor a DTO boundary.
    """

    retained_h0: RetainedInboxExecutionInitialization
    selected_owner_decision: SelectedOwnerDecision
    physical_command: PhysicalPublicationCommand
    terminal_run: ExecutionRunRecord
    acceptance_head: ExactHead
    delivery_manifest_head: ExactHead
    committed_conversation_projection_head: ExactHead


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
        self._runtime: CommonCliExecutionRuntime | None = None
        self._prepared: dict[str, tuple[PreparedOwnerPublication | None, Any]] = {}

    def _bind_installed_runtime(self, runtime: CommonCliExecutionRuntime) -> None:
        """Bind this authority once to the one installed H1 object graph.

        The authority never accepts a runtime at commit time.  Establishing the
        graph here makes the enrollment-owned marker table and the physical
        broker coordinator one identity set before any finalization can start.
        """
        from chiplog.composition.common_cli_execution_runtime import CommonCliExecutionRuntime
        from chiplog.composition.h1_live_completion_enrollment import _H1LiveCompletionEnrollment
        from chiplog.composition.h1_live_invocation_source import H1LiveInvocationSource
        from chiplog.composition.h1_postseal_recovery_coordinator import (
            _H1PostSealRecoveryCoordinator,
        )
        from chiplog.platform.owner_publications import BrokerPublicationCoordinator

        if type(runtime) is not CommonCliExecutionRuntime:
            raise TypeError("live H1 authority requires the canonical installed runtime")
        if self._revoked or self._runtime is not None:
            raise RuntimeError("live H1 authority runtime binding is unavailable")
        enrollment = getattr(runtime, "_h1_live_completion_enrollment", None)
        mount = getattr(runtime, "_h1_live_completion_mount", None)
        coordinator = getattr(runtime, "_h1_live_publication_coordinator", None)
        recovery = getattr(runtime, "_h1_postseal_recovery_coordinator", None)
        invocation = getattr(runtime, "_h1_live_invocation_source", None)
        gate = runtime._authority_gate()
        if (
            getattr(runtime, "_h1_live_publication_authority", None) is not self
            or type(enrollment) is not _H1LiveCompletionEnrollment
            or enrollment._runtime is not runtime
            or enrollment._authority is not self
            or enrollment._gate is not gate
            or getattr(mount, "_runtime", None) is not runtime
            or getattr(mount, "_authority", None) is not self
            or type(coordinator) is not BrokerPublicationCoordinator
            or coordinator._authority is not self
            or coordinator._appender is not runtime._appender
            or coordinator._journal is not runtime._owner_decisions()
            or type(recovery) is not _H1PostSealRecoveryCoordinator
            or recovery._runtime is not runtime
            or type(invocation) is not H1LiveInvocationSource
            or invocation._runtime is not runtime
            or invocation._revoked
        ):
            raise RuntimeError("live H1 authority installation graph differs")
        self._runtime = runtime

    def verify(
        self, command: PhysicalPublicationCommand, mode: PublicationVerificationMode
    ) -> VerifiedOwnerPublication:
        """Resolve only the retained issued or selected H1 batch at the physical cut."""
        from chiplog.composition.h1_completion_issuance import h1_completion_exchange
        from chiplog.composition.r14_execution_completion_records import complete_acceptance_command
        from chiplog.platform._owner_publication_contracts import CompleteDeliveryBatchV2

        runtime = self._runtime
        if self._revoked:
            raise StoreAdmissionError("live H1 publication authority is revoked")
        if runtime is None:
            raise StoreAdmissionError("live H1 completion capability is not mounted")
        try:
            with runtime._authority_gate().hold():
                entry = self._prepared.get(command.idempotency_key)
                batch: CompleteDeliveryBatchV2
                selected = runtime._owner_decisions().lookup(
                    command.tenant_id, command.idempotency_key
                )
                prepared = None if entry is None else entry[0]
                retained = None if entry is None else getattr(entry[1], "batch", None)
                if selected is not None:
                    if type(selected.prepared.request) is not CompleteDeliveryBatchV2:
                        raise ValueError("H1 selected publication has the wrong batch")
                    batch = selected.prepared.request
                    if retained is not None and retained != batch:
                        raise ValueError("H1 retained and selected batches differ")
                elif type(retained) is CompleteDeliveryBatchV2:
                    batch = retained
                else:
                    raise ValueError("H1 physical publication has no retained batch")
                if mode in ("PRECOMMIT", "REPLAY") and selected is None:
                    raise ValueError("H1 physical publication has no selected decision")
                expected = complete_acceptance_command(h1_completion_exchange(batch))
                if PhysicalPublicationProjection.from_command(
                    command
                ) != PhysicalPublicationProjection.from_command(expected):
                    raise ValueError("H1 physical publication projection differs")
                if mode != "REPLAY" and (
                    command.fault != "none"
                    or command.authority_checkpoint_guard is not None
                    or not callable(command.admission_guard)
                    or not callable(command.decision_guard)
                ):
                    raise ValueError("H1 physical publication control shape differs")
                if mode == "ABSENT":
                    if selected is not None:
                        if not self.check_selected_predecessor(selected):
                            raise ValueError("H1 selected predecessor differs")
                    elif prepared is None or self.check_prepared(prepared) is not None:
                        raise ValueError("H1 retained preparation differs")
                if mode == "REPLAY" and (
                    selected is not None and self.materialization_state(selected) == "CONFLICT"
                ):
                    raise ValueError("H1 selected physical state conflicts")
                return VerifiedOwnerPublication.from_command(
                    command,
                    binding_fingerprint=batch.authentication.applicability_fingerprint,
                    selected_identity=None if selected is None else selected.decision_id,
                    selected_fingerprint=(
                        None if selected is None else selected.decision_fingerprint
                    ),
                    expected_resulting=None if selected is None else selected.resulting_commitment,
                    expected_commit_sequence=(
                        None if selected is None else selected.tenant_commit_sequence
                    ),
                )
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            raise StoreAdmissionError("live H1 completion capability differs") from error

    def _revoke_all(self) -> None:
        """Invalidate this installation before its evidence-root issuer unbinds."""
        self._revoked = True
        self._runtime = None

    async def _recover_finalization_held(
        self,
        *,
        identity: DriverCommandIdentityV1,
        original_fingerprint: str,
        lease: _H1RecoveryExecutionLease,
    ) -> BrokerPublicationResult | None:
        """Reconcile a previous selection before B is opened.

        This is intentionally fail-closed until the installed selected/pending
        reader is mounted.  Returning ``None`` without that authenticated
        reader would turn ambiguous absence into permission to issue.
        """
        from chiplog.composition.h1_completion_issuance import (
            H1CompletionIssuanceV2,
            decode_h1_completion_issuance,
        )
        from chiplog.platform._owner_publication_contracts import CompleteDeliveryBatchV2

        runtime = self._runtime
        if self._revoked or runtime is None:
            raise RuntimeError("H1 finalization selected reader is not mounted")
        selected: SelectedOwnerDecision | None = None
        with runtime._authority_gate().hold():
            lease.require_owned()
            runtime._check_database_identity()
            matches: list[SelectedOwnerDecision] = []
            for decision in runtime._owner_decisions().snapshot().decisions:
                batch = decision.prepared.request
                if type(batch) is not CompleteDeliveryBatchV2:
                    continue
                try:
                    from chiplog.composition.h1_historical_selected_sources import (
                        _verify_selected_v2_delivery_closure,
                    )

                    retained_principal = _verify_selected_v2_delivery_closure(decision, runtime)
                    value = decode_h1_completion_issuance(batch)
                    from chiplog.composition.h1_historical_selected_sources import (
                        _selected_v2_issuance_principal,
                    )

                    if retained_principal is not None:
                        if type(value) is not H1CompletionIssuanceV2:
                            raise ValueError("H1 finalization selected closure issuance differs")
                        issuance_principal = _selected_v2_issuance_principal(value)
                        if retained_principal != issuance_principal:
                            raise ValueError("H1 finalization selected closure principal differs")
                    retained = self._retained_h0(value.assembly)
                except (TypeError, ValueError) as error:
                    raise RuntimeError("H1 finalization selected evidence is malformed") from error
                from chiplog.composition.common_execution_driver_contracts import (
                    DriveInputRequestV1,
                )

                driver = DriveInputRequestV1.model_validate_json(retained.driver_request_bytes)
                if (
                    driver.identity == identity
                    and retained.driver_request_fingerprint == original_fingerprint
                ):
                    matches.append(decision)
            if len(matches) > 1:
                raise RuntimeError("H1 finalization selected decisions are ambiguous")
            if matches:
                selected = matches[0]
            elif (
                runtime._pending_owners()
                or runtime._pending()
                or runtime._pending_gate_publications()
            ):
                raise RuntimeError("H1 finalization pending state is not an authenticated absence")
        if selected is None:
            return None
        coordinator = getattr(runtime, "_h1_live_publication_coordinator", None)
        if coordinator is None:
            raise RuntimeError("H1 finalization coordinator is not mounted")
        return cast(
            "BrokerPublicationResult",
            await coordinator.recover_selected(
                selected.prepared.request.identity.tenant_id,
                selected.prepared.request.identity.command_id,
            ),
        )

    async def _commit_finalization_held(
        self, *, source: _H1LiveCompletionIssuance
    ) -> BrokerPublicationResult:
        """Consume one B marker before writer reentry once the payload exists."""
        from chiplog.composition.h1_live_completion_enrollment import _H1LiveCompletionEnrollment
        from chiplog.platform._owner_publication_contracts import CompleteDeliveryBatchV2

        runtime = self._runtime
        enrollment = (
            None if runtime is None else getattr(runtime, "_h1_live_completion_enrollment", None)
        )
        if self._revoked or runtime is None or type(enrollment) is not _H1LiveCompletionEnrollment:
            raise RuntimeError("H1 finalization issuance authority is not mounted")
        record = enrollment._consume_authority_issuance(authority=self, source=source)
        if type(record.batch) is not CompleteDeliveryBatchV2:
            raise RuntimeError("H1 finalization issuance batch differs")
        batch = record.batch
        self._prepared[batch.identity.command_id] = (None, record)
        try:
            coordinator = getattr(runtime, "_h1_live_publication_coordinator", None)
            if coordinator is None:
                raise RuntimeError("H1 finalization coordinator is not mounted")
            return cast("BrokerPublicationResult", await coordinator.commit(batch))
        finally:
            self._prepared.pop(batch.identity.command_id, None)

    async def _read_finalization_receipt_held(
        self,
        *,
        identity: DriverCommandIdentityV1,
        original_fingerprint: str,
        publication: object,
    ) -> _H1SelectedTerminalReadback:
        """Read one selected H1 completion back from the physical installed cut.

        The caller's publication result only names the decision to compare.
        All authoritative material is reopened from the mounted journal and
        SQLite while the common gate is held.
        """
        from chiplog.capabilities.agent_loop.completion_owner_record_contracts import (
            make_prepared_delivery_acceptance_member,
            make_terminal_manifest_member,
        )
        from chiplog.composition.h1_completion_issuance import _h1_identity
        from chiplog.composition.h1_historical_selected_sources import bind_selected_h1_completion
        from chiplog.composition.r14_loop_history import (
            _selected_h1_completion,
            read_execution_history,
        )
        from chiplog.platform._owner_publication_contracts import (
            CompleteDeliveryBatchV2,
            JournalSelectedPublication,
        )

        runtime = self._runtime
        if self._revoked or runtime is None:
            raise RuntimeError("live H1 finalization readback is not mounted")
        if type(publication) is not JournalSelectedPublication:
            raise TypeError("H1 finalization readback requires a selected publication")
        with runtime._authority_gate().hold():
            if getattr(runtime, "_h1_live_publication_authority", None) is not self:
                raise RuntimeError("live H1 finalization readback mount differs")
            runtime._check_database_identity()
            runtime._require_no_pending()
            decision = runtime._owner_decisions().lookup(
                publication.tenant_id, publication.command_id
            )
            if (
                type(decision) is not SelectedOwnerDecision
                or decision.decision_id != publication.decision_id
                or decision.decision_head != publication.decision_head
                or decision.decision_fingerprint != publication.decision_fingerprint
                or decision.resulting_commitment != publication.resulting_commitment
                or decision.tenant_commit_sequence != publication.tenant_commit_sequence
                or type(decision.prepared.request) is not CompleteDeliveryBatchV2
            ):
                raise ValueError("H1 finalization selected decision differs from publication")
            bound = bind_selected_h1_completion(decision.prepared.request, runtime)
            if bound.decision != decision:
                raise ValueError("H1 finalization historical selected decision differs")
            command, terminal, _ = _selected_h1_completion(runtime, decision)
            state, actual, anchored = runtime._selected_physical_state(command)
            if state != "COMPLETE" or actual != anchored:
                raise ValueError("H1 finalization selected physical command is not complete")
            history = read_execution_history(runtime)
            lineage = tuple(
                record for record in history.records if record.run_id == terminal.run_id
            )
            if not lineage or lineage[-1] != terminal:
                raise ValueError("H1 finalization terminal Run lineage differs")

            assembly = bound.issuance.assembly
            command_id, fingerprint = _h1_identity(assembly)
            if (
                command_id != publication.command_id
                or fingerprint != publication.command_id.removeprefix("h1-completion:")
            ):
                # ``command_id`` is itself derived from the retained H0
                # fingerprint.  Keep this explicit comparison separate from
                # the caller's original driver fingerprint below.
                raise ValueError("H1 finalization selected identity differs")
            retained = self._retained_h0(assembly)
            from chiplog.composition.common_execution_driver_contracts import DriveInputRequestV1

            driver = DriveInputRequestV1.model_validate_json(retained.driver_request_bytes)
            if (
                driver.canonical_bytes() != retained.driver_request_bytes
                or driver.identity != identity
                or retained.driver_request_fingerprint != original_fingerprint
            ):
                raise ValueError("H1 finalization retained H0 identity differs")

            acceptance = make_prepared_delivery_acceptance_member(
                assembly.original_completion_request, assembly.prepared_completion
            )
            manifest = make_terminal_manifest_member(assembly.prepared_completion.terminal_manifest)
            conversations = assembly.conversation_result.ordered_members
            if len(conversations) != 1:
                raise ValueError("H1 finalization conversation projection is ambiguous")

            def physical_head(record_id: str, raw: bytes, owner: str, schema: str) -> ExactHead:
                matches = [
                    record
                    for record in command.records
                    if record.record_id == record_id
                    and record.owner == owner
                    and record.schema_id == schema
                    and record.canonical_bytes == raw
                    and record.fingerprint == hashlib.sha256(raw).hexdigest()
                ]
                if len(matches) != 1:
                    raise ValueError("H1 finalization selected physical member differs")
                record = matches[0]
                return ExactHead(
                    identity=record.record_id,
                    head=record.record_id,
                    fingerprint=record.fingerprint,
                )

            return _H1SelectedTerminalReadback(
                retained_h0=retained,
                selected_owner_decision=decision,
                physical_command=command,
                terminal_run=terminal,
                acceptance_head=physical_head(
                    acceptance.record_id,
                    acceptance.canonical_record_bytes,
                    "agent_loop",
                    acceptance.schema_id,
                ),
                delivery_manifest_head=physical_head(
                    manifest.record_id,
                    manifest.canonical_record_bytes,
                    "agent_loop",
                    manifest.schema_id,
                ),
                committed_conversation_projection_head=physical_head(
                    conversations[0].record_id,
                    conversations[0].canonical_bytes,
                    "conversation",
                    conversations[0].schema_id,
                ),
            )

    @staticmethod
    def _retained_h0(assembly: object) -> RetainedInboxExecutionInitialization:
        """Decode the H0 evidence through the retained effects origin only."""
        from chiplog.composition.r14_execution_inbox_records import (
            RetainedInboxExecutionInitialization,
        )

        try:
            effects = assembly.ordered_effects  # type: ignore[attr-defined]
            if type(effects) is not tuple or len(effects) != 1:
                raise ValueError("H1 finalization effects origin differs")
            raw = effects[0].owner_call.request.retained_origin.initialization_envelope_bytes
            envelope = json.loads(raw)
            encoded = envelope["inbox_initialization"]
            if type(encoded) is not str:
                raise ValueError("H1 finalization retained H0 is absent")
            retained = RetainedInboxExecutionInitialization.model_validate_json(encoded)
            if retained.canonical_bytes() != encoded.encode():
                raise ValueError("H1 finalization retained H0 is noncanonical")
            return retained
        except (AttributeError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise ValueError("H1 finalization retained H0 differs") from error

    def authenticate_replay(self, query: ExactReplayQuery) -> PublicationRejected | None:
        """Admit only the exact query within one active private commit scope."""
        from chiplog.composition.h1_live_completion_enrollment import (
            _H1LiveCompletionEnrollment,
            _IssuanceRecord,
        )
        from chiplog.platform._owner_publication_contracts import (
            CompleteDeliveryBatchV2,
        )
        from chiplog.platform.owner_publications import source_commands

        def denied(reason: str) -> PublicationRejected:
            return PublicationRejected(
                kind="DENIED",
                tenant_id=query.identity.tenant_id,
                command_id=query.identity.command_id,
                reason=reason,
            )

        runtime = self._runtime
        if self._revoked or runtime is None:
            return denied("live H1 completion capability is not mounted")
        assert runtime is not None
        try:
            gate = cast(Any, runtime)._authority_gate()
            with gate.hold():
                enrollment = getattr(runtime, "_h1_live_completion_enrollment", None)
                if (
                    getattr(runtime, "_h1_live_publication_authority", None) is not self
                    or type(enrollment) is not _H1LiveCompletionEnrollment
                    or enrollment._runtime is not runtime
                    or enrollment._authority is not self
                    or enrollment._gate is not gate
                ):
                    return denied("live H1 completion authority graph differs")
                entry = self._prepared.get(query.identity.command_id)
                if entry is None or type(entry[1]) is not _IssuanceRecord:
                    return denied("live H1 completion replay is outside its active commit")
                record = entry[1]
                if (
                    record.authority is not self
                    or record.state != "CONSUMED"
                    or enrollment._issuances.get(id(record.source)) is not record
                    or type(record.batch) is not CompleteDeliveryBatchV2
                ):
                    return denied("live H1 completion replay source differs")
                batch = record.batch
                if (
                    query.identity != batch.identity
                    or query.operation != batch.operation
                    or query.current_invocation != batch.authentication.invocation
                    or query.original_commands != source_commands(batch)
                ):
                    return denied("live H1 completion replay query differs")
                enrollment._require_recovery_record_current(record.enrollment)
                if record.enrollment.preflight is None:
                    return denied("live H1 completion replay finalization differs")
                return None
        except OSError, RuntimeError, TypeError, ValueError:
            return denied("live H1 completion replay is not current")

    async def prepare(
        self, request: RegisteredPublication
    ) -> PreparedOwnerPublication | PublicationRejected:
        from chiplog.composition.h1_completion_issuance import (
            H1CompletionIssuanceV2,
            decode_h1_completion_issuance,
        )
        from chiplog.composition.h1_delivery_evidence_contracts import (
            ROOT_V2_SCHEMA,
            H1DeliverySelectionClosureV2,
            decode_h1_delivery_evidence,
        )
        from chiplog.composition.h1_delivery_evidence_journal import H1DeliveryEvidenceJournal
        from chiplog.composition.h1_live_completion_enrollment import (
            _H1LiveCompletionEnrollment,
            _IssuanceRecord,
        )
        from chiplog.composition.h1_live_readplan_source import H1LiveReadPlanSource
        from chiplog.platform._owner_publication_contracts import CompleteDeliveryBatchV2
        from chiplog.platform.h1_delivery_binding_contracts import H1DeliveryBinding
        from chiplog.platform.owner_decision_journal import canonical_owner_publication_bytes

        runtime = self._runtime
        if self._revoked or runtime is None or type(request) is not CompleteDeliveryBatchV2:
            return PublicationRejected(
                kind="DENIED",
                tenant_id=request.identity.tenant_id,
                command_id=request.identity.command_id,
                reason="unissued live H1 completion publication",
            )
        try:
            gate = cast(Any, runtime)._authority_gate()
            with gate.hold():
                entry = self._prepared.get(request.identity.command_id)
                if entry is None or type(entry[1]) is not _IssuanceRecord:
                    raise ValueError("private issued candidate is absent")
                record = entry[1]
                enrollment = getattr(runtime, "_h1_live_completion_enrollment", None)
                if (
                    record.batch is not request
                    or record.authority is not self
                    or record.state != "CONSUMED"
                    or type(enrollment) is not _H1LiveCompletionEnrollment
                    or enrollment._runtime is not runtime
                    or enrollment._authority is not self
                    or enrollment._gate is not gate
                    or enrollment._issuances.get(id(record.source)) is not record
                    or type(record.issuance) is not H1CompletionIssuanceV2
                ):
                    raise ValueError("private issued candidate differs")
                enrollment._require_recovery_record_current(record.enrollment)
                if record.enrollment.preflight is None:
                    raise ValueError("finalization enrollment differs")
                value = decode_h1_completion_issuance(request)
                issued_bytes = record.issuance.canonical_bytes()
                if (
                    type(value) is not H1CompletionIssuanceV2
                    or value.canonical_bytes() != issued_bytes
                    or request.authentication.applicability_bytes != issued_bytes
                    or request.authentication.applicability_fingerprint
                    != hashlib.sha256(issued_bytes).hexdigest()
                ):
                    raise ValueError("candidate differs")
                readplan = getattr(runtime, "_h1_live_readplan_source", None)
                if type(readplan) is not H1LiveReadPlanSource:
                    raise ValueError("issued H1 read plan is not mounted")
                if (
                    readplan._admit_issued_manifest(
                        capture=record.readplan_capture,
                        session=record.enrollment.session,
                    )
                    != request.expected
                ):
                    raise ValueError("issued H1 read plan differs")
                journal = getattr(runtime, "_h1_delivery_evidence_journal", None)
                if type(journal) is not H1DeliveryEvidenceJournal:
                    raise ValueError("mounted H1 delivery evidence journal differs")
                mount = journal._mount
                mount.assert_current()
                launch = mount._launch
                launch.assert_current()
                slot = launch._slot
                genesis_digest = launch.custody._binding._genesis
                principal_id = (
                    value.assembly.original_completion_request.source.selected_admitted_input.principal_id
                )
                predecessor_commitment = request.expected.expected_materialization_commitment
                request_bytes = canonical_owner_publication_bytes(request)
                closure_fields = {
                    "schema_id": ROOT_V2_SCHEMA,
                    "deployment_id": slot.deployment_id,
                    "database_id": slot.database_id,
                    "database_genesis_digest": genesis_digest,
                    "tenant_id": mount.tenant_id,
                    "principal_id": principal_id,
                    "journal_role": "h1-delivery-evidence",
                    "journal_instance_id": mount.journal_instance_id,
                    "command_id": request.identity.command_id,
                    "command_fingerprint": request.identity.command_fingerprint,
                    "request_digest": hashlib.sha256(request_bytes).hexdigest(),
                    "predecessor_commitment": predecessor_commitment,
                    "expected_tenant_frontier": request.expected.tenant_frontier,
                }
                closure = decode_h1_delivery_evidence(
                    json.dumps(
                        closure_fields,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode()
                )
                if type(closure) is not H1DeliverySelectionClosureV2:
                    raise ValueError("H1 V2 delivery closure differs")
                locator = journal._issue_from_bound_root_owner(closure, self)
                binding = H1DeliveryBinding(
                    deployment_id=slot.deployment_id,
                    database_id=slot.database_id,
                    database_genesis_digest=genesis_digest,
                    tenant_id=mount.tenant_id,
                    journal_instance_id=mount.journal_instance_id,
                    closure_entry_id=locator.entry_id,
                    closure_payload_digest=locator.payload_digest,
                    closure_schema_id="chiplog.execution.h1-delivery-selection-closure.v2",
                    command_id=request.identity.command_id,
                    command_fingerprint=request.identity.command_fingerprint,
                    request_digest=hashlib.sha256(request_bytes).hexdigest(),
                )
                retained = journal.read_closure(binding)
                if (
                    type(retained.record) is not H1DeliverySelectionClosureV2
                    or retained.record._value != closure_fields
                ):
                    raise ValueError("H1 delivery closure retained request differs")
                prepared = entry[0]
                if prepared is None:
                    prepared = PreparedOwnerPublication(
                        request=request,
                        issuance_id=secrets.token_hex(32),
                        fence_generation="r6",
                        fence_frontier=0,
                        predecessor_commitment=request.expected.expected_materialization_commitment,
                        h1_delivery_binding=binding,
                    )
                    self._prepared[request.identity.command_id] = (prepared, record)
                elif prepared.request is not request or prepared.h1_delivery_binding != binding:
                    raise ValueError("prepared H1 delivery binding differs")
                return prepared
        except OSError, RuntimeError, TypeError, ValueError:
            return PublicationRejected(
                kind="DENIED",
                tenant_id=request.identity.tenant_id,
                command_id=request.identity.command_id,
                reason="issued H1 completion candidate differs",
            )

    def check_prepared(
        self, prepared: PreparedOwnerPublication
    ) -> Literal["DENIED", "STALE", "INDETERMINATE"] | None:
        runtime = self._runtime
        if self._revoked or runtime is None:
            return "DENIED"
        entry = self._prepared.get(prepared.request.identity.command_id)
        if entry is None or entry[0] != prepared:
            return "DENIED"
        try:
            from chiplog.composition.h1_live_readplan_source import H1LiveReadPlanSource

            readplan = getattr(runtime, "_h1_live_readplan_source", None)
            if type(readplan) is not H1LiveReadPlanSource:
                return "DENIED"
            manifest = readplan._admit_issued_manifest(
                capture=entry[1].readplan_capture,
                session=entry[1].enrollment.session,
            )
            if manifest != prepared.request.expected:
                return "STALE"
            return None
        except OSError, RuntimeError, TypeError, ValueError:
            return "STALE"

    def materialization_state(
        self, decision: SelectedOwnerDecision
    ) -> Literal["ABSENT", "EXACT_PREFIX", "COMPLETE", "CONFLICT"]:
        runtime = self._runtime
        if self._revoked or runtime is None:
            return "CONFLICT"
        try:
            from chiplog.composition.r14_loop_history import _selected_h1_completion

            _selected_h1_completion(runtime, decision)
            state, _, _ = runtime._selected_physical_state(runtime._owner_command(decision))
            if state in ("ABSENT", "COMPLETE", "CONFLICT"):
                return cast(Literal["ABSENT", "COMPLETE", "CONFLICT"], state)
            return "CONFLICT"
        except OSError, RuntimeError, TypeError, ValueError:
            return "CONFLICT"

    def check_selected_predecessor(self, decision: SelectedOwnerDecision) -> bool:
        return self.materialization_state(decision) == "ABSENT"
