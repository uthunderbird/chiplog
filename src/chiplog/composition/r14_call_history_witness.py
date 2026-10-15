"""Private per-adoption proof of unchanged, previously verified history inputs."""

from __future__ import annotations

import hashlib
import json
from contextlib import closing
from dataclasses import dataclass
from typing import TYPE_CHECKING

from chiplog.adapters.driven.deployment_trust import IndependentTenantDecisionJournal
from chiplog.capabilities.agent_loop.contracts import LoopRejected
from chiplog.platform.authority_gate import FileIdentity

from .r16_dispatch_inputs import DispatchCapture
from .r16_dispatch_registry import HermeticDispatchResources

if TYPE_CHECKING:
    from .r16_dispatch_inputs import ExecutionCallHistory
    from .r16_dispatch_runtime import ExecutionDispatchRuntime


# This slice covers the executed native V5 prefix, not every history family.
_SCHEMAS = {
    "agent_loop.native-one-call-recovery-profile.v5": {
        "chiplog.execution.native-one-call-recovery-profile.v5",
    },
    "agent_loop.inbox-initialization.v1": {"chiplog.agent-loop.execution-record.v2"},
    "agent_loop.execution-transition.v2": {"chiplog.agent-loop.execution-record.v2"},
    "workspace.policy": {"chiplog.workspace.policy.v1"},
    "agent_loop.execution-fanout.v2": {
        "chiplog.agent-loop.execution-record.v2",
        "chiplog.call.initialized.v1",
        "chiplog.call.response-seal.v1",
    },
    "agent_loop.accepted-direct-terminal.v1": {
        "chiplog.execution.accepted-direct-terminal-accounting.v1",
        "chiplog.execution.accepted-direct-terminal-disposition.v1",
        "chiplog.execution.accepted-direct-terminal-result.v1",
    },
    "agent_loop.native-sealed-accounting.v3": {"chiplog.agent-loop.native-sealed-accounting.v3"},
    "agent_loop.execution-accounting-readiness.v1": {
        "chiplog.execution.continuation-ready.v1",
        "chiplog.execution.sealed-accounting.v1",
    },
    "agent_loop.execution-next-turn.v1": {"chiplog.agent-loop.execution-record.v2"},
}
_OWNER_OPERATIONS = frozenset(
    {
        "ingress.allocate_receipt_token",
        "ingress.stage_raw_bytes",
        "ingress.publish_custody_successor",
        "effects.accept_call",
        "effects.authorize",
        "effects.commit_send",
        "effects.record_evidence",
        "dispatch.consume_effect_send",
        "effects.reconcile",
    }
)
RawEntries = tuple[tuple[str, str | None, bytes], ...]
JournalInputs = tuple[tuple[FileIdentity, FileIdentity, FileIdentity], RawEntries]


def _standard_configuration(runtime):
    from .common_cli_execution_runtime import CommonCliExecutionRuntime
    from .r14_native_recovery_cut import (
        _NATIVE_AUTHENTICATE_SELECTED_HELD,
        NativeRecoveryCutSource,
    )
    from .r16_dispatch_registry import _HISTORICAL_DISPATCH_METHODS

    if type(runtime) is not CommonCliExecutionRuntime:
        return None
    source = runtime._full_recovery_cut_source
    if (
        type(source) is not NativeRecoveryCutSource
        or vars(source) != {"_profile_reader": None}
        or getattr(source.authenticate_selected_held, "__func__", None)
        is not _NATIVE_AUTHENTICATE_SELECTED_HELD
        or getattr(source.authenticate_selected_held, "__self__", None) is not source
    ):
        return None
    resources = runtime._require_dispatch_resources()
    if type(resources) is not HermeticDispatchResources:
        return None
    for name, original in _HISTORICAL_DISPATCH_METHODS:
        method = getattr(resources, name)
        if (
            getattr(method, "__func__", None) is not original
            or getattr(method, "__self__", None) is not resources
        ):
            return None
    custody = resources._historical_custody()
    # Keep issuer key material out of witness values and their diagnostics.
    fingerprint = hashlib.sha256(
        custody.issuer_key
        + json.dumps(
            [
                custody.credential_id,
                custody.grant_id,
                custody.cap,
            ],
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    return source, resources, fingerprint, tuple(runtime._record_schema_variants)


def _supported_prefix(entries: RawEntries) -> bool:
    native = False
    for _, _, raw in entries:
        entry = json.loads(raw)
        if entry.get("kind") == "MATERIALIZED":
            continue
        if entry.get("kind") != "DECIDED":
            return False
        operation = entry.get("operation_kind")
        schemas = _SCHEMAS.get(operation)
        if schemas is None or {row["schema"] for row in entry["records"]} != schemas:
            return False
        if operation == "agent_loop.native-one-call-recovery-profile.v5":
            native = True
        if operation == "agent_loop.execution-transition.v2":
            transition = json.loads(entry["execution_transition"])
            if transition.get("kind") not in {
                "R14_SELECTED_EXECUTION_TRANSITION_V2",
                "R14_SELECTED_EXECUTION_TRANSITION_V3",
                "R14_SELECTED_EXECUTION_TRANSITION_V4",
            }:
                return False
    return native


def _journal_inputs(journal: IndependentTenantDecisionJournal) -> JournalInputs:
    return journal.physical_sources(), journal.entries()


@dataclass(frozen=True, repr=False)
class CallHistoryInputs:
    runtime: ExecutionDispatchRuntime
    configuration: tuple
    loop: JournalInputs
    owner: JournalInputs
    preview: JournalInputs
    h1_workspace: JournalInputs
    dashboard: JournalInputs


def capture_call_history_inputs(runtime: ExecutionDispatchRuntime) -> CallHistoryInputs | None:
    """Read exact authenticated inputs; opening auxiliary streams cannot provision them."""
    runtime._authority_gate().require_held()
    runtime._check_database_identity()
    configuration = _standard_configuration(runtime)
    if configuration is None:
        return None
    loop = _journal_inputs(runtime._loop_decisions())
    if not _supported_prefix(loop[1]):
        return None
    owners = runtime._owner_decisions()
    if any(
        row.prepared.request.operation not in _OWNER_OPERATIONS
        for row in owners.snapshot().decisions
    ):
        return None
    owner = _journal_inputs(owners._raw)
    preview = _journal_inputs(runtime._require_call_preview_journal())
    extras = []
    for suffix in (".h1-workspace-issuance", ".workspace-issuance"):
        with closing(
            IndependentTenantDecisionJournal.for_existing_authority_bundle(
                runtime._database.with_suffix(suffix),
                authority_gate=runtime._authority_gate(),
            )
        ) as journal:
            extras.append(_journal_inputs(journal))
    return CallHistoryInputs(runtime, configuration, loop, owner, preview, *extras)


@dataclass(frozen=True, repr=False)
class CallHistoryWitness:
    inputs: CallHistoryInputs
    history: ExecutionCallHistory
    captured: DispatchCapture

    def recheck(
        self, runtime: ExecutionDispatchRuntime, captured: DispatchCapture
    ) -> ExecutionCallHistory:
        runtime._authority_gate().require_held()
        if (
            runtime is not self.inputs.runtime
            or captured != self.captured
            or capture_call_history_inputs(runtime) != self.inputs
        ):
            raise LoopRejected("call acceptance verified history inputs changed after owner IPC")
        return self.history
