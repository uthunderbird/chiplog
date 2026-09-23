"""Actual socket/isolated-trust/private-writer path for one retained CLI successor."""

from __future__ import annotations

import json
import secrets
import time
from typing import TYPE_CHECKING

from chiplog.adapters.driven.cli_custody_socket import CliCustodySocket
from chiplog.capabilities.deployment_trust.cli_custody_contracts import (
    CliCustodyAuthenticated,
    CliCustodyHead,
    CliCustodyScope,
)
from chiplog.capabilities.deployment_trust.cli_custody_validation import CliCustodyOwnerCall
from chiplog.composition.r17_authenticated_records import (
    COMMAND_SCHEMA,
    OPERATION,
    frame,
    prepare_authenticated_custody,
)
from chiplog.composition.r17_ingress_authority import IngressAuthority, _Issued
from chiplog.composition.r17_ingress_history import read_manifest, record_head, wire_record
from chiplog.platform._owner_publication_contracts import (
    BrokerIngressAuthentication,
    BrokerPublicationResult,
    ExactReplayQuery,
    NoSelectedDecision,
    OwnerCommandBytes,
    PublicationIdentity,
    PublicationRejected,
    SingleOwnerBatch,
)
from chiplog.platform.broker import BrokerSession, CallBudget, PublicPortCall, PublicPortSuccess
from chiplog.platform.ingress_authenticated_contracts import AuthenticatedCustodyCommand
from chiplog.platform.ingress_custody_records import canonical, digest, subject_id
from chiplog.platform.owner_publications import (
    BrokerPublicationCoordinator,
    PreparedOwnerPublication,
)

if TYPE_CHECKING:
    from chiplog.composition.r17_ingress_runtime import R17IngressRuntime


