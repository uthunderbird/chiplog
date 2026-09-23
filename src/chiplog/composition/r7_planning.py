"""Executable R7 planning slice: broker authority around an isolated policy owner."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import secrets
import sqlite3
import time
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, ClassVar, Literal, cast

from chiplog.adapters.driven.deployment_trust import (
    IndependentTenantDecisionJournal,
    SQLiteTrustMaterializer,
)
from chiplog.adapters.driven.planning_sqlite import _publication
from chiplog.architecture.r7_runtime import R7_PRODUCTION_MANIFEST, RuntimeAssemblyManifest
from chiplog.architecture.r7_storage_surface import AUTHORITY_STORAGE_SURFACE_DIGEST
from chiplog.capabilities.planning import (
    CreateIntentionLine,
    PlanningCommittedResult,
    PlanningOutcome,
)
from chiplog.capabilities.planning.r7_boundary import (
    R7PlanningCreateDTO,
    R7PlanningRenderDTO,
    R7PlanningRenderResultDTO,
    R7PlanningResultDTO,
)
from chiplog.capabilities.planning.r8_boundary import R8PlanningRequest
from chiplog.domain_primitives import RecordId, TenantId
from chiplog.platform._sqlite import (
    EventAppender,
    FenceAdvanceCommand,
    PhysicalPublicationCommand,
    PhysicalRecord,
    SQLiteMaterializer,
)
from chiplog.platform.authority_ledger import OperationDisposition, OperationToken
from chiplog.platform.authority_reads import (
    AMR_FINGERPRINT,
    AuthorityCommitmentJournal,
    BrokerAuthorityReader,
    capture_authority_storage_state,
)
from chiplog.platform.broker import BrokerSession, CallBudget, PublicPortCall, PublicPortRejected
from chiplog.platform.r7_leaves import ProductionClock, ProductionPlanningStore
from chiplog.platform.r7_trust import TrustOwnerCall, TrustOwnerResult, encode_trust_journal
from chiplog.platform.r7_trust_durability import BrokerTrustDurability
from chiplog.platform.read_ledger import BrokerReadLedger, BrokerReadState, ReadOperation

from .r7_supervisor import R4RuntimeAdmission, R7RuntimeSupervisor

_OPERATION = "planning.create_intention_line"
_SCHEMA = "chiplog.planning.record.v1"
_FENCE = "r6"
_READ_PAGE_SIZE = 200


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _record_id(value: object, tenant: TenantId) -> RecordId:
    if not isinstance(value, Mapping):
        raise ValueError("R7 planning result contains a foreign record identity")
    encoded_tenant = value.get("tenant_id")
    if encoded_tenant not in (tenant.value, {"value": tenant.value}):
        raise ValueError("R7 planning result contains a foreign record identity")
    identifier = value.get("value")
    if not isinstance(identifier, str):
        raise ValueError("R7 planning result contains a malformed record identity")
    return RecordId(tenant, identifier)


def _committed_result(value: object, tenant: TenantId) -> PlanningCommittedResult:
    if not isinstance(value, Mapping):
        raise ValueError("R7 planning result is malformed")
    return PlanningCommittedResult(
        _record_id(value["command_id"], tenant),
        _record_id(value["result_id"], tenant),
        _record_id(value["intention_line_id"], tenant),
        _record_id(value["revision_id"], tenant),
        _record_id(value["authorization_evidence_id"], tenant),
        int(cast(int, value["commit_sequence"])),
        tuple(
            (int(cast(int, item[0])), str(item[1]), _record_id(item[2], tenant))
            for item in cast(list[list[object]], value["allocation_manifest"])
        ),
        tuple(
            (_record_id(item[0], tenant), str(item[1]), str(item[2]))
            for item in cast(list[list[object]], value["record_manifest"])
        ),
        str(value["batch_fingerprint"]),
        str(value["result_fingerprint"]),
        str(value["publication_fingerprint"]),
    )


def _decode_owner_result(payload: bytes) -> R7PlanningResultDTO:
    values = json.loads(payload)
    if values.get("canonical_result_bytes") is not None:
        values["canonical_result_bytes"] = base64.b64decode(values["canonical_result_bytes"])
    result = R7PlanningResultDTO.model_validate(values)
    if result.canonical_bytes() != payload:
        raise ValueError("planning owner returned non-canonical bytes")
    return result


@dataclass(frozen=True)
class PreparedPlanningCandidate:
    """Isolated owner output, not a publication or durable planning receipt."""

    command_bytes: bytes
    request_bytes: bytes
    trust_reference_bytes: bytes
    owner_result_bytes: bytes
    owner_session: BrokerSession
    expected_tenant_head: int
    disposition: Literal["PREPARED"] = "PREPARED"


class R7PlanningRuntime:
    """The broker is the only object holding trust, SQLite, writer, and token authority."""

    _record_contracts: ClassVar[dict[str, str]] = {"planning": _SCHEMA}
    _record_schema_variants: ClassVar[tuple[tuple[str, str], ...]] = ()
    _derivative_contracts: ClassVar[tuple[str, ...]] = ()

    def _bind_appender(self) -> None:
        pass

    _planning_read_variant: ClassVar[
        Literal["PLANNING_PUBLICATIONS", "PLANNING_PUBLICATIONS_R13"]
    ] = "PLANNING_PUBLICATIONS"
    _planning_operation = _OPERATION
    _planning_schema = "chiplog.planning.public.create.v1"

    def __init__(
        self,
        tenant_id: str,
        trust: BrokerTrustDurability,
        journal: IndependentTenantDecisionJournal,
        database: Path,
        read_ledger: BrokerReadLedger,
        commitment_journal: AuthorityCommitmentJournal,
        appender: EventAppender,
        supervisor: R7RuntimeSupervisor,
    ) -> None:
        self._tenant_id = tenant_id
        self._trust = trust
        self._journal = journal
        self._database = database
        self._read_ledger = read_ledger
        self._commitment_journal = commitment_journal
        self._reader = BrokerAuthorityReader(database, "planning-database", read_ledger)
        self._appender = appender
        self._supervisor = supervisor
        self._generation_counter = 0
        self._planning_lane = asyncio.Lock()

    def _trust_call_request(
        self,
        mode: Literal["AUTHENTICATE", "BOOTSTRAP", "REVALIDATE", "RUNTIME_ADMISSION"],
        value: object,
    ) -> PublicPortCall:
        callee = (
            self._supervisor.quarantined_trust_session()
            if mode == "BOOTSTRAP"
            else self._supervisor.runtime().session("deployment_trust")
        )
        payload = TrustOwnerCall(
            mode=mode,
            snapshot_bytes=encode_trust_journal(self._trust.owner_snapshot_entries()),
            request_bytes=_canonical(value),
        )
        return PublicPortCall(
            operation_id=f"deployment_trust.{mode.lower()}",
            request_id=f"trust:{secrets.token_hex(8)}",
            caller=BrokerSession(
                tenant_id=self._tenant_id,
                broker_epoch=callee.broker_epoch,
                generation_id=callee.generation_id,
                owner_id="broker",
                session_id=f"broker:{callee.generation_id}",
            ),
            callee=callee,
            schema_id="chiplog.deployment-trust.owner-call.v1",
            canonical_payload=payload.canonical_bytes(),
            budget=CallBudget(
                remaining_calls=1,
                remaining_depth=1,
                absolute_deadline_ns=time.monotonic_ns() + 5_000_000_000,
                policy_version=1,
            ),
        )

    @staticmethod
    def _decode_trust_result(payload: bytes) -> TrustOwnerResult:
        values = json.loads(payload)
        if values["reference_bytes"] is not None:
            values["reference_bytes"] = base64.b64decode(values["reference_bytes"])
        result = TrustOwnerResult.model_validate(values)
        if result.canonical_bytes() != payload:
            raise ValueError("deployment-trust owner returned non-canonical bytes")
        return result

    async def _trust_call(
        self,
        mode: Literal["AUTHENTICATE", "BOOTSTRAP", "REVALIDATE", "RUNTIME_ADMISSION"],
        value: object,
    ) -> TrustOwnerResult:
        request = self._trust_call_request(mode, value)
        if mode == "BOOTSTRAP":
            response = await self._supervisor.call_quarantined_trust(request)
        else:
            response = await self._supervisor.runtime().call(request)
        if isinstance(response, PublicPortRejected):
            return TrustOwnerResult(
                disposition="INDETERMINATE", reference_bytes=None, reason=response.failure.reason
            )
        return self._decode_trust_result(response.canonical_payload)

    def _trust_call_sync(self, value: object) -> TrustOwnerResult:
        response = self._supervisor.runtime().call_sync(
            self._trust_call_request("REVALIDATE", value)
        )
        if isinstance(response, PublicPortRejected):
            return TrustOwnerResult(
                disposition="INDETERMINATE", reference_bytes=None, reason=response.failure.reason
            )
        return self._decode_trust_result(response.canonical_payload)

    def _start_generation(self) -> None:
        self._generation_counter += 1
        self._supervisor.start_generation(f"r7:{self._generation_counter}:{secrets.token_hex(8)}")
        self._reconcile_read_generation()

    def _reconcile_read_generation(self) -> None:
        runtime = self._supervisor.runtime()
        session = runtime.session("planning")
        commitment, observation = capture_authority_storage_state(self._database)
        trust_state = self._trust.verify()
        authenticated_commitment = self._commitment_journal.load(self._tenant_id)
        if authenticated_commitment is None:
            with sqlite3.connect(self._database) as connection:
                populated = any(
                    connection.execute(f"SELECT EXISTS(SELECT 1 FROM {table})").fetchone()[0]
                    for table in ("records", "publications")
                )
            if populated:
                raise RuntimeError("authority commitment journal is absent for populated storage")
            if trust_state is not None:
                raise RuntimeError("authority commitment journal is absent on restart")
            self._commitment_journal.commit(self._tenant_id, commitment)
            authenticated_commitment = commitment
        if not secrets.compare_digest(authenticated_commitment, commitment):
            record_id = "unknown"
            with sqlite3.connect(self._database) as connection:
                for candidate, canonical_bytes in connection.execute(
                    "SELECT record_id, canonical_bytes FROM records ORDER BY record_id"
                ):
                    try:
                        json.loads(bytes(canonical_bytes))
                    except UnicodeDecodeError, json.JSONDecodeError:
                        record_id = str(candidate)
                        break
            raise RuntimeError(
                f"operation=render tenant={self._tenant_id} record_id={record_id}: "
                "authority materialization differs from authenticated commitment"
            )
        self._read_ledger.reconcile_generation(
            BrokerReadState(
                tenant_id=self._tenant_id,
                broker_epoch=session.broker_epoch,
                credential_session_head=str(
                    trust_state.materialization_head if trust_state else "unavailable"
                ),
                endpoint_channel_head="local-cli",
                file_wal_observation=observation,
                journal_head=str(
                    trust_state.materialization_head if trust_state else "unavailable"
                ),
                materialization_commitment=authenticated_commitment,
                owner_generation=session.generation_id,
                owner_draining=False,
                principal_contour_head=str(
                    trust_state.trust_head if trust_state else "unavailable"
                ),
                storage_mutation_generation=0,
                trust_transition_head=str(trust_state.trust_head if trust_state else "unavailable"),
                authority_surface_digest=AUTHORITY_STORAGE_SURFACE_DIGEST,
                amr_fingerprint=AMR_FINGERPRINT,
            )
        )

    async def bootstrap(
        self,
        *,
        database_instance_id: str,
        principal_id: str,
        credential_id: str,
        session_id: str,
        token: str,
    ) -> None:
        self._supervisor.start_quarantine(f"r7-bootstrap:{secrets.token_hex(8)}")
        peer = f"uid:{os.getuid()}"
        bootstrap_decision = await self._trust_call(
            "BOOTSTRAP",
            {
                "credential_id": credential_id,
                "database_instance_id": database_instance_id,
                "expected_peer": peer,
                "peer": peer,
                "principal_id": principal_id,
                "session_id": session_id,
                "tenant_id": self._tenant_id,
                "token_fingerprint": hashlib.sha256(token.encode()).hexdigest(),
            },
        )
        if bootstrap_decision.disposition != "VALID":
            raise PermissionError(bootstrap_decision.reason or "bootstrap denied by trust owner")
        if bootstrap_decision.reference_bytes is None:
            raise RuntimeError("trust owner omitted authorized durable decision bytes")
        self._trust.apply_authorized(bootstrap_decision.reference_bytes)
        await self._appender.advance_fence(
            FenceAdvanceCommand(self._tenant_id, _FENCE, 0, allow_exact_replay=True)
        )
        commitment, _ = capture_authority_storage_state(self._database)
        self._commitment_journal.commit(self._tenant_id, commitment)
        self._start_generation()

    def restart_generation(self) -> None:
        self._start_generation()

    def _verified_publications(
        self, owner_id: Literal["planning", "projections"]
    ) -> tuple[Any, ...]:
        session = self._supervisor.runtime().session(owner_id)
        page_size = _READ_PAGE_SIZE
        after_commit_sequence = 0
        after_operation_kind = ""
        after_idempotency_key = ""
        publication_rows: list[list[object]] = []
        record_bytes: dict[str, bytes] = {}
        verified_frontier: int | None = None
        while True:
            attempt = secrets.token_hex(16)
            released = self._reader.execute(
                ReadOperation(
                    variant=self._planning_read_variant,
                    request_id=f"read:{attempt}",
                    read_attempt_id=attempt,
                    tenant_id=self._tenant_id,
                    broker_epoch=session.broker_epoch,
                    owner_id=owner_id,
                    generation_id=session.generation_id,
                    session_id=session.session_id,
                    capability_id="planning.read",
                    target_id="planning-store",
                    after_commit_sequence=after_commit_sequence,
                    after_operation_kind=after_operation_kind,
                    after_idempotency_key=after_idempotency_key,
                    max_rows=page_size,
                    response_slot_id=f"read-slot:{attempt}",
                )
            )
            if released.disposition != "RELEASED" or released.canonical_result_bytes is None:
                raise RuntimeError("authority read was not released")
            raw = json.loads(released.canonical_result_bytes)
            frontier = raw["tenant_frontier"]
            if type(frontier) is not int or frontier < 0:
                raise ValueError("missing verified tenant frontier")
            if verified_frontier is not None and verified_frontier != frontier:
                raise ValueError("authority frontier changed between bounded read pages")
            verified_frontier = frontier
            page = cast(list[list[object]], raw["publications"])
            publication_rows.extend(page)
            record_bytes.update(
                {
                    str(row[1]): base64.b64decode(cast(dict[str, str], row[4])["base64"])
                    for row in cast(list[list[object]], raw["records"])
                }
            )
            if len(page) < page_size:
                break
            after_commit_sequence = cast(int, page[-1][4])
            after_operation_kind = str(page[-1][1])
            after_idempotency_key = str(page[-1][2])
        self._verified_tenant_frontier = verified_frontier
        tenant = TenantId(self._tenant_id)
        return tuple(
            _publication(
                tenant,
                str(row[2]),
                str(row[3]),
                cast(int, row[4]),
                tuple(
                    (record_id, record_bytes[record_id]) for record_id in str(row[5]).split("\n")
                ),
            )
            for row in publication_rows
        )

    def _snapshot(self) -> bytes:
        publications = self._verified_publications("planning")
        return _canonical(
            {
                "commands": [
                    {
                        "command_id": item.command_id.value,
                        "request_fingerprint": item.request_fingerprint,
                        "result": asdict(item.result),
                    }
                    for item in publications
                ],
                "head": self._verified_tenant_frontier,
                "record_ids": [
                    record.record_id.value for item in publications for record in item.records
                ],
            }
        )

    def _projection_snapshot(self) -> bytes:
        publications = self._verified_publications("projections")
        return _canonical(
            {
                "lines": [
                    {
                        "intention_line_id": item.result.intention_line_id.value,
                        "purpose": str(item.records[3].fields["purpose"]),
                    }
                    for item in publications
                ],
                "record_count": sum(len(item.records) for item in publications),
            }
        )

    async def render(
        self, *, principal_id: str, credential_id: str, session_id: str
    ) -> tuple[str, ...]:
        peer = f"uid:{os.getuid()}"
        decision = await self._trust_call(
            "AUTHENTICATE",
            {
                "contour": "CLI",
                "credential_id": credential_id,
                "peer_credential": peer,
                "session_id": session_id,
            },
        )
        if (
            decision.disposition != "VALID"
            or decision.reference_bytes is None
            or json.loads(decision.reference_bytes)["tenant_id"] != self._tenant_id
            or json.loads(decision.reference_bytes)["principal_id"] != principal_id
        ):
            raise PermissionError("DENIED: caller identity does not match authenticated trust")
        request = R7PlanningRenderDTO(
            tenant_id=self._tenant_id,
            planning_snapshot_bytes=self._projection_snapshot(),
        )
        runtime = self._supervisor.runtime()
        callee = runtime.session("projections")
        response = await runtime.call(
            PublicPortCall(
                operation_id="projections.render_planning",
                request_id=f"render:{secrets.token_hex(8)}",
                caller=BrokerSession(
                    tenant_id=self._tenant_id,
                    broker_epoch=callee.broker_epoch,
                    generation_id=callee.generation_id,
                    owner_id="broker",
                    session_id=f"broker:{callee.generation_id}",
                ),
                callee=callee,
                schema_id="chiplog.planning.public.render.v1",
                canonical_payload=request.canonical_bytes(),
                budget=CallBudget(
                    remaining_calls=1,
                    remaining_depth=1,
                    absolute_deadline_ns=time.monotonic_ns() + 5_000_000_000,
                    policy_version=1,
                ),
            )
        )
        if isinstance(response, PublicPortRejected):
            raise RuntimeError(f"projection owner rejected render: {response.failure.reason}")
        values = json.loads(response.canonical_payload)
        values["lines"] = tuple(values["lines"])
        result = R7PlanningRenderResultDTO.model_validate(values)
        if result.canonical_bytes() != response.canonical_payload:
            raise ValueError("projection owner returned non-canonical bytes")
        return result.lines

    async def create(
        self,
        *,
        principal_id: str,
        credential_id: str,
        session_id: str,
        command: CreateIntentionLine,
    ) -> PlanningOutcome:
        async with self._planning_lane:
            return await self._create(
                principal_id=principal_id,
                credential_id=credential_id,
                session_id=session_id,
                command=command,
            )

    async def _prepare_create(
        self,
        *,
        principal_id: str,
        credential_id: str,
        session_id: str,
        command: CreateIntentionLine,
    ) -> PreparedPlanningCandidate | PlanningOutcome:
        """Authenticate and invoke the owner without issuing a physical publication."""
        peer = f"uid:{os.getuid()}"
        decision = await self._trust_call(
            "AUTHENTICATE",
            {
                "contour": "CLI",
                "credential_id": credential_id,
                "peer_credential": peer,
                "session_id": session_id,
            },
        )
        if decision.disposition != "VALID" or decision.reference_bytes is None:
            disposition = (
                "INDETERMINATE" if decision.disposition == "VALID" else decision.disposition
            )
            return PlanningOutcome(disposition, None, decision.reason)
        reference = json.loads(decision.reference_bytes)
        if reference["tenant_id"] != self._tenant_id or reference["principal_id"] != principal_id:
            return PlanningOutcome(
                "DENIED", None, "caller identity does not match authenticated trust"
            )
        trust_bytes = _canonical(
            {
                key: reference[key]
                for key in (
                    "contour",
                    "credential_head",
                    "freshness_sequence",
                    "materialization_head",
                    "peer_credential",
                    "session_head",
                    "source_head",
                    "trust_head",
                )
            }
        )
        request = self._planning_request(command, principal_id, trust_bytes)
        runtime = self._supervisor.runtime()
        callee = runtime.session("planning")
        caller = BrokerSession(
            tenant_id=self._tenant_id,
            broker_epoch=callee.broker_epoch,
            generation_id=callee.generation_id,
            owner_id="broker",
            session_id=f"broker:{callee.generation_id}",
        )
        response = await runtime.call(
            PublicPortCall(
                operation_id=self._planning_operation,
                request_id=f"planning:{command.command_id.value}:{secrets.token_hex(8)}",
                caller=caller,
                callee=callee,
                schema_id=self._planning_schema,
                canonical_payload=request.canonical_bytes(),
                budget=CallBudget(
                    remaining_calls=1,
                    remaining_depth=1,
                    absolute_deadline_ns=time.monotonic_ns() + 5_000_000_000,
                    policy_version=1,
                ),
            )
        )
        if isinstance(response, PublicPortRejected):
            return PlanningOutcome("INDETERMINATE", None, response.failure.reason)
        owner_result = _decode_owner_result(response.canonical_payload)
        if owner_result.disposition != "COMMITTED":
            result = None
            if owner_result.disposition == "REPLAY" and owner_result.canonical_result_bytes:
                result = _committed_result(
                    json.loads(owner_result.canonical_result_bytes)["result"],
                    TenantId(self._tenant_id),
                )
            return PlanningOutcome(owner_result.disposition, result, owner_result.reason)
        assert owner_result.canonical_result_bytes is not None
        proposal = json.loads(owner_result.canonical_result_bytes)
        _committed_result(proposal["result"], TenantId(self._tenant_id))
        return PreparedPlanningCandidate(
            command_bytes=_canonical(
                {
                    "command_id": command.command_id.value,
                    "intention_line_id": command.intention_line_id.value,
                    "revision_id": command.revision_id.value,
                    "purpose": command.purpose,
                    "authority_act_id": command.authority_act_id,
                }
            ),
            request_bytes=request.canonical_bytes(),
            trust_reference_bytes=decision.reference_bytes,
            owner_result_bytes=owner_result.canonical_result_bytes,
            owner_session=callee,
            expected_tenant_head=int(proposal["commit_sequence"]) - 1,
        )

    async def _create(
        self,
        *,
        principal_id: str,
        credential_id: str,
        session_id: str,
        command: CreateIntentionLine,
    ) -> PlanningOutcome:
        prepared = await self._prepare_create(
            principal_id=principal_id,
            credential_id=credential_id,
            session_id=session_id,
            command=command,
        )
        if isinstance(prepared, PlanningOutcome):
            return prepared
        reference = json.loads(prepared.trust_reference_bytes)
        callee = prepared.owner_session
        proposal = json.loads(prepared.owner_result_bytes)
        result = _committed_result(proposal["result"], TenantId(self._tenant_id))
        payload_fingerprint = hashlib.sha256(prepared.owner_result_bytes).hexdigest()
        token = OperationToken(
            mode="IDEMPOTENT_EXACT",
            tenant_id=self._tenant_id,
            broker_epoch=callee.broker_epoch,
            owner_id=callee.owner_id,
            generation_id=callee.generation_id,
            session_id=callee.session_id,
            target_id="event_appender",
            capability_id="event_appender",
            operation_kind=_OPERATION,
            payload_fingerprint=payload_fingerprint,
            nonce=hashlib.sha256(
                f"{self._tenant_id}:{command.command_id.value}".encode()
            ).hexdigest(),
            expiry_ns=time.monotonic_ns() + 5_000_000_000,
        )
        operation_id = f"planning:{command.command_id.value}"
        slot_id = f"planning-result:{command.command_id.value}"
        admitted, novel = self._supervisor.authority_ledger.admit_with_novelty(
            operation_id, slot_id, token, time.monotonic_ns()
        )
        if not novel:
            return self._recorded_outcome(admitted)

        def guard() -> Literal["DENIED", "STALE", "INDETERMINATE"] | None:
            current = self._trust_call_sync(
                {
                    "operation": "CREATE_INTENTION_LINE",
                    "reference": reference,
                    "subject_id": result.intention_line_id.value,
                }
            )
            if current.disposition != "VALID":
                return current.disposition
            return self._publication_authority_guard(current.reference_bytes)

        try:
            publication = await self._appender.submit(
                PhysicalPublicationCommand(
                    tenant_id=self._tenant_id,
                    operation_kind=_OPERATION,
                    idempotency_key=command.command_id.value,
                    request_fingerprint=str(proposal["request_fingerprint"]),
                    expected_head=int(proposal["commit_sequence"]) - 1,
                    fence_generation=_FENCE,
                    expected_fence_frontier=0,
                    minimum_fence_frontier=0,
                    records=tuple(
                        PhysicalRecord(
                            str(item["record_id"]),
                            "planning",
                            _SCHEMA,
                            base64.b64decode(item["canonical_bytes"]),
                            hashlib.sha256(base64.b64decode(item["canonical_bytes"])).hexdigest(),
                        )
                        for item in proposal["records"]
                    ),
                    admission_guard=guard,
                    decision_guard=lambda commitment: self._publication_decision_guard(
                        prepared.owner_result_bytes, commitment
                    ),
                )
            )
        except BaseException:
            self._supervisor.authority_ledger.finish(
                admitted.model_copy(
                    update={
                        "state": "OUTCOME_UNKNOWN",
                        "recovery_obligation_id": f"writer-failure:{operation_id}",
                    }
                )
            )
            raise
        outcome = PlanningOutcome(
            publication.disposition,
            result if publication.disposition == "COMMITTED" else None,
            None if publication.disposition == "COMMITTED" else "broker publication rejected",
        )
        if publication.disposition == "COMMITTED":
            read_state = self._read_ledger.current_state(self._tenant_id)
            commitment, observation = capture_authority_storage_state(self._database)
            self._commitment_journal.commit(self._tenant_id, commitment)
            self._read_ledger.invalidate_storage_mutation(
                self._tenant_id,
                read_state.fingerprint(),
                commitment,
                observation,
            )
        outcome_bytes = _canonical(asdict(outcome))
        self._supervisor.authority_ledger.finish(
            admitted.model_copy(
                update={
                    "state": "DEFINITE",
                    "result_bytes": outcome_bytes,
                    "result_digest": hashlib.sha256(outcome_bytes).hexdigest(),
                }
            )
        )
        return outcome

    def _recorded_outcome(self, disposition: OperationDisposition) -> PlanningOutcome:
        if disposition.state != "DEFINITE" or disposition.result_bytes is None:
            return PlanningOutcome(
                "INDETERMINATE", None, "raw operation outcome awaits reconciliation"
            )
        value = json.loads(disposition.result_bytes)
        result = None
        if value["result"] is not None:
            result = _committed_result(value["result"], TenantId(self._tenant_id))
        return PlanningOutcome(value["disposition"], result, value["reason"])

    def _planning_request(
        self, command: CreateIntentionLine, principal_id: str, trust_bytes: bytes
    ) -> R7PlanningCreateDTO | R8PlanningRequest:
        return R7PlanningCreateDTO(
            tenant_id=self._tenant_id,
            principal_id=principal_id,
            command_id=command.command_id.value,
            intention_line_id=command.intention_line_id.value,
            revision_id=command.revision_id.value,
            purpose=command.purpose,
            authority_act_id=command.authority_act_id,
            trust_reference_bytes=trust_bytes,
            planning_snapshot_bytes=self._snapshot(),
        )

    def _publication_authority_guard(
        self, current_reference: bytes | None
    ) -> Literal["DENIED", "STALE", "INDETERMINATE"] | None:
        return None

    def _publication_decision_guard(
        self, proposal_bytes: bytes | None, resulting_commitment: str
    ) -> Literal["DENIED", "STALE", "INDETERMINATE"] | None:
        return None

    async def _prepare_startup(self) -> None:
        pass

    def close(self) -> None:
        self._supervisor.close()


@asynccontextmanager
async def open_r7_runtime(
    database: Path, *, tenant_id: str, operator_secret: bytes
) -> AsyncIterator[R7PlanningRuntime]:
    async with _open_runtime(
        database,
        tenant_id=tenant_id,
        operator_secret=operator_secret,
        runtime_type=R7PlanningRuntime,
        manifest=R7_PRODUCTION_MANIFEST,
    ) as runtime:
        yield runtime


@asynccontextmanager
async def _open_runtime(
    database: Path,
    *,
    tenant_id: str,
    operator_secret: bytes,
    runtime_type: type[R7PlanningRuntime],
    manifest: RuntimeAssemblyManifest,
    extra_leaves: Mapping[str, object] | None = None,
) -> AsyncIterator[R7PlanningRuntime]:
    journal = IndependentTenantDecisionJournal(
        database.with_suffix(database.suffix + ".trust-journal")
    )
    trust_store = SQLiteTrustMaterializer(database.with_suffix(database.suffix + ".trust.sqlite3"))
    trust = BrokerTrustDurability(journal, trust_store, operator_secret)
    planning_store = ProductionPlanningStore(database)
    supervisor = R7RuntimeSupervisor(
        tenant_id,
        database.with_suffix(database.suffix + ".broker.sqlite3"),
        manifest,
        R4RuntimeAdmission(trust, journal),
        {"clock": ProductionClock(), "planning_store": planning_store, **(extra_leaves or {})},
    )
    with SQLiteMaterializer(
        database,
        record_contracts=runtime_type._record_contracts,
        record_schema_variants=runtime_type._record_schema_variants,
        derivative_contracts=runtime_type._derivative_contracts,
        managed_derivative_sinks=runtime_type._derivative_contracts,
    ) as store:
        async with EventAppender(store, capacity=4) as appender:
            read_ledger = BrokerReadLedger(database.with_suffix(database.suffix + ".reads.sqlite3"))
            commitment_journal = AuthorityCommitmentJournal(database, operator_secret)
            runtime = runtime_type(
                tenant_id,
                trust,
                journal,
                planning_store.path or database,
                read_ledger,
                commitment_journal,
                appender,
                supervisor,
            )
            runtime._bind_appender()
            try:
                trust_state = trust.verify()
                if trust_state is not None and trust_state.phase == "ACTIVE":
                    await appender.advance_fence(
                        FenceAdvanceCommand(tenant_id, _FENCE, 0, allow_exact_replay=True)
                    )
                    await runtime._prepare_startup()
                    runtime.restart_generation()
                yield runtime
            finally:
                runtime.close()
    trust_store.close()


__all__ = ["R7PlanningRuntime", "open_r7_runtime"]
