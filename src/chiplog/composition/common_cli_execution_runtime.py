"""One H0 runtime: selected retained CLI custody to a native created Run."""

from __future__ import annotations

import base64
import hashlib
import json
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, ClassVar, Literal, cast

from chiplog.adapters.driven.loop_hermetic import HermeticModel
from chiplog.architecture.r7_runtime import R14_R17_H0_PRODUCTION_MANIFEST
from chiplog.capabilities.agent_loop.call_acceptance_contracts import (
    CallAuthorityObservation,
    CallSubjectHead,
)
from chiplog.capabilities.agent_loop.contracts import BudgetPolicy, LoopRejected
from chiplog.capabilities.agent_loop.delivery_contracts import (
    ExactHead,
    OriginSelection,
    ProviderRecipient,
)
from chiplog.capabilities.agent_loop.execution_initialization_contracts import (
    ExecutionInitializationCut,
    PrepareInboxExecution,
    SelectedAdmittedRunInput,
)
from chiplog.capabilities.agent_loop.execution_transition_contracts import CreateExecutionRun
from chiplog.capabilities.agent_loop.recovery_contracts import Absent, Present
from chiplog.composition.common_execution_driver_contracts import (
    CommonExecutionResultV1,
    DriveInputRequestV1,
    ExecutionDriverRejectedV1,
    ExecutionPendingReceiptV1,
    LookupExecutionRequestV1,
    SelectedExecutionReceiptV1,
)
from chiplog.composition.r7_planning import _open_runtime
from chiplog.composition.r14_execution_inbox_records import (
    RetainedInboxExecutionInitialization,
    inbox_initialization_command,
)
from chiplog.composition.r14_loop_history import read_execution_history
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources, ResourceObservation
from chiplog.composition.r16_dispatch_runtime import ExecutionDispatchRuntime, _configured
from chiplog.composition.r17_authenticated_records import decode_authentication
from chiplog.composition.r17_ingress_registry import RETAINED_CLI_READER_ID
from chiplog.composition.r17_ingress_runtime import R17IngressRuntime
from chiplog.platform._ingress_contracts import Head
from chiplog.platform.ingress_custody_records import canonical, reference
from chiplog.platform.ingress_transition_contracts import RetainedIngressSource

R17_RETAINED_READER_ID = RETAINED_CLI_READER_ID


class _DriverConflict(Exception):
    pass


def _call_head(value: Head) -> CallSubjectHead:
    return CallSubjectHead(
        subject_id=value.identity,
        revision=Present(head=value.head, fingerprint=value.fingerprint),
    )


def _loop_head(value: Any) -> ExactHead:
    return ExactHead(identity=value.subject_id, head=value.head, fingerprint=value.fingerprint)


