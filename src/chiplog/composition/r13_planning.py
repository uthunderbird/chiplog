"""Exact displayed adoption bridge to the R8 recorder, isolated owner and committer."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import time
from dataclasses import asdict
from typing import Literal

from chiplog.adapters.driven.loop_sqlite import SQLiteLoopStore
from chiplog.capabilities.agent_loop.contracts import (
    LocalPlanningReceipt,
    LoopRejected,
    LoopSnapshot,
    ProposalDisplay,
    RunRecord,
)
from chiplog.capabilities.planning import CreateIntentionLine
from chiplog.capabilities.planning._r8_authority import (
    PROPOSAL_REGISTRY_INPUTS,
    decode_freshness,
    decode_trace,
    freshness_bytes,
    proposed_create_result,
    trace_bytes,
    validate_proposal_freshness,
)
from chiplog.capabilities.planning.r8_boundary import (
    AuthorityRead,
    ProposalFreshnessBinding,
    R8PlanningRequest,
)
from chiplog.composition.r8 import command_bytes
from chiplog.composition.r13_runtime import R13Runtime
from chiplog.domain_primitives import RecordId, TenantId


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _trust_payload(reference: dict[str, object]) -> bytes:
    return _canonical(
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


class R13PlanningRuntime(R13Runtime):
    _adoption: ProposalDisplay | None = None
    _authenticated_adoption: tuple[str, str, str] | None = None

    def _displays(self) -> tuple[ProposalDisplay, ...]:
        displays = []
        for _, _, raw in self._loop_decisions().entries():
            entry = json.loads(raw)
            if entry.get("kind") == "DISPLAY":
                display = ProposalDisplay.model_validate_json(entry["display"])
                if entry["operation_id"] != display.display_id or display.tenant != self._tenant_id:
                    raise LoopRejected("immutable display physical identity mismatch")
                displays.append(display)
        return tuple(displays)

    def _loop_snapshot(self) -> LoopSnapshot:
        return SQLiteLoopStore(self._database, self._appender, self._tenant_id, "r6").snapshot()

    def _proposal(self, proposal_id: str) -> tuple[RunRecord, str]:
        rows = self._loop_snapshot().records
        latest = {row.run_id: row for row in rows}
        matches = [
            (run, outcome.call.text)
            for run in latest.values()
            for turn in run.turns
            for outcome in (turn.sealed_calls or ())
            if outcome.proposal_id == proposal_id
            and outcome.call.tool == "propose_planning"
            and outcome.state == "TERMINAL"
        ]
        if len(matches) != 1:
            raise LoopRejected("absent or ambiguous immutable planning interpretation")
        return matches[0]

    def _adoption_trace(
        self, principal: str, trust: bytes, deadline: int, binding: ProposalFreshnessBinding
    ) -> bytes:
        trace = decode_trace(super()._trace(principal, trust, deadline))
        registry = trace.reads[2].model_copy(
            update={
                "head": "adopted-proposal-create-v1",
                "canonical_value": _canonical(PROPOSAL_REGISTRY_INPUTS),
            }
        )
        adoption = AuthorityRead(
            kind="ADOPTION",
            source_id="planning.exact-adoption",
            source_version="1",
            head=binding.proposal_id,
            generation=trace.reads[0].generation,
            frontier=trace.reads[0].frontier,
            valid_until_ns=deadline,
            canonical_value=_canonical(binding.model_dump(exclude={"trace"})),
        )
        return trace_bytes(
            trace.model_copy(
                update={
                    "reads": (*trace.reads[:2], registry, adoption),
                    "registry_inputs": PROPOSAL_REGISTRY_INPUTS,
                }
            )
        )

    async def display(self, proposal_id: str) -> ProposalDisplay:
        run, purpose = self._proposal(proposal_id)
        existing = [display for display in self._displays() if display.proposal_id == proposal_id]
        revision = len(existing) + 1
        display_id = f"{proposal_id}/display/{revision}"
        act = display_id + "/adoption"
        subject = "intention:" + hashlib.sha256(proposal_id.encode()).hexdigest()
        command = {
            "command_id": display_id + "/command",
            "intention_line_id": subject,
            "revision_id": display_id + "/revision",
            "purpose": purpose,
            "authority_act_id": act,
        }
        authenticated = await self._trust_call(
            "AUTHENTICATE",
            {
                "contour": "CLI",
                "credential_id": "hermetic-credential",
                "peer_credential": f"uid:{os.getuid()}",
                "session_id": "hermetic-session",
            },
        )
        if authenticated.disposition != "VALID" or authenticated.reference_bytes is None:
            raise LoopRejected("display reader is not authenticated")
        reference = json.loads(authenticated.reference_bytes)
        if reference["principal_id"] != run.principal:
            raise LoopRejected("foreign display principal")
        trust = _trust_payload(reference)
        deadline = time.monotonic_ns() + 300_000_000_000
        original = decode_trace(super()._trace(run.principal, trust, deadline))
        original = original.model_copy(
            update={
                "registry_inputs": PROPOSAL_REGISTRY_INPUTS,
                "reads": (
                    *original.reads[:2],
                    original.reads[2].model_copy(
                        update={
                            "head": "adopted-proposal-create-v1",
                            "canonical_value": _canonical(PROPOSAL_REGISTRY_INPUTS),
                        }
                    ),
                ),
            }
        )
        rendered = _canonical(
            {
                "operation": "CREATE_INTENTION_LINE",
                "principal": run.principal,
                "command": command,
                "result": json.loads(proposed_create_result(command)),
                "consequence": "Create one local planning intention; no provider effect.",
                "authority_sources": [
                    read.model_dump(mode="json", exclude={"canonical_value"})
                    for read in original.reads
                ],
            }
        ).decode()
        binding = ProposalFreshnessBinding(
            proposal_id=proposal_id,
            display_digest=hashlib.sha256(rendered.encode()).hexdigest(),
            principal_id=run.principal,
            adoption_act_id=act,
            ingress_id=display_id + "/expected-ingress",
            interpretation_revision=proposal_id,
            command_digest=hashlib.sha256(_canonical(command)).hexdigest(),
            proposed_result_digest=hashlib.sha256(proposed_create_result(command)).hexdigest(),
            trace=original,
        )
        binding = binding.model_copy(
            update={
                "trace": decode_trace(self._adoption_trace(run.principal, trust, deadline, binding))
            }
        )
        validate_proposal_freshness(
            binding, binding, display_bytes=rendered.encode(), now_ns=time.monotonic_ns()
        )
        display = ProposalDisplay(
            display_id=display_id,
            proposal_id=proposal_id,
            revision=revision,
            run_id=run.run_id,
            tenant=run.tenant,
            principal=run.principal,
            adoption_act_id=act,
            canonical_command=_canonical(command).decode(),
            display_text=rendered,
            display_digest=binding.display_digest,
            original_binding_base64=base64.b64encode(freshness_bytes(binding)).decode(),
        )
        # Independent immutable display storage is deliberately not an append to
        # the canonical event frontier against which its full trace was recorded.
        self._append_decision(
            {
                "version": 1,
                "kind": "DISPLAY",
                "operation_id": display_id,
                "display": display.model_dump_json(),
            }
        )
        return display

    def _trace(self, principal_id: str, trust_bytes: bytes, deadline: int) -> bytes:
        if self._adoption is None:
            return super()._trace(principal_id, trust_bytes, deadline)
        binding = decode_freshness(
            base64.b64decode(self._adoption.original_binding_base64, validate=True)
        )
        command = json.loads(self._adoption.canonical_command)
        replay = any(
            row["command_id"] == command["command_id"]
            for row in json.loads(self._snapshot())["commands"]
        )
        return self._adoption_trace(
            principal_id,
            trust_bytes,
            deadline if replay else binding.trace.reads[0].valid_until_ns,
            binding,
        )

    def _planning_request(
        self, command: CreateIntentionLine, principal_id: str, trust_bytes: bytes
    ) -> R8PlanningRequest:
        request = super()._planning_request(command, principal_id, trust_bytes)
        if self._adoption is not None:
            request = request.model_copy(
                update={
                    "proposal_binding_bytes": base64.b64decode(
                        self._adoption.original_binding_base64, validate=True
                    ),
                    "display_bytes": self._adoption.display_text.encode(),
                }
            )
            self._traced_request = request
        return request

    def _publication_authority_guard(
        self, current_reference: bytes | None
    ) -> Literal["DENIED", "STALE", "INDETERMINATE"] | None:
        if self._adoption is None:
            return super()._publication_authority_guard(current_reference)
        display = self._adoption
        if current_reference is None or self._authenticated_adoption != (
            "hermetic-ingress",
            display.display_digest,
            display.adoption_act_id,
        ):
            return "DENIED"
        binding = decode_freshness(base64.b64decode(display.original_binding_base64, validate=True))
        if time.monotonic_ns() >= binding.trace.reads[0].valid_until_ns:
            return "STALE"
        reproduced = self._trace(
            display.principal, _trust_payload(json.loads(current_reference)), 0
        )
        return None if reproduced == trace_bytes(binding.trace) else "STALE"

    def _publication_decision_guard(
        self, proposal_bytes: bytes | None, resulting_commitment: str
    ) -> Literal["DENIED", "STALE", "INDETERMINATE"] | None:
        if self._adoption is None:
            return super()._publication_decision_guard(proposal_bytes, resulting_commitment)
        if proposal_bytes is None or self._pending():
            return "DENIED"
        proposal = json.loads(proposal_bytes)
        command = json.loads(self._adoption.canonical_command)
        self._append_decision(
            {
                "version": 1,
                "kind": "DECIDED",
                "operation_id": command["command_id"],
                "operation_kind": "planning.create_intention_line",
                "expected_head": proposal["commit_sequence"] - 1,
                "fingerprint": proposal["request_fingerprint"],
                "predecessor": self._commitment_journal.load(self._tenant_id),
                "resulting": resulting_commitment,
                "records": [
                    {
                        "record_id": row["record_id"],
                        "owner": "planning",
                        "schema": "chiplog.planning.record.v1",
                        "payload": row["canonical_bytes"],
                        "digest": hashlib.sha256(
                            base64.b64decode(row["canonical_bytes"], validate=True)
                        ).hexdigest(),
                    }
                    for row in proposal["records"]
                ],
            }
        )
        return None

    def _validate_receipt(self, receipt: LocalPlanningReceipt) -> None:
        displays = [
            display for display in self._displays() if display.display_id == receipt.display_id
        ]
        if len(displays) != 1:
            raise LoopRejected("receipt has no exact independently stored display")
        display = displays[0]
        command = json.loads(display.canonical_command)
        expected = LocalPlanningReceipt(
            evidence_id=command["command_id"],
            command_id=command["command_id"],
            proposal_id=display.proposal_id,
            display_id=display.display_id,
            revision_id=command["revision_id"],
            purpose=command["purpose"],
            canonical_result=receipt.canonical_result,
            result_digest=hashlib.sha256(receipt.canonical_result.encode()).hexdigest(),
        )
        if receipt != expected:
            raise LoopRejected("receipt fields differ from exact displayed committed command")

    async def adopt(
        self, peer: str, display_id: str, digest: str, adoption_act_id: str
    ) -> LocalPlanningReceipt:
        displays = [display for display in self._displays() if display.display_id == display_id]
        if len(displays) != 1:
            raise LoopRejected("missing exact immutable display")
        display = displays[0]
        if (
            peer != "hermetic-ingress"
            or digest != display.display_digest
            or adoption_act_id != display.adoption_act_id
        ):
            raise LoopRejected("missing authenticated exact adoption ingress")
        run, purpose = self._proposal(display.proposal_id)
        if run.state != "ACTIVE" and not any(
            receipt.display_id == display_id for receipt in run.planning_receipts
        ):
            raise LoopRejected("terminal Run cannot admit a new planning adoption")
        command = json.loads(display.canonical_command)
        if purpose != command["purpose"] or run.principal != display.principal:
            raise LoopRejected("display substituted the immutable interpretation")
        tenant = TenantId(self._tenant_id)
        value = CreateIntentionLine(
            RecordId(tenant, command["command_id"]),
            RecordId(tenant, command["intention_line_id"]),
            RecordId(tenant, command["revision_id"]),
            command["purpose"],
            command["authority_act_id"],
        )
        if command_bytes(value) != display.canonical_command.encode():
            raise LoopRejected("noncanonical displayed command")
        async with self._planning_lane:
            self._adoption = display
            self._authenticated_adoption = (peer, digest, adoption_act_id)
            try:
                outcome = await self._create(
                    principal_id=display.principal,
                    credential_id="hermetic-credential",
                    session_id="hermetic-session",
                    command=value,
                )
                if outcome.disposition not in ("COMMITTED", "REPLAY") or outcome.result is None:
                    raise LoopRejected(
                        "adoption requires redisplay and new act: " + outcome.disposition
                    )
                if outcome.disposition == "COMMITTED":
                    self._finish_decision(command["command_id"])
                result = _canonical(asdict(outcome.result)).decode()
                return LocalPlanningReceipt(
                    evidence_id=command["command_id"],
                    proposal_id=display.proposal_id,
                    display_id=display.display_id,
                    command_id=command["command_id"],
                    revision_id=command["revision_id"],
                    purpose=purpose,
                    canonical_result=result,
                    result_digest=hashlib.sha256(result.encode()).hexdigest(),
                )
            finally:
                self._adoption = None
                self._authenticated_adoption = None