class AuthenticatedIngressAuthority(IngressAuthority):
    def __init__(self, runtime: R17IngressRuntime, slot_id: str) -> None:
        self._authorized = False
        super().__init__(runtime, slot_id)
        captured = self.capture()
        entries = [
            entry
            for entry in captured.history.custody.entries
            if entry.token.receive_slot == slot_id
        ]
        if len(entries) != 1 or entries[0].staged_bytes is None:
            raise ValueError("authenticated custody requires materialized raw staging")
        self.entry = entries[0]
        assert self.entry.staged_bytes is not None
        self.command_id = subject_id(
            runtime._ingress_profile(), slot_id, "ingress.publish_custody_successor"
        )
        scope = CliCustodyScope(
            tenant_id=runtime._tenant_id,
            database_id=runtime._ingress_profile().database_id,
            token_id=self.entry.token.token_id,
            token_state=CliCustodyHead.model_validate(self.entry.state_head.model_dump()),
            source_profile=CliCustodyHead.model_validate(
                runtime._ingress_profile().head().model_dump()
            ),
            original_token_bytes=canonical(self.entry.token),
            raw_digest=digest(self.entry.staged_bytes),
            raw_byte_count=len(self.entry.staged_bytes),
            replay_identity=self.command_id,
            original_subject=self.entry.token.token_id,
        )
        self.socket = CliCustodySocket(
            scope,
            self.entry.staged_bytes,
            broker_epoch=str(captured.session.broker_epoch),
            broker_session=captured.session.session_id,
            generation=captured.session.generation_id,
        )

    def _current_session(self) -> BrokerSession:
        session = super()._current_session()
        if self._authorized:
            self.socket.verify(self._request)
            if (
                self.runtime._trust.capture_verified_observation() != self._trust_observation
                or self.runtime._supervisor.runtime().session("deployment_trust")
                != self._sent.callee
                or time.monotonic_ns() >= self._sent.budget.absolute_deadline_ns
            ):
                raise ValueError("CLI trust authority changed")
        return session

    async def authenticate(self) -> None:
        request = await self.socket.receive()
        self.socket.verify(request)
        observation = self.runtime._trust.capture_verified_observation()
        callee = self.runtime._supervisor.runtime().session("deployment_trust")
        session = self._current_session()
        payload = CliCustodyOwnerCall(snapshot_bytes=observation.snapshot_bytes, request=request)
        sent = PublicPortCall(
            operation_id=OPERATION,
            request_id="cli-custody:" + secrets.token_hex(24),
            caller=session,
            callee=callee,
            schema_id="chiplog.cli.custody-owner-call.v1",
            canonical_payload=payload.canonical_bytes(),
            budget=CallBudget(
                remaining_calls=1,
                remaining_depth=1,
                policy_version=1,
                absolute_deadline_ns=request.socket.valid_until_ns,
            ),
        )
        if len(frame(sent)) > 262144:
            raise ValueError("CLI owner call frame exceeds bound")
        returned = await self.runtime._supervisor.runtime().call(sent)
        if (
            not isinstance(returned, PublicPortSuccess)
            or returned.request_id != sent.request_id
            or returned.responder != sent.callee
            or returned.schema_id != "chiplog.cli.custody-decision.v1"
        ):
            raise ValueError("CLI trust owner failed or returned foreign frame")
        result = CliCustodyAuthenticated.model_validate_json(returned.canonical_payload)
        if result.reference.request_fingerprint != digest(request.canonical_bytes()):
            raise ValueError("CLI trust owner changed requested scope")
        self._request, self._sent, self._returned = request, sent, returned
        self._trust_observation = observation
        self._authorized = True
        self._current_session()

    def authenticate_replay(self, query: ExactReplayQuery) -> PublicationRejected | None:
        with self.runtime._authority_gate().hold():
            entry = self._invocations.get(query.current_invocation.issuance_id)
            try:
                if (
                    not self._authorized
                    or entry is None
                    or entry.proof != query.current_invocation
                    or entry.identity != query.identity
                    or query.original_commands != (entry.command,)
                    or self._current_session() != entry.session
                    or time.monotonic_ns() >= entry.deadline
                ):
                    raise ValueError("unissued current CLI invocation")
                original = AuthenticatedCustodyCommand.model_validate_json(
                    entry.command.canonical_bytes
                )
                if (
                    original.command_id != self.command_id
                    or original.retention != self.claim
                    or original.token != self.entry.token
                    or original.raw_bytes != self.socket.raw
                    or original.operation != query.operation
                ):
                    raise ValueError("CLI replay substituted original scope")
            except OSError, RuntimeError, ValueError, TypeError:
                return PublicationRejected(
                    kind="DENIED",
                    tenant_id=query.identity.tenant_id,
                    command_id=query.identity.command_id,
                    reason="no exact current CLI invocation",
                )
            return None

    def issue_authenticated(self) -> SingleOwnerBatch:
        if not self._authorized:
            raise ValueError("no actual authenticated CLI exchange")
        with self.runtime._authority_gate().hold():
            captured = self.capture()
            records = captured.history.records
            if not records or self.entry.staged_bytes is None:
                raise ValueError("missing complete staged predecessor")
            command = AuthenticatedCustodyCommand(
                command_id=self.command_id,
                profile=self.runtime._ingress_profile(),
                predecessor=record_head(records[-1]),
                token=self.entry.token,
                staged_head=self.entry.state_head,
                retention=self.claim,
                raw_bytes=self.entry.staged_bytes,
                authentication_request_bytes=frame(self._sent),
                authentication_result_bytes=frame(self._returned),
                broker_session=captured.session,
                read_state_bytes=captured.read_state,
                tenant_frontier=captured.history.tenant_frontier,
            )
            record, _ = prepare_authenticated_custody(
                command, record_head(records[-1]), captured.history.custody
            )
            wire = wire_record(record)
            owner_command = OwnerCommandBytes(
                owner="broker_ingress",
                schema_id=COMMAND_SCHEMA,
                canonical_bytes=canonical(command),
                fingerprint=digest(canonical(command)),
            )
            identity = PublicationIdentity(
                tenant_id=self.runtime._tenant_id,
                command_id=self.command_id,
                command_fingerprint=owner_command.fingerprint,
                canonicalization_version="chiplog.owner-publication.v1",
            )
            proof = self.invocation(identity, owner_command)
            batch = SingleOwnerBatch(
                operation=command.operation,
                identity=identity,
                authentication=BrokerIngressAuthentication(
                    invocation=proof,
                    ingress_row=command.profile.source_identity,
                    source_contract_head=command.profile.head().head,
                    admission_epoch_head=command.profile.epoch().head,
                    admission_fence=0,
                ),
                expected=read_manifest(command, records),
                command=owner_command,
                complete_records=(wire,),
                complete_batch_fingerprint=digest(
                    json.dumps(
                        [wire.model_dump(mode="json")],
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=False,
                    ).encode()
                ),
            )
            prepared = PreparedOwnerPublication(
                batch, secrets.token_hex(32), "r6", 0, captured.history.commitment
            )
            self._issued[prepared.issuance_id] = _Issued(
                prepared, captured, self._invocations[proof.issuance_id]
            )
            return batch

    async def admit(self) -> BrokerPublicationResult:
        await self.authenticate()
        coordinator = BrokerPublicationCoordinator(
            self.runtime._appender, self, self.runtime._owner_decisions()
        )
        selected = self.runtime._owner_decisions().lookup(self.runtime._tenant_id, self.command_id)
        if selected is not None:
            original = selected.prepared.request
            if (
                not isinstance(original, SingleOwnerBatch)
                or original.command.schema_id != COMMAND_SCHEMA
            ):
                return PublicationRejected(
                    kind="CONFLICT",
                    tenant_id=self.runtime._tenant_id,
                    command_id=self.command_id,
                    reason="receipt already has another immutable successor",
                )
            invocation = self.invocation(original.identity, original.command)
            result = coordinator.lookup_exact(
                ExactReplayQuery(
                    identity=original.identity,
                    operation=original.operation,
                    current_invocation=invocation,
                    original_commands=(original.command,),
                )
            )
            if isinstance(result, NoSelectedDecision):
                raise ValueError("selected CLI custody disappeared")
            if isinstance(result, PublicationRejected) and result.kind == "HOLD":
                return await coordinator.recover_selected(self.runtime._tenant_id, self.command_id)
            return result
        return await coordinator.commit(self.issue_authenticated())