class CommonCliExecutionRuntime(R17IngressRuntime, ExecutionDispatchRuntime):
    """Cooperative ingress/dispatch assembly sharing exactly one writer and gate."""

    _record_contracts: ClassVar[dict[str, str]] = {
        **R17IngressRuntime._record_contracts,
        **ExecutionDispatchRuntime._record_contracts,
    }
    _record_schema_variants: ClassVar[tuple[tuple[str, str], ...]] = tuple(
        dict.fromkeys(
            (
                *R17IngressRuntime._record_schema_variants,
                *ExecutionDispatchRuntime._record_schema_variants,
            )
        )
    )

    def _selected_input(
        self,
        request: DriveInputRequestV1,
        observation: ResourceObservation,
        *,
        historical: bool = False,
    ) -> SelectedAdmittedRunInput:
        source = request.selected_source
        if source.kind != "CLI_RETAINED_SELECTED_SOURCE_V1":
            raise PermissionError("only the deployed retained CLI route is mounted")
        admitted = self.read_admitted_inbox(source.original_ingress_identity.command_id)
        if admitted is None:
            raise PermissionError("selected admitted inbox is absent")
        record = admitted.record
        profile = record.command.profile
        retained = RetainedIngressSource(
            source=record.command.retention.proof,
            reader_id=R17_RETAINED_READER_ID,
            schema_id="chiplog.ingress.retained-source-observation.v1",
            canonical_source_bytes=record.command.retention.observation_bytes,
        )
        if (
            request.identity.tenant_id != self._tenant_id
            or request.identity.database_id != profile.database_id
            or profile != self._ingress_profile()
            or source.expected_reader_id != R17_RETAINED_READER_ID
            or source.source_binding != record.command.token.source
            or source.selected_ingress_decision != admitted.selected_decision
            or source.source_head != retained.source
            or source.retained_source != retained
            or request.identity.original_ingress_request_fingerprint
            != record.inbox.authentication_request_fingerprint
        ):
            raise PermissionError("driver source differs from independently selected R17 inbox")
        _, authenticated = decode_authentication(record.command)
        raw = record.inbox.raw_bytes
        prompt = raw.decode("utf-8")
        if not prompt:
            raise ValueError("selected CLI prompt is empty")
        resources = self._require_dispatch_resources()
        resource_recipient = (
            resources.historical_recipient(observation)
            if historical
            else resources.recipient(observation)
        )
        if (
            authenticated.reference.tenant_id != self._tenant_id
            or authenticated.reference.principal_id != resource_recipient.recipient
        ):
            raise PermissionError("authenticated CLI principal differs from registered recipient")
        recipient = ProviderRecipient(
            provider_id=resource_recipient.provider,
            account_id=resource_recipient.account,
            recipient_id=resource_recipient.recipient,
            endpoint=_loop_head(resource_recipient.endpoint),
            canonical_address=resource_recipient.canonical_address,
            credential_binding=_loop_head(resource_recipient.credential_binding),
        )
        token = reference(
            "ingress.token:" + record.command.token.token_id,
            canonical(record.command.token),
        )
        authentication = record.inbox.authentication
        return SelectedAdmittedRunInput(
            tenant_id=profile.tenant_id,
            database_id=profile.database_id,
            source_class="CLI",
            source_contract=_call_head(profile.head()),
            token=_call_head(token),
            custody=_call_head(admitted.physical_record),
            inbox=_call_head(record.custody.inbox),
            selected_decision=_call_head(admitted.selected_decision),
            physical_record=_call_head(admitted.physical_record),
            commit_sequence=admitted.commit_sequence,
            raw_input_bytes=raw,
            custody_schema=record.schema_id,
            canonical_custody_record=json.dumps(
                record.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
            ).encode(),
            inbox_schema=record.inbox.schema_id,
            canonical_inbox_record=json.dumps(
                record.inbox.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
            ).encode(),
            source_authentication=_call_head(authentication.proof),
            authentication_schema="chiplog.cli.custody-decision.v1",
            canonical_authentication=record.command.authentication_result_bytes,
            normalization=_call_head(record.custody.inbox),
            normalization_schema=record.inbox.schema_id,
            canonical_normalization_record=json.dumps(
                record.inbox.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
            ).encode(),
            normalized_prompt=prompt,
            principal_id=authenticated.reference.principal_id,
            contour_head=authentication.principal_contour.head,
            origin=OriginSelection(
                ingress_binding=ExactHead(**record.custody.inbox.model_dump()), recipient=recipient
            ),
        )

    @staticmethod
    def _run_id(admitted: SelectedAdmittedRunInput) -> str:
        raw = json.dumps(
            [admitted.tenant_id, admitted.database_id, admitted.inbox.subject_id],
            separators=(",", ":"),
        ).encode()
        return "run:inbox:" + hashlib.sha256(b"chiplog.h0.run.v1\x00" + raw).hexdigest()

    async def drive_input(self, request: DriveInputRequestV1) -> CommonExecutionResultV1:
        try:
            await self._execution_actor("hermetic-ingress")
            existing = self._find(request.identity, request.original_driver_command_fingerprint())
            if existing is not None:
                return self._receipt(*existing, "EXACT_REPLAY")
            observation = self._require_dispatch_resources().observe()
            admitted = self._selected_input(request, observation)
            run_id = self._run_id(admitted)
            for _, _, raw in self._loop_decisions().entries():
                entry = json.loads(raw)
                value = entry.get("inbox_initialization")
                if isinstance(value, str):
                    evidence = RetainedInboxExecutionInitialization.model_validate_json(value)
                    if evidence.proposal.run.run_id == run_id:
                        raise _DriverConflict(
                            "selected inbox already belongs to another driver command"
                        )
            snapshot = read_execution_history(self)
            create = CreateExecutionRun(
                command_id="create:inbox:" + run_id.rsplit(":", 1)[-1],
                tenant=self._tenant_id,
                principal=admitted.principal_id,
                run_id=run_id,
                prompt=admitted.normalized_prompt,
                policy=BudgetPolicy(),
                origin=admitted.origin,
                contour_head=admitted.contour_head,
                policy_head="hermetic-policy-v1",
                worker_session=self.current_worker(),
            )
            endpoint = _call_head(observation_to_head(observation))
            cut = ExecutionInitializationCut(
                tenant_id=self._tenant_id,
                database_id=admitted.database_id,
                tenant_commit_sequence=snapshot.tenant_head,
                materialization_commitment=self._commitment_journal.load(self._tenant_id)
                or "0" * 64,
                initial_run_absence=Absent(),
                worker_session_id=self.current_worker(),
                runtime_generation=self.current_worker(),
                authority_registry=endpoint,
                sources=(
                    CallAuthorityObservation(
                        source_id="dispatch-recipient",
                        family="RECIPIENT",
                        source=endpoint,
                        generation=observation.clock_epoch,
                        frontier=str(snapshot.tenant_head),
                        canonical_value_base64=base64.b64encode(
                            observation.endpoint_bytes
                        ).decode(),
                        observed_at_ns=time.monotonic_ns(),
                        valid_until_ns=time.monotonic_ns() + 60_000_000_000,
                    ),
                ),
            )
            prepared = PrepareInboxExecution(create=create, admitted=admitted, cut=cut)
            run = await self._publish_selected_inbox_initialization(
                "hermetic-ingress",
                prepared,
                driver_request_bytes=request.canonical_bytes(),
                driver_request_fingerprint=request.original_driver_command_fingerprint(),
                dispatch_observation=observation,
                source_guard=lambda: self._selected_input(request, observation) == admitted,
            )
            retained = self._find(request.identity, request.original_driver_command_fingerprint())
            if retained is None or retained[0].proposal.run != run:
                raise LoopRejected("selected inbox initialization disappeared")
            return self._receipt(*retained, "COMMITTED")
        except UnicodeDecodeError:
            return self._reject(request, "INVALID_INPUT", "selected CLI prompt is not UTF-8")
        except ValueError as error:
            return self._reject(request, "INVALID_INPUT", str(error))
        except PermissionError as error:
            return self._reject(request, "DENIED", str(error))
        except _DriverConflict as error:
            return self._reject(request, "CONFLICT", str(error))
        except LoopRejected as error:
            return self._reject(request, "STALE", str(error))

    async def lookup_execution(self, request: LookupExecutionRequestV1) -> CommonExecutionResultV1:
        try:
            await self._execution_actor("hermetic-ingress")
            found = self._find(request.identity, request.original_driver_command_fingerprint)
        except _DriverConflict as error:
            return ExecutionDriverRejectedV1(
                identity=request.identity,
                original_driver_command_fingerprint=request.original_driver_command_fingerprint,
                code="CONFLICT",
                reason=str(error),
            )
        except LoopRejected as error:
            return ExecutionDriverRejectedV1(
                identity=request.identity,
                original_driver_command_fingerprint=request.original_driver_command_fingerprint,
                code="DENIED",
                reason=str(error),
            )
        if found is None:
            return ExecutionPendingReceiptV1(
                identity=request.identity,
                original_driver_command_fingerprint=request.original_driver_command_fingerprint,
                phase="NO_RUN",
            )
        return self._receipt(*found, "EXACT_REPLAY")

    def _find(
        self, identity: object, fingerprint: str
    ) -> tuple[RetainedInboxExecutionInitialization, str] | None:
        for decision_id, _, raw in self._loop_decisions().entries():
            entry = json.loads(raw)
            value = entry.get("inbox_initialization")
            if entry.get("kind") != "DECIDED":
                if isinstance(value, str):
                    raise LoopRejected("unselected inbox initialization evidence is present")
                continue
            if (
                entry.get("operation_kind") == "agent_loop.inbox-initialization.v1"
                and not isinstance(value, str)
            ) or (
                isinstance(value, str)
                and entry.get("operation_kind") != "agent_loop.inbox-initialization.v1"
            ):
                raise LoopRejected("selected inbox initialization operation/evidence differs")
            if not isinstance(value, str):
                continue
            evidence = RetainedInboxExecutionInitialization.model_validate_json(value)
            command = self._publication(entry)
            if (
                evidence.canonical_bytes().decode() != value
                or command != inbox_initialization_command(evidence)
                or command.idempotency_key != evidence.proposal.run.head
                or command.expected_head != evidence.expected_head
                or entry.get("predecessor") != evidence.predecessor_commitment
            ):
                raise LoopRejected("selected inbox initialization evidence differs")
            wire = DriveInputRequestV1.model_validate_json(evidence.driver_request_bytes)
            if wire.identity == identity:
                if evidence.driver_request_fingerprint != fingerprint:
                    raise _DriverConflict(
                        "driver command identity has another immutable fingerprint"
                    )
                self._validate_historical(evidence, wire)
                state, actual, anchored = self._selected_physical_state(command)
                if state != "COMPLETE" or actual != anchored:
                    raise LoopRejected("selected inbox initialization is not materialized")
                if evidence.proposal.run not in read_execution_history(self).records:
                    raise LoopRejected("selected native inbox Run is absent")
                return evidence, decision_id
        return None

    def _validate_historical(
        self, evidence: RetainedInboxExecutionInitialization, wire: DriveInputRequestV1
    ) -> None:
        observation = ResourceObservation(
            evidence.dispatch_grant_bytes,
            evidence.dispatch_credential_bytes,
            evidence.dispatch_endpoint_bytes,
            evidence.dispatch_clock_epoch,
            evidence.dispatch_signature,
        )
        recipient = self._require_dispatch_resources().historical_recipient(observation)
        origin = evidence.request.admitted.origin
        if (
            (
                recipient.provider,
                recipient.account,
                recipient.recipient,
                recipient.canonical_address,
            )
            != (
                origin.recipient.provider_id,
                origin.recipient.account_id,
                origin.recipient.recipient_id,
                origin.recipient.canonical_address,
            )
            or _loop_head(recipient.endpoint) != origin.recipient.endpoint
            or _loop_head(recipient.credential_binding) != origin.recipient.credential_binding
        ):
            raise LoopRejected("historical selected recipient differs")
        admitted = self.read_admitted_inbox(wire.identity.original_ingress_identity.command_id)
        if admitted is None:
            raise LoopRejected("historical selected inbox is absent")
        decoded = self._selected_input(wire, observation, historical=True)
        if decoded != evidence.request.admitted:
            raise LoopRejected("historical selected R17 input differs")

    def _receipt(
        self,
        evidence: RetainedInboxExecutionInitialization,
        decision_id: str,
        disposition: Literal["COMMITTED", "EXACT_REPLAY"],
    ) -> SelectedExecutionReceiptV1:
        wire = DriveInputRequestV1.model_validate_json(evidence.driver_request_bytes)
        run, admitted = evidence.proposal.run, evidence.request.admitted
        command = inbox_initialization_command(evidence)

        def ingress_head(value: CallSubjectHead) -> Head:
            assert isinstance(value.revision, Present)
            return Head(
                identity=value.subject_id,
                head=value.revision.head,
                fingerprint=value.revision.fingerprint,
            )

        return SelectedExecutionReceiptV1(
            disposition=disposition,
            identity=wire.identity,
            original_driver_command_fingerprint=evidence.driver_request_fingerprint,
            selected_ingress_decision=ingress_head(admitted.selected_decision),
            selected_custody=ingress_head(admitted.custody),
            selected_admitted_input=ingress_head(admitted.inbox),
            stable_run_lineage_id=run.run_id,
            selected_run_head=Head(
                identity=run.head,
                head=run.head,
                fingerprint=hashlib.sha256(run.canonical_bytes()).hexdigest(),
            ),
            selected_run_state=run.state,
            selected_journal_decision=Head(
                identity=command.idempotency_key,
                head=decision_id,
                fingerprint=hashlib.sha256(
                    next(
                        raw
                        for key, _, raw in self._loop_decisions().entries()
                        if key == decision_id
                    )
                ).hexdigest(),
            ),
            commit_sequence=command.expected_head + 1,
            phase="INITIALIZED",
        )

    def _reject(
        self,
        request: DriveInputRequestV1,
        code: Literal["HOLD", "CONFLICT", "STALE", "DENIED", "INVALID_INPUT", "INTEGRITY_FAULT"],
        reason: str,
    ) -> ExecutionDriverRejectedV1:
        return ExecutionDriverRejectedV1(
            identity=request.identity,
            original_driver_command_fingerprint=request.original_driver_command_fingerprint(),
            code=code,
            reason=reason,
        )


def observation_to_head(observation: ResourceObservation) -> Head:
    return reference("hermetic-dispatch-observation", observation.endpoint_bytes)


@asynccontextmanager
async def open_common_cli_execution_runtime(
    database: Path, *, resources: HermeticDispatchResources, responses: tuple[bytes, ...] = ()
) -> AsyncIterator[CommonCliExecutionRuntime]:
    model = HermeticModel(responses)
    with _configured(resources):
        async with _open_runtime(
            database,
            tenant_id="hermetic-tenant",
            operator_secret=b"r13-hermetic-only",
            runtime_type=CommonCliExecutionRuntime,
            manifest=R14_R17_H0_PRODUCTION_MANIFEST,
            extra_leaves={
                "model": model,
                "effects_transport": resources.require_original_provider(),
            },
        ) as opened:
            runtime = cast(CommonCliExecutionRuntime, opened)
            runtime._execution_model = model
            model.session = runtime
            if runtime._trust.verify() is None:
                await runtime.bootstrap(
                    database_instance_id="hermetic-database",
                    principal_id="hermetic-principal",
                    credential_id="hermetic-credential",
                    session_id="hermetic-session",
                    token="hermetic-bootstrap",
                )
            yield runtime
