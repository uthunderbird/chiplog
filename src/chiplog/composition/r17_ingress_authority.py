"""Private broker-owned PRE_AUTH retained custody issuance; no source authentication."""

from __future__ import annotations

import json
import secrets
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from chiplog.adapters.driven.ingress_retained_source import RetainedSourceObservation
from chiplog.composition.r17_ingress_history import (
    IngressHistory,
    read_ingress_history,
    read_manifest,
    wire_record,
)
from chiplog.platform._ingress_contracts import ReceiptToken
from chiplog.platform._owner_publication_contracts import (
    BrokerIngressAuthentication,
    ExactReplayQuery,
    InvocationProofRef,
    OwnerCommandBytes,
    PublicationIdentity,
    PublicationRejected,
    RegisteredPublication,
    SingleOwnerBatch,
)
from chiplog.platform.authority_reads import capture_authority_snapshot_commitment
from chiplog.platform.broker import BrokerSession
from chiplog.platform.ingress_custody_records import (
    CustodyCommand,
    RetentionClaim,
    canonical,
    digest,
    prepare_custody,
    subject_id,
)
from chiplog.platform.owner_publications import PreparedOwnerPublication, SelectedOwnerDecision
from chiplog.platform.workspace_snapshot import read_connection

if TYPE_CHECKING:
    from chiplog.composition.r17_ingress_runtime import R17IngressRuntime


@dataclass(frozen=True)
class _Captured:
    history: IngressHistory
    read_state: bytes
    session: BrokerSession
    graph: object
    trust: object


@dataclass(frozen=True)
class _Invocation:
    proof: InvocationProofRef
    identity: PublicationIdentity
    command: OwnerCommandBytes
    session: BrokerSession
    deadline: int


@dataclass(frozen=True)
class _Issued:
    prepared: PreparedOwnerPublication
    captured: _Captured
    invocation: _Invocation


