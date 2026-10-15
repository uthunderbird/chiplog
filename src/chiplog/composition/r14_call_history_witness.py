"""Private per-adoption proof of unchanged, previously verified history inputs."""

from __future__ import annotations

import hashlib
import json
from contextlib import closing
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from chiplog.adapters.driven.deployment_trust import IndependentTenantDecisionJournal
from chiplog.capabilities.agent_loop.contracts import LoopRejected
from chiplog.platform.authority_gate import FileIdentity
from chiplog.platform.authority_reads import (
    AuthoritySnapshotIntegrityError,
    capture_authority_snapshot_commitment,
)
from chiplog.platform.workspace_snapshot import workspace_snapshot

from .r16_dispatch_inputs import DispatchCapture
from .r16_dispatch_registry import HermeticDispatchResources

if TYPE_CHECKING:
    from .r16_dispatch_inputs import ExecutionCallHistory
    from .r16_dispatch_runtime import ExecutionDispatchRuntime


# These exact families are validated by the complete call-history reader before
# a cache entry is retained.  This remains a closed schema allowlist, not a
# general cache admission path.
_SCHEMAS = {
    "agent_loop.native-one-call-recovery-profile.v3": {
        "chiplog.execution.native-one-call-recovery-profile.v3",
    },
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
    "agent_loop.cancel-execution-before-accept.v1": {
        "chiplog.call.cancelled-before-accept.v1",
        "chiplog.call.not-executed-result.v1",
    },
    "agent_loop.native-one-call-cancelled-accounting.v1": {
        "chiplog.agent-loop.native-one-call-cancelled-accounting.v1",
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
    "agent_loop.accepted-open-recovery.v1": {
        "chiplog.execution.accepted-open-accounting.v1",
        "chiplog.execution.accepted-open-recovery-opening.v1",
        "chiplog.execution.accepted-open-terminal-disposition.v1",
    },
    "agent_loop.execution-suspension.v1": {
        "chiplog.execution.suspension-baseline.v2",
        "chiplog.agent-loop.execution-record.v2",
        "chiplog.execution.suspension-pair.v2",
    },
    "agent_loop.original-resolver-authority-policy.v1": {
        "chiplog.loop.original-resolver-authority-policy.v1",
        "chiplog.loop.original-effect-evidence-reducer-policy.v1",
    },
    "agent_loop.original-resolver-invocation.v1": {
        "chiplog.loop.original-resolver-invocation.v1",
    },
    "agent_loop.original-call-resolution.v1": {
        "chiplog.loop.original-resolution-basis.v1",
        "chiplog.loop.original-obligation-closure.v1",
        "chiplog.loop.recovered-call-outcome.v1",
        "chiplog.loop.original-resolver-batch.v1",
    },
    "agent_loop.loop-semantic-reduction.v1": {
        "chiplog.loop.semantic-reduction.v1",
    },
    "agent_loop.execution-resume.v1": {"chiplog.agent-loop.execution-record.v2"},
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
    source = getattr(runtime, "_full_recovery_cut_source", None)
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
        if operation in {
            "agent_loop.native-one-call-recovery-profile.v3",
            "agent_loop.native-one-call-recovery-profile.v5",
        }:
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
    database_identity: tuple[str, int, int]
    authority_commitment: str


def capture_call_history_inputs(runtime: ExecutionDispatchRuntime) -> CallHistoryInputs | None:
    """Read exact authenticated inputs; opening auxiliary streams cannot provision them."""
    runtime._authority_gate().require_held()
    runtime._check_database_identity()
    database_identity = runtime._database_identity
    try:
        with workspace_snapshot(runtime._database) as snapshot:
            authority_commitment = capture_authority_snapshot_commitment(
                snapshot.connection, runtime._tenant_id
            )
    except AuthoritySnapshotIntegrityError:
        return None
    if runtime._commitment_journal.load(runtime._tenant_id) != authority_commitment:
        return None
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
    return CallHistoryInputs(
        runtime,
        configuration,
        loop,
        owner,
        preview,
        *extras,
        database_identity,
        authority_commitment,
    )


def seed_call_history_cache(runtime) -> None:
    """Seed the one-entry runtime cache only across an unchanged exact input cut."""
    runtime._authority_gate().require_held()
    before = capture_call_history_inputs(runtime)
    from .r14_loop_history import read_execution_call_history

    verified = read_execution_call_history(runtime)
    if before is None:
        runtime._call_history_cache = None
        return
    after = capture_call_history_inputs(runtime)
    runtime._call_history_cache = (
        (after, verified) if after is not None and before == after else None
    )


def read_cached_execution_call_history(runtime):
    """Return prior fully verified history only for the exact current input cut."""
    runtime._authority_gate().require_held()
    entry = getattr(runtime, "_call_history_cache", None)
    if entry is None:
        return None
    runtime._require_no_pending()
    inputs, history = entry
    current = capture_call_history_inputs(runtime)
    if current is None or current != inputs:
        runtime._call_history_cache = None
        return None
    return history


def remember_verified_call_history(
    runtime,
    inputs: CallHistoryInputs | None,
    history,
    *,
    allow_preview_change: bool = False,
) -> bool:
    """Retain already-read history while every non-preview input remains exact."""
    runtime._authority_gate().require_held()
    if inputs is None:
        runtime._call_history_cache = None
        return False
    current = capture_call_history_inputs(runtime)
    if current is None:
        runtime._call_history_cache = None
        return False
    matches = (
        replace(current, preview=inputs.preview) == inputs
        if allow_preview_change
        else current == inputs
    )
    if not matches:
        runtime._call_history_cache = None
        return False
    runtime._call_history_cache = (current, history)
    return True


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
