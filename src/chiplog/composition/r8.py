"""Canonical R8 runtime: traced owner inputs and final-boundary default HOLD."""

from __future__ import annotations

import base64
import hashlib
import json
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal, cast

from chiplog.adapters.driven.deployment_trust import IndependentTenantDecisionJournal
from chiplog.architecture.r7_runtime import R8_PRODUCTION_MANIFEST
from chiplog.capabilities.planning import CreateIntentionLine, PlanningOutcome
from chiplog.capabilities.planning.r8_boundary import R8PlanningRequest
from chiplog.platform._sqlite import PhysicalPublicationCommand, PhysicalRecord
from chiplog.platform.authority_reads import capture_authority_storage_state
from chiplog.platform.deployment_gate import DeploymentGateRequest
from chiplog.platform.r8_gate import BrokerDeploymentGate, canonical_request
from chiplog.verification.r8_surface import verify_offline_import_boundary, verify_r8_surfaces

from .r7_planning import R7PlanningRuntime, _open_runtime

R8_SURFACES = (
    ("cli.render", "planning.read"),
    ("planning.create", "planning.create_intention_line"),
)


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def command_bytes(command: CreateIntentionLine) -> bytes:
    return _canonical(
        {
            "command_id": command.command_id.value,
            "intention_line_id": command.intention_line_id.value,
            "revision_id": command.revision_id.value,
            "purpose": command.purpose,
            "authority_act_id": command.authority_act_id,
        }
    )