class IngressAuthority:
    """One actual adapter observation, captured privately for this operation."""

    def __init__(self, runtime: R17IngressRuntime, slot_id: str) -> None:
        self.runtime = runtime
        self.source = runtime._retained_source()
        self.observed: RetainedSourceObservation = self.source.observe(slot_id)
        self.claim = RetentionClaim(
            slot_id=slot_id,
            raw_digest=self.observed.raw_digest,
            byte_count=self.observed.byte_count,
            proof=self.observed.proof,
            observation_bytes=self.observed.canonical_bytes(),
        )
        self._invocations: dict[str, _Invocation] = {}
        self._issued: dict[str, _Issued] = {}

    def _current_session(self) -> BrokerSession:
        runtime = self.runtime
        runtime._check_database_identity()
        if runtime._retained_source() is not self.source:
            raise ValueError("retained source adapter changed")
        self.source.verify(self.observed)
        trust = runtime._trust.verify()
        profile = runtime._ingress_profile()
        graph = runtime._supervisor.admitted_graph
        admission = runtime._supervisor.admission_evidence
        session = runtime._supervisor.runtime().session("deployment_trust")
        ledger = runtime._read_ledger.current_state(runtime._tenant_id)
        if (
            trust is None
            or trust.phase != "ACTIVE"
            or (trust.tenant_id, trust.database_instance_id)
            != (profile.tenant_id, profile.database_id)
            or graph is None
            or admission is None
            or not admission.accepted
            or admission.observation != runtime._trust.capture_verified_observation()
            or ledger.owner_draining
            or (ledger.tenant_id, ledger.broker_epoch, ledger.owner_generation)
            != (session.tenant_id, session.broker_epoch, session.generation_id)
            or (graph.tenant_id, graph.broker_epoch, graph.generation_id)
            != (session.tenant_id, session.broker_epoch, session.generation_id)
        ):
            raise ValueError("retained ingress runtime admission or generation is not current")
        return BrokerSession(
            tenant_id=session.tenant_id,
            broker_epoch=session.broker_epoch,
            generation_id=session.generation_id,
            owner_id="broker",
            session_id="broker:" + session.generation_id,
        )

    def capture(self) -> _Captured:
        with self.runtime._authority_gate().hold():
            self.runtime._require_no_pending()
            session = self._current_session()
            history = read_ingress_history(self.runtime)
            ledger = self.runtime._read_ledger.current_state(self.runtime._tenant_id)
            if ledger.materialization_commitment != history.commitment:
                raise ValueError("retained ingress ledger differs from physical cut")
            return _Captured(
                history,
                ledger.canonical_bytes(),
                session,
                self.runtime._supervisor.admitted_graph,
                self.runtime._trust.capture_verified_observation(),
            )

    def invocation(
        self, identity: PublicationIdentity, command: OwnerCommandBytes
    ) -> InvocationProofRef:
        with self.runtime._authority_gate().hold():
            session = self._current_session()
            nonce = secrets.token_hex(32)
            proof = InvocationProofRef(
                issuance_id=nonce,
                issuance_fingerprint=digest(
                    nonce.encode() + command.canonical_bytes + self.claim.observation_bytes
                ),
                broker_epoch=str(session.broker_epoch),
                broker_session=session.session_id,
                runtime_generation=session.generation_id,
                operation_subject=identity.command_id,
            )
            self._invocations[nonce] = _Invocation(
                proof,
                identity,
                command,
                session,
                time.monotonic_ns() + 5_000_000_000,
            )
            return proof

    def authenticate_replay(self, query: ExactReplayQuery) -> PublicationRejected | None:
        with self.runtime._authority_gate().hold():
            entry = self._invocations.get(query.current_invocation.issuance_id)
            try:
                if (
                    entry is None
                    or entry.proof != query.current_invocation
                    or entry.identity != query.identity
                    or query.original_commands != (entry.command,)
                    or query.identity.tenant_id != self.runtime._tenant_id
                    or time.monotonic_ns() >= entry.deadline
                    or self._current_session() != entry.session
                ):
                    raise ValueError("no current private retained-source invocation")
                original = CustodyCommand.model_validate_json(entry.command.canonical_bytes)
                if original.retention != self.claim or query.operation != original.operation:
                    raise ValueError("retained replay input differs")
            except OSError, RuntimeError, ValueError, TypeError:
                return PublicationRejected(
                    kind="DENIED",
                    tenant_id=query.identity.tenant_id,
                    command_id=query.identity.command_id,
                    reason="no current private retained-source invocation",
                )
            return None

    def issue(
        self,
        operation: Literal[
            "ingress.allocate_receipt_token",
            "ingress.stage_raw_bytes",
            "ingress.publish_custody_successor",
        ],
        token: ReceiptToken,
        raw_bytes: bytes | None,
    ) -> SingleOwnerBatch:
        with self.runtime._authority_gate().hold():
            captured = self.capture()
            profile = self.runtime._ingress_profile()
            records = captured.history.records
            command = CustodyCommand(
                operation=operation,
                command_id=subject_id(profile, self.observed.slot_id, operation),
                profile=profile,
                predecessor=records[-1].head() if records else None,
                token=token,
                retention=self.claim,
                raw_bytes=raw_bytes,
                broker_session=captured.session,
                read_state_bytes=captured.read_state,
                tenant_frontier=captured.history.tenant_frontier,
            )
            record, _ = prepare_custody(command, command.predecessor, captured.history.custody)
            wire = wire_record(record)
            owner_command = OwnerCommandBytes(
                owner="broker_ingress",
                schema_id=command.schema_id,
                canonical_bytes=canonical(command),
                fingerprint=digest(canonical(command)),
            )
            identity = PublicationIdentity(
                tenant_id=profile.tenant_id,
                command_id=command.command_id,
                command_fingerprint=owner_command.fingerprint,
                canonicalization_version="chiplog.owner-publication.v1",
            )
            proof = self.invocation(identity, owner_command)
            batch = SingleOwnerBatch(
                operation=operation,
                identity=identity,
                authentication=BrokerIngressAuthentication(
                    invocation=proof,
                    ingress_row=profile.source_identity,
                    source_contract_head=profile.head().head,
                    admission_epoch_head=profile.epoch().head,
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
            if self.check_prepared(prepared) is not None:
                del self._issued[prepared.issuance_id]
                raise ValueError("retained custody sources changed before issuance")
            return batch

    async def prepare(
        self, request: RegisteredPublication
    ) -> PreparedOwnerPublication | PublicationRejected:
        matches = [
            entry.prepared for entry in self._issued.values() if entry.prepared.request is request
        ]
        if len(matches) == 1:
            return matches[0]
        return PublicationRejected(
            kind="DENIED",
            tenant_id=request.identity.tenant_id,
            command_id=request.identity.command_id,
            reason="unissued retained custody object",
        )

    def check_prepared(
        self, prepared: PreparedOwnerPublication
    ) -> Literal["DENIED", "STALE", "INDETERMINATE"] | None:
        with self.runtime._authority_gate().hold():
            entry = self._issued.get(prepared.issuance_id)
            if entry is None or entry.prepared is not prepared:
                return "DENIED"
            if time.monotonic_ns() >= entry.invocation.deadline:
                return "STALE"
            try:
                if self.capture() != entry.captured:
                    return "STALE"
            except OSError, RuntimeError, ValueError, TypeError:
                return "STALE"
            return None

    def materialization_state(
        self, decision: SelectedOwnerDecision
    ) -> Literal["ABSENT", "COMPLETE", "CONFLICT"]:
        with self.runtime._authority_gate().hold():
            history = read_ingress_history(self.runtime, allow_pending=True)
            selected = self.runtime._owner_decisions().lookup(
                self.runtime._tenant_id, decision.prepared.request.identity.command_id
            )
            if selected != decision:
                return "CONFLICT"
            command = decision.prepared.request
            with read_connection(self.runtime._database) as connection:
                if (
                    capture_authority_snapshot_commitment(connection, self.runtime._tenant_id)
                    != history.commitment
                ):
                    return "CONFLICT"
                publication = connection.execute(
                    "SELECT request_fingerprint,commit_sequence,record_ids FROM main.publications "
                    "WHERE tenant_id=? AND operation_kind=? AND idempotency_key=?",
                    (self.runtime._tenant_id, command.operation, command.identity.command_id),
                ).fetchone()
                self.runtime._check_database_identity()
                if publication is None:
                    return (
                        "ABSENT"
                        if history.commitment == decision.prepared.predecessor_commitment
                        else "CONFLICT"
                    )
                return "COMPLETE"

    def check_selected_predecessor(self, decision: SelectedOwnerDecision) -> bool:
        with self.runtime._authority_gate().hold():
            history = self.runtime._owner_decisions().snapshot()
            return (
                bool(history.decisions)
                and history.decisions[-1] == decision
                and self.runtime._pending_owners() == (decision,)
                and not self.runtime._pending()
                and not self.runtime._pending_gate_publications()
                and self.materialization_state(decision) == "ABSENT"
            )