class R8PlanningRuntime(R7PlanningRuntime):
    _planning_operation = "planning.r8_create_intention_line"
    _planning_schema = "chiplog.planning.public.create.v2"
    _gate: BrokerDeploymentGate | None = None
    _pending_gate: DeploymentGateRequest | None = None
    _traced_request: R8PlanningRequest | None = None
    _trace_principal: str = ""

    def _configure_gate(self) -> None:
        if self._gate is None:
            self._gate = BrokerDeploymentGate(
                self._database.with_suffix(self._database.suffix + ".gate.sqlite3"),
                tenant_id=self._tenant_id,
                surfaces=R8_SURFACES,
                journal=IndependentTenantDecisionJournal(
                    self._database.with_suffix(self._database.suffix + ".gate-journal")
                ),
                clock=time.time_ns,
            )

    async def _prepare_startup(self) -> None:
        self._configure_gate()
        await self._recover_pending()

    async def _recover_pending(self) -> None:
        if self._gate is None:
            raise RuntimeError("gate journal unavailable during recovery")
        for operation_id, execution in self._gate.pending():
            if not execution:
                continue  # An external/disclosure cut is never replayed as a planning write.
            decision = json.loads(execution)
            if (
                set(decision) != {"version", "predecessor", "resulting", "proposal"}
                or decision["version"] != 1
            ):
                raise RuntimeError("unknown gate materialization decision")
            proposal = json.loads(base64.b64decode(decision["proposal"], validate=True))
            actual, _ = capture_authority_storage_state(self._database)
            if actual == decision["predecessor"]:
                publication = await self._appender.submit(
                    PhysicalPublicationCommand(
                        tenant_id=self._tenant_id,
                        operation_kind="planning.create_intention_line",
                        idempotency_key=operation_id,
                        request_fingerprint=proposal["request_fingerprint"],
                        expected_head=proposal["commit_sequence"] - 1,
                        fence_generation="r6",
                        expected_fence_frontier=0,
                        minimum_fence_frontier=0,
                        records=tuple(
                            PhysicalRecord(
                                item["record_id"],
                                "planning",
                                "chiplog.planning.record.v1",
                                base64.b64decode(item["canonical_bytes"], validate=True),
                                hashlib.sha256(
                                    base64.b64decode(item["canonical_bytes"], validate=True)
                                ).hexdigest(),
                            )
                            for item in proposal["records"]
                        ),
                    )
                )
                if publication.disposition not in {"COMMITTED", "REPLAY"}:
                    raise RuntimeError(
                        "decided planning batch has an unresolved materialization obligation"
                    )
                actual, _ = capture_authority_storage_state(self._database)
            if actual != decision["resulting"]:
                raise RuntimeError(
                    "gate materialization does not match exact decided authority content"
                )
            self._commitment_journal.commit(self._tenant_id, actual)
            read_state = self._read_ledger.current_state(self._tenant_id)
            if read_state.materialization_commitment != actual:
                _, observation = capture_authority_storage_state(self._database)
                self._read_ledger.invalidate_storage_mutation(
                    self._tenant_id, read_state.fingerprint(), actual, observation
                )
            self._gate.materialized(operation_id)

    def _start_generation(self) -> None:
        root = Path(__file__).resolve().parents[3]
        verify_r8_surfaces(root)
        verify_offline_import_boundary(root)
        super()._start_generation()

    async def create(
        self,
        *,
        principal_id: str,
        credential_id: str,
        session_id: str,
        command: CreateIntentionLine,
        gate_request: DeploymentGateRequest | None = None,
    ) -> PlanningOutcome:
        # A decided batch must materialize before any later planning writer.
        async with self._planning_lane:
            await self._recover_pending()
            if any(
                record_id.tenant_id.value != self._tenant_id
                for record_id in (
                    command.command_id,
                    command.intention_line_id,
                    command.revision_id,
                )
            ):
                return PlanningOutcome("DENIED", None, "command record belongs to another tenant")
            if (
                self._gate is None
                or gate_request is None
                or gate_request.surface_id != "planning.create"
                or gate_request.operation_id != command.command_id.value
                or gate_request.payload_digest != hashlib.sha256(command_bytes(command)).hexdigest()
            ):
                return PlanningOutcome(
                    "DENIED", None, "HOLD: exact deployment entitlement required"
                )
            prior = self._gate.crossed(command.command_id.value)
            if prior is not None:
                if prior != (canonical_request(gate_request), command_bytes(command)):
                    return PlanningOutcome("CONFLICT", None, "decided command binding changed")
                # Inspect the existing disposition through current authentication
                # and recorded owner inputs. This never issues another handoff;
                # the absent pending permit also rejects an unexpected new write.
                self._pending_gate = None
                self._traced_request = None
                return await self._create(
                    principal_id=principal_id,
                    credential_id=credential_id,
                    session_id=session_id,
                    command=command,
                )
            if self._gate.check(gate_request).disposition == "HOLD":
                return PlanningOutcome(
                    "DENIED", None, "HOLD: exact deployment entitlement required"
                )
            self._pending_gate = gate_request
            self._traced_request = None
            outcome = await self._create(
                principal_id=principal_id,
                credential_id=credential_id,
                session_id=session_id,
                command=command,
            )
            if outcome.disposition == "COMMITTED":
                self._gate.materialized(command.command_id.value)
            return outcome

    def _trace(self, principal_id: str, trust_bytes: bytes, deadline: int) -> bytes:
        before = self._read_ledger.current_state(self._tenant_id)
        snapshot = self._snapshot()
        after = self._read_ledger.current_state(self._tenant_id)
        if before != after:
            raise ValueError("authority frontier changed while recording planning inputs")
        generation = self._supervisor.runtime().session("planning").generation_id
        trust = json.loads(trust_bytes)
        rows = (
            ("TRUST", "deployment_trust.current", str(trust["trust_head"]), trust_bytes),
            ("PLANNING", "planning.committed", str(json.loads(snapshot)["head"]), snapshot),
            (
                "REGISTRY",
                "planning.authority-registry",
                "direct-principal-create-v1",
                _canonical([["authority-row", "direct-principal-create-v1"]]),
            ),
        )
        return _canonical(
            {
                "tenant_id": self._tenant_id,
                "principal_id": principal_id,
                "registry_inputs": [["authority-row", "direct-principal-create-v1"]],
                "reads": [
                    {
                        "kind": kind,
                        "source_id": source,
                        "source_version": "1",
                        "head": head,
                        "generation": generation,
                        "frontier": before.fingerprint(),
                        "valid_until_ns": deadline,
                        "canonical_value": base64.b64encode(value).decode("ascii"),
                    }
                    for kind, source, head, value in rows
                ],
            }
        )

    def _planning_request(
        self, command: CreateIntentionLine, principal_id: str, trust_bytes: bytes
    ) -> R8PlanningRequest:
        now = time.monotonic_ns()
        request = R8PlanningRequest(
            command_bytes=command_bytes(command),
            authority_trace_bytes=self._trace(principal_id, trust_bytes, now + 5_000_000_000),
            observed_time_ns=now,
        )
        self._traced_request = request
        self._trace_principal = principal_id
        return request

    def _publication_authority_guard(
        self, current_reference: bytes | None
    ) -> Literal["DENIED", "STALE", "INDETERMINATE"] | None:
        original = self._traced_request
        if (
            original is None
            or current_reference is None
            or self._gate is None
            or self._pending_gate is None
        ):
            return "DENIED"
        if time.monotonic_ns() >= original.observed_time_ns + 5_000_000_000:
            return "STALE"
        reference = json.loads(current_reference)
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
        reproduced = self._trace(
            self._trace_principal, trust_bytes, original.observed_time_ns + 5_000_000_000
        )
        if reproduced != original.authority_trace_bytes:
            return "STALE"
        return None

    def _publication_decision_guard(
        self, proposal_bytes: bytes | None, resulting_commitment: str
    ) -> Literal["DENIED", "STALE", "INDETERMINATE"] | None:
        original = self._traced_request
        if (
            original is None
            or proposal_bytes is None
            or self._gate is None
            or self._pending_gate is None
        ):
            return "DENIED"
        # All semantic checks and SQL constraints have succeeded. The independent
        # journal now owns the full exact batch, before SQLite's fallible commit.
        execution = _canonical(
            {
                "version": 1,
                "predecessor": self._read_ledger.current_state(
                    self._tenant_id
                ).materialization_commitment,
                "resulting": resulting_commitment,
                "proposal": base64.b64encode(proposal_bytes).decode("ascii"),
            }
        )
        result = self._gate.commit_handoff(
            self._pending_gate, original.command_bytes, execution=execution
        )
        return "DENIED" if result.disposition == "HOLD" else None

    async def render(
        self,
        *,
        principal_id: str,
        credential_id: str,
        session_id: str,
        gate_request: DeploymentGateRequest | None = None,
    ) -> tuple[str, ...]:
        if self._gate is None or gate_request is None or gate_request.surface_id != "cli.render":
            raise PermissionError("HOLD: exact deployment entitlement required for disclosure")
        lines = await super().render(
            principal_id=principal_id, credential_id=credential_id, session_id=session_id
        )
        payload = _canonical(lines)
        if self._gate.commit_handoff(gate_request, payload).disposition == "HOLD":
            raise PermissionError("HOLD: disclosure eligibility changed")
        return lines


@asynccontextmanager
async def open_r8_runtime(
    database: Path,
    *,
    tenant_id: str,
    operator_secret: bytes,
) -> AsyncIterator[R8PlanningRuntime]:
    root = _source_root()
    verify_r8_surfaces(root)
    verify_offline_import_boundary(root)
    async with _open_runtime(
        database,
        tenant_id=tenant_id,
        operator_secret=operator_secret,
        runtime_type=R8PlanningRuntime,
        manifest=R8_PRODUCTION_MANIFEST,
    ) as runtime:
        result = cast(R8PlanningRuntime, runtime)
        # R8 consumes no independently provisioned entitlement adapter yet.
        # Missing authority holds every exposure; no local test/development mode.
        result._configure_gate()
        yield result


def _source_root() -> Path:
    return Path(__file__).resolve().parents[3]


__all__ = ["R8PlanningRuntime", "command_bytes", "open_r8_runtime"]
