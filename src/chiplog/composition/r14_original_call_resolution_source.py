"""Capture one exact selected R14 obligation and closed R16 witness."""

from __future__ import annotations

import json
import sqlite3
from typing import TYPE_CHECKING

from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
from chiplog.capabilities.agent_loop.contracts import LoopRejected
from chiplog.capabilities.agent_loop.execution_recovery_observations import RecoverySourceRecord
from chiplog.capabilities.agent_loop.original_recovery_contracts import (
    IndependentResolverAuthority,
    LoopRecoveryStream,
    OriginalResolverCut,
    PrepareOriginalCallResolution,
    original_effect_evidence_reducer_policy_v1,
    original_resolver_authority_policy_v1,
)
from chiplog.capabilities.agent_loop.recovery_contracts import Absent, Present
from chiplog.composition.r14_call_preview import execution_run_head
from chiplog.composition.r14_loop_history import read_execution_recovery_history
from chiplog.composition.r14_native_preparation_bindings import (
    bind_native_execution_preparation_v2,
)
from chiplog.composition.r14_native_selected_accounting_source import (
    R14NativeAcceptedOpenOutcome,
    R14NativeSelectedAccountingSource,
)
from chiplog.composition.r14_original_prepare_source import R14OriginalNativePrepareSource
from chiplog.platform.authority_reads import capture_authority_snapshot_commitment
from chiplog.platform.workspace_snapshot import read_connection

if TYPE_CHECKING:
    from chiplog.composition.common_cli_execution_runtime import CommonCliExecutionRuntime

_RESOLUTION_OPERATION = "agent_loop.original-call-resolution.v1"
_RESOLUTION_SCHEMAS = frozenset(
    {
        "chiplog.loop.original-resolution-basis.v1",
        "chiplog.loop.original-obligation-closure.v1",
        "chiplog.loop.recovered-call-outcome.v1",
        "chiplog.loop.original-resolver-batch.v1",
    }
)


def _require_resolution_absent(runtime: CommonCliExecutionRuntime) -> None:
    for _, _, raw in runtime._loop_decisions().entries():
        entry = json.loads(raw)
        if entry.get("kind") != "DECIDED":
            continue
        if entry.get("operation_kind") == _RESOLUTION_OPERATION:
            raise LoopRejected("selected original call resolution already exists")
        records = entry.get("records")
        if isinstance(records, list) and any(
            isinstance(row, dict) and row.get("schema") in _RESOLUTION_SCHEMAS for row in records
        ):
            raise LoopRejected("original resolution records exist without a selected resolution")

    placeholders = ",".join("?" for _ in _RESOLUTION_SCHEMAS)
    with read_connection(runtime._database) as connection:
        rows = tuple(
            connection.execute(
                "SELECT record_id FROM records WHERE tenant_id=? AND schema_id IN ("
                + placeholders
                + ") LIMIT 1",
                (runtime._tenant_id, *_RESOLUTION_SCHEMAS),
            ).fetchall()
        )
    if rows:
        raise LoopRejected("unselected original resolution physical records exist")


def _verify_selected_commitment_chain(
    base_sequence: int,
    base_commitment: str,
    end_sequence: int,
    end_commitment: str,
    steps: dict[int, tuple[str, str]],
) -> None:
    """Require one contiguous selected publication and commitment per tenant sequence."""
    expected = tuple(range(base_sequence + 1, end_sequence + 1))
    if tuple(sorted(steps)) != expected:
        raise LoopRejected("R14 evidence interval has a sequence gap or competing publication")
    previous = base_commitment
    for sequence in expected:
        predecessor, resulting = steps[sequence]
        if predecessor != previous:
            raise LoopRejected("R14 evidence interval commitment chain forks")
        previous = resulting
    if previous != end_commitment:
        raise LoopRejected("R14 evidence interval terminal commitment differs")


def _verify_same_obligation_append(previous, outcome) -> None:
    """Validate the R16 evidence/attempt link between adjacent selected outcomes."""
    from chiplog.capabilities.effects.dispatch_outcomes import evidence_head
    from chiplog.capabilities.effects.dispatch_v2 import reference

    evidence = outcome.command.evidence
    expected_intent = reference(
        previous.snapshot.intent.intent_id, previous.snapshot.intent.canonical_bytes()
    )
    if (
        outcome.command.operation != "APPEND_EVIDENCE"
        or outcome.kind != "EVIDENCE_RECORDED"
        or evidence is None
        or outcome.predecessor != previous.record
        or outcome.command.expected_attempt != previous.snapshot.attempt
        or outcome.command.intent != expected_intent
        or outcome.snapshot.intent != previous.snapshot.intent
        or outcome.snapshot.obligation != previous.snapshot.obligation
        or outcome.command.original_send != previous.command.original_send
        or outcome.command.complete_children != previous.command.complete_children
        or outcome.command.prior_evidence != previous.snapshot.evidence_heads
        or outcome.snapshot.evidence_heads
        != (*previous.snapshot.evidence_heads, evidence_head(evidence))
        or outcome.snapshot.evidence != (*previous.snapshot.evidence, evidence)
    ):
        raise LoopRejected("R14 evidence interval is not a same-obligation append")


def _selected_resolution_anchor_is_valid(
    actual: str,
    anchored: str,
    predecessor: str,
    resulting: str,
    *,
    materialized: bool,
) -> bool:
    """Accept either a current anchor or this exact pending selected publication."""
    return anchored == actual or (
        not materialized and actual == resulting and anchored == predecessor
    )


def _authenticate_append_evidence_suffix(
    runtime: CommonCliExecutionRuntime,
    *,
    base_sequence: int,
    base_commitment: str,
    end_sequence: int,
    end_commitment: str,
    base_witness: RecoverySourceRecord,
    owner_snapshot=None,
    loop_entries=None,
) -> None:
    """Authenticate every selected publication between an R14 cut and its successor.

    Same-intent R16 APPEND_EVIDENCE plus the registered R14 accounting/readiness
    and next-Turn publications may advance this interval. Owner and loop journals
    are stitched against the complete physical sequence under the authority gate.
    """
    from chiplog.capabilities.effects.dispatch_outcome_contracts import (
        DispatchOutcomeRecordV2,
    )
    from chiplog.composition.r14_loop_semantic_reduction_publication import (
        validate_selected_witness_at_cut,
    )
    from chiplog.platform.publication_readback import inspect_publication

    runtime._authority_gate().require_held()
    if (
        type(base_sequence) is not int
        or type(end_sequence) is not int
        or base_sequence < 0
        or end_sequence < base_sequence
        or not isinstance(base_commitment, str)
        or not isinstance(end_commitment, str)
    ):
        raise LoopRejected("R14 evidence interval has an invalid frontier")
    owner_journal = runtime._owner_decisions()
    owner_before = owner_snapshot or owner_journal.snapshot()
    if owner_before.tenant_id != runtime._tenant_id:
        raise LoopRejected("R14 evidence interval has a foreign owner journal")
    owner_ids = {
        decision.prepared.request.identity.command_id
        for decision in owner_before.decisions
    }
    if owner_ids != set(owner_before.materialized_command_ids):
        raise LoopRejected("R14 evidence interval has an unfinished owner publication")
    loop_journal = runtime._loop_decisions()
    loop_before = loop_entries if loop_entries is not None else loop_journal.entries()

    steps: dict[int, tuple[object, str, str, str, dict[str, object] | None]] = {}

    def add_step(
        sequence: int,
        batch,
        predecessor: str,
        resulting: str,
        family: str,
        entry: dict[str, object] | None = None,
    ) -> None:
        if sequence in steps:
            raise LoopRejected("R14 evidence interval has competing selected publications")
        if batch.expected_head + 1 != sequence or batch.tenant_id != runtime._tenant_id:
            raise LoopRejected("R14 evidence interval selected header differs from its sequence")
        steps[sequence] = (batch, predecessor, resulting, family, entry)

    for decision in owner_before.decisions:
        sequence = decision.tenant_commit_sequence
        if base_sequence < sequence <= end_sequence:
            batch = runtime._owner_command(decision)
            add_step(
                sequence,
                batch,
                decision.prepared.predecessor_commitment,
                decision.resulting_commitment,
                "owner",
            )

    for identity, _, raw in loop_before:
        entry = json.loads(raw)
        if entry.get("kind") != "DECIDED":
            continue
        expected_head = entry.get("expected_head")
        if type(expected_head) is not int:
            raise LoopRejected("R14 evidence interval loop decision lacks its sequence")
        sequence = expected_head + 1
        if base_sequence < sequence <= end_sequence:
            batch = runtime._publication(entry)
            if (
                entry.get("operation_id") != batch.idempotency_key
                or identity != batch.idempotency_key
            ):
                raise LoopRejected("R14 evidence interval loop identity differs")
            add_step(
                sequence,
                batch,
                str(entry.get("predecessor")),
                str(entry.get("resulting")),
                "loop",
                entry,
            )

    _verify_selected_commitment_chain(
        base_sequence,
        base_commitment,
        end_sequence,
        end_commitment,
        {sequence: (step[1], step[2]) for sequence, step in steps.items()},
    )
    previous_outcome = DispatchOutcomeRecordV2.model_validate_json(
        base_witness.canonical_record_bytes
    )
    if previous_outcome.canonical_bytes() != base_witness.canonical_record_bytes:
        raise LoopRejected("R14 evidence interval base witness is noncanonical")
    validate_selected_witness_at_cut(
        runtime, base_witness, base_sequence, snapshot=owner_before
    )

    with read_connection(runtime._database) as connection:
        runtime._check_database_identity()
        actual = capture_authority_snapshot_commitment(connection, runtime._tenant_id)
        anchored = runtime._commitment_journal.load(runtime._tenant_id)
        row = connection.execute(
            "SELECT head FROM tenant_heads WHERE tenant_id=?", (runtime._tenant_id,)
        ).fetchone()
        current_sequence = 0 if row is None else int(row[0])
        publications = tuple(
            connection.execute(
                "SELECT operation_kind,idempotency_key,request_fingerprint,commit_sequence,"
                "record_ids "
                "FROM publications WHERE tenant_id=? AND commit_sequence>? AND commit_sequence<=? "
                "ORDER BY commit_sequence",
                (runtime._tenant_id, base_sequence, end_sequence),
            ).fetchall()
        )
        expected_sequences = tuple(range(base_sequence + 1, end_sequence + 1))
        if (
            current_sequence < end_sequence
            or len(publications) != len(expected_sequences)
            or tuple(item[3] for item in publications) != expected_sequences
            or set(steps) != set(expected_sequences)
        ):
            raise LoopRejected("R14 evidence interval has a gap or incomplete physical cut")
        if actual != anchored:
            pending = runtime._pending()
            if len(pending) != 1:
                raise LoopRejected("R14 evidence interval has an unauthenticated current suffix")
            pending_entry = pending[0]
            pending_batch = runtime._publication(pending_entry)
            pending_sequence = pending_batch.expected_head + 1
            pending_follows_interval = (
                end_sequence + 1 == pending_sequence
                and pending_entry.get("predecessor") == end_commitment
            )
            pending_is_interval_base = (
                base_sequence == end_sequence == pending_sequence
                and base_commitment == actual
            )
            if (
                pending_sequence != current_sequence
                or pending_entry.get("predecessor") != anchored
                or pending_entry.get("resulting") != actual
                or not (pending_follows_interval or pending_is_interval_base)
                or inspect_publication(connection, pending_batch, pending_sequence) != "COMPLETE"
            ):
                raise LoopRejected("R14 evidence interval pending publication is not exact")

        for publication in publications:
            sequence = publication[3]
            batch, _, _, family, entry = steps[sequence]
            if publication != (
                batch.operation_kind,
                batch.idempotency_key,
                batch.request_fingerprint,
                sequence,
                "\n".join(record.record_id for record in batch.records),
            ) or inspect_publication(connection, batch, sequence) != "COMPLETE":
                raise LoopRejected("R14 evidence interval differs from physical publication")
            physical = tuple(
                connection.execute(
                    "SELECT record_id,owner,schema_id,canonical_bytes FROM records "
                    "WHERE tenant_id=? AND commit_sequence=? ORDER BY rowid",
                    (runtime._tenant_id, sequence),
                ).fetchall()
            )
            expected_physical = tuple(
                (record.record_id, record.owner, record.schema_id, record.canonical_bytes)
                for record in batch.records
            )
            if physical != expected_physical:
                raise LoopRejected("R14 evidence interval physical records differ")
            if family == "loop":
                if entry is None:
                    raise LoopRejected("R14 evidence interval loop evidence is absent")
                if batch.operation_kind == "agent_loop.execution-accounting-readiness.v1":
                    from chiplog.composition.r14_recovery_publication import validate_selected

                    validate_selected(
                        runtime, entry, batch, through_sequence=batch.expected_head
                    )
                    continue
                if batch.operation_kind == "agent_loop.execution-next-turn.v1":
                    from chiplog.composition.r14_next_turn_records import (
                        validate_selected_next_turn,
                    )

                    validate_selected_next_turn(runtime, entry, batch)
                    continue
                raise LoopRejected("R14 evidence interval contains an unregistered R14 record")
            if family != "owner" or len(batch.records) != 1:
                raise LoopRejected("R14 evidence interval contains a non-R16 publication")
            physical_record = batch.records[0]
            if (
                physical_record.owner != "effects"
                or physical_record.schema_id != "chiplog.effects.dispatch-outcome-record.v2"
            ):
                raise LoopRejected("R14 evidence interval contains a foreign R16 record")
            try:
                outcome = DispatchOutcomeRecordV2.model_validate_json(
                    physical_record.canonical_bytes
                )
            except (TypeError, ValueError) as error:
                raise LoopRejected("R14 evidence interval has malformed R16 outcome") from error
            if outcome.canonical_bytes() != physical_record.canonical_bytes:
                raise LoopRejected("R14 evidence interval has noncanonical R16 bytes")
            _verify_same_obligation_append(previous_outcome, outcome)
            previous_outcome = outcome

    if (
        owner_journal.snapshot() != owner_before
        or loop_journal.entries() != loop_before
        or runtime._commitment_journal.load(runtime._tenant_id) != anchored
    ):
        raise LoopRejected("R14 evidence interval changed while its cut was checked")


def _selected_resolution_preimage(runtime, expected_cut):
    """Prove the selected resolution and its allowed later R16 evidence suffix."""
    import sqlite3
    from contextlib import closing

    from chiplog.composition import r14_loop_semantic_reduction_publication
    from chiplog.composition import r14_original_call_resolution_publication as publication
    from chiplog.platform.publication_readback import inspect_publication

    selected = publication._selected_entries(runtime)
    if len(selected) != 1:
        raise LoopRejected("selected original resolution is absent or ambiguous")
    _, _, entry, batch = selected[0]
    evidence = publication.validate_selected_command(runtime, entry, batch)
    predecessor, resulting = entry.get("predecessor"), entry.get("resulting")
    if (
        batch.expected_head != expected_cut.tenant_commit_sequence
        or predecessor != expected_cut.materialization_commitment
    ):
        raise LoopRejected("selected original resolution does not extend its retained source cut")
    reductions = r14_loop_semantic_reduction_publication._selected_entries(runtime)
    if reductions:
        r14_loop_semantic_reduction_publication.validate_loop_semantic_reduction_startup(runtime)
        from chiplog.composition.r14_loop_history import read_execution_recovery_history

        read_execution_recovery_history(runtime, selected_only=True)
    owner_snapshot = runtime._owner_decisions().snapshot()
    loop_entries = runtime._loop_decisions().entries()
    materialization_markers = []
    for _, _, raw in loop_entries:
        entry = json.loads(raw)
        if (
            entry.get("kind") == "MATERIALIZED"
            and entry.get("operation_id") == batch.idempotency_key
        ):
            materialization_markers.append(entry)
    if len(materialization_markers) > 1:
        raise LoopRejected("selected original resolution has rival materialization markers")
    materialized = bool(materialization_markers)
    with read_connection(runtime._database) as connection:
        actual = capture_authority_snapshot_commitment(connection, runtime._tenant_id)
        head = connection.execute(
            "SELECT head FROM tenant_heads WHERE tenant_id=?", (runtime._tenant_id,)
        ).fetchone()
        state = inspect_publication(connection, batch, batch.expected_head + 1)
        anchored = runtime._commitment_journal.load(runtime._tenant_id)
        current_sequence = 0 if head is None else int(head[0])
        if state != "COMPLETE":
            raise LoopRejected("selected original resolution source differs from current authority")
        if not _selected_resolution_anchor_is_valid(
            actual,
            anchored,
            str(predecessor),
            str(resulting),
            materialized=materialized,
        ):
            raise LoopRejected("selected original resolution has a foreign or stale anchor")

        if not reductions:
            # Reconstruct the resolver's exact source preimage while removing the
            # complete selected suffix, including any authenticated later appends.
            with closing(sqlite3.connect(":memory:")) as historical:
                connection.backup(historical)
                historical.execute("BEGIN")
                historical.execute(
                    "DELETE FROM records WHERE tenant_id=? AND commit_sequence>?",
                    (runtime._tenant_id, expected_cut.tenant_commit_sequence),
                )
                historical.execute(
                    "DELETE FROM publications WHERE tenant_id=? AND commit_sequence>?",
                    (runtime._tenant_id, expected_cut.tenant_commit_sequence),
                )
                if expected_cut.tenant_commit_sequence:
                    historical.execute(
                        "UPDATE tenant_heads SET head=? WHERE tenant_id=?",
                        (expected_cut.tenant_commit_sequence, runtime._tenant_id),
                    )
                else:
                    historical.execute(
                        "DELETE FROM tenant_heads WHERE tenant_id=?", (runtime._tenant_id,)
                    )
                if (
                    capture_authority_snapshot_commitment(historical, runtime._tenant_id)
                    != expected_cut.materialization_commitment
                ):
                    raise LoopRejected("selected original resolver source preimage differs")

    if reductions:
        latest_entry = reductions[-1][2]
        retained_reduction = (
            r14_loop_semantic_reduction_publication.RetainedLoopSemanticReductionPublication
            .model_validate_json(latest_entry[r14_loop_semantic_reduction_publication.EVIDENCE_FIELD])
        )
        base_sequence = reductions[-1][3].expected_head + 1
        base_commitment = str(latest_entry["resulting"])
        base_witness = retained_reduction.request.complete_anchor_sources[-1]
    else:
        base_sequence = batch.expected_head + 1
        base_commitment = str(resulting)
        base_witness = expected_cut.selected_witness
    _authenticate_append_evidence_suffix(
        runtime,
        base_sequence=base_sequence,
        base_commitment=base_commitment,
        end_sequence=current_sequence,
        end_commitment=actual,
        base_witness=base_witness,
        owner_snapshot=owner_snapshot,
        loop_entries=loop_entries,
    )
    return evidence, actual, anchored, len(reductions), current_sequence


def authenticate_selected_resolution_prefix_held(runtime, actual: str, anchored: str) -> bool:
    """Authenticate one selected resolution as the complete current suffix."""
    runtime._authority_gate().require_held()
    try:
        from chiplog.composition import r14_original_call_resolution_publication as publication

        selected = publication._selected_entries(runtime)
        if len(selected) != 1:
            return False
        evidence = selected[0][2].get(publication.EVIDENCE_FIELD)
        if not isinstance(evidence, str):
            return False
        retained = publication.RetainedOriginalCallResolutionPublication.model_validate_json(
            evidence
        )
        checked, current, current_anchor, _, _ = _selected_resolution_preimage(
            runtime, retained.request.cut
        )
        return (
            checked == retained
            and current == actual
            and current_anchor == anchored
            and runtime._commitment_journal.load(runtime._tenant_id) == anchored
        )
    except ValueError, TypeError, RuntimeError, OSError, sqlite3.Error:
        return False


def _selected_witness_source(closed: R14NativeAcceptedOpenOutcome) -> RecoverySourceRecord:
    outcome = closed.latest_outcome
    member = closed.latest_selected_member
    raw = outcome.canonical_bytes()
    if (
        member.record_bytes != raw
        or member.record != outcome.record
        or member.kind != outcome.kind
        or member.intent.subject_id != outcome.snapshot.intent.intent_id
    ):
        raise LoopRejected("closed R16 selected witness differs from retained physical record")
    record = CallSubjectHead(
        subject_id=member.record.subject_id,
        revision=Present(head=member.record.head, fingerprint=member.record.fingerprint),
    )
    decision = CallSubjectHead(
        subject_id=member.selected_decision.subject_id,
        revision=Present(
            head=member.selected_decision.head,
            fingerprint=member.selected_decision.fingerprint,
        ),
    )
    return RecoverySourceRecord(
        owner="effects",
        subject=record,
        schema_id=outcome.schema_id,
        canonical_record_bytes=raw,
        selected_decision=decision,
        physical_record=record,
    )


def capture_original_call_resolution_request(
    runtime: CommonCliExecutionRuntime,
    *,
    allow_selected_resolution: bool = False,
    expected_cut=None,
    accepted_closed: R14NativeAcceptedOpenOutcome | None = None,
) -> PrepareOriginalCallResolution:
    """Capture revalidated R14 sources and the complete selected closed R16 cut.

    The effects reader reopens the full materialized history under its closed
    verifier. Unknown or unsupported effects rows fail closed; therefore the
    Effects-owned semantic-reduction inventory is empty in this profile.
    """
    from chiplog.composition.r14_original_resolver_invocation_publication import (
        read_selected_original_resolver_invocation_source_held,
    )
    from chiplog.composition.r14_original_resolver_policy_publication import (
        read_selected_original_effect_evidence_reducer_policy_source_held,
        read_selected_original_resolver_authority_policy_source_held,
    )

    runtime._pending()
    with runtime._authority_gate().hold():
        runtime._check_database_identity()
        database_identity = runtime._database_identity
        with read_connection(runtime._database) as connection:
            before = capture_authority_snapshot_commitment(connection, runtime._tenant_id)
            head_row = connection.execute(
                "SELECT head FROM tenant_heads WHERE tenant_id=?", (runtime._tenant_id,)
            ).fetchone()
            tenant_commit_sequence = 0 if head_row is None else int(head_row[0])
        anchored = runtime._commitment_journal.load(runtime._tenant_id)
        selected_suffix = None
        if expected_cut is not None:
            if not allow_selected_resolution:
                raise LoopRejected("selected source suffix supplied on a fresh resolution")
            selected_suffix = _selected_resolution_preimage(runtime, expected_cut)
        if before != anchored and selected_suffix is None:
            raise LoopRejected("original resolution source cut differs from authority anchor")

        opened = runtime.read_selected_original_obligation_sources(  # type: ignore[no-untyped-call]
            historical=allow_selected_resolution
        )
        resolver = read_selected_original_resolver_authority_policy_source_held(  # type: ignore[no-untyped-call]
            runtime, historical=True
        )
        reducer = read_selected_original_effect_evidence_reducer_policy_source_held(  # type: ignore[no-untyped-call]
            runtime, historical=True
        )
        invocation = read_selected_original_resolver_invocation_source_held(
            runtime, historical=allow_selected_resolution
        )
        if opened is None or resolver is None or reducer is None or invocation is None:
            raise LoopRejected(
                "selected original obligation authority sources are incomplete; HOLD"
            )
        opening_source, terminal_source = opened
        resolver_policy, resolver_source = resolver
        reducer_policy, reducer_source = reducer
        authenticated_invocation, invocation_source = invocation

        closed = accepted_closed
        if closed is None:
            history = read_execution_recovery_history(runtime, selected_only=True)
            snapshot, _, _, executions, _, _ = history
            if len(executions) != 1 or not snapshot.records:
                raise LoopRejected("original resolution requires one selected native execution")
            seal = executions[0].proposal.fan_out.response_seal
            seal_digest = seal.digest()
            seal_ref = CallSubjectHead(
                subject_id=seal.response_seal_id,
                revision=Present(head="record:" + seal_digest, fingerprint=seal_digest),
            )
            projection = R14OriginalNativePrepareSource(runtime).collect(seal_ref)
            if projection is None:
                raise LoopRejected("original resolution native Prepare source unavailable")
            accounting = R14NativeSelectedAccountingSource(
                runtime, projection, bind_native_execution_preparation_v2(projection)
            )
            closed = accounting.collect_one_accepted_closed_outcome(
                execution_run_head(snapshot.records[-1]),
                selected_only=True,
                allow_authenticated_later_suffix=selected_suffix is not None,
            )
        if closed is None:
            raise LoopRejected("exact selected closed R16 outcome unavailable; HOLD")

        from chiplog.capabilities.agent_loop.execution_recovery_contracts import (
            AcceptedOpenRecoveryOpeningV1,
            AcceptedOpenTerminalDispositionV1,
        )

        opening = AcceptedOpenRecoveryOpeningV1.model_validate_json(
            opening_source.canonical_record_bytes
        )
        terminal = AcceptedOpenTerminalDispositionV1.model_validate_json(
            terminal_source.canonical_record_bytes
        )
        original = opening.original
        if (
            terminal.kind != "RECOVERY_REQUIRED"
            or resolver_policy != original_resolver_authority_policy_v1(runtime._tenant_id)
            or reducer_policy != original_effect_evidence_reducer_policy_v1(runtime._tenant_id)
            or authenticated_invocation.original != original
            or authenticated_invocation.accepted_opening_source != opening_source
            or authenticated_invocation.accepted_terminal_source != terminal_source
            or authenticated_invocation.resolver_policy_source != resolver_source
            or authenticated_invocation.reducer_policy_source != reducer_source
            or closed.tenant_id != runtime._tenant_id
            or closed.database_identity != database_identity
            or closed.tenant_commit_sequence != tenant_commit_sequence
            or closed.materialization_commitment != before
        ):
            raise LoopRejected("selected R14 and R16 resolution sources do not share one cut")
        source_sequence = tenant_commit_sequence
        source_commitment = before
        if selected_suffix is not None:
            (
                _,
                selected_actual,
                selected_anchor,
                reduction_count,
                checked_sequence,
            ) = selected_suffix
            if (
                selected_actual != before
                or selected_anchor
                not in (before, expected_cut.materialization_commitment)
                or checked_sequence != tenant_commit_sequence
            ):
                raise LoopRejected(
                    "selected resolution suffix changed the authenticated source cut"
                )
            if reduction_count:
                source_sequence = tenant_commit_sequence
                source_commitment = before
            else:
                source_sequence = expected_cut.tenant_commit_sequence
                source_commitment = expected_cut.materialization_commitment
        if not allow_selected_resolution:
            _require_resolution_absent(runtime)

        stream = LoopRecoveryStream(
            stream_id=original.obligation_stream_id,
            registry=reducer_source.physical_record,
            subject=opening_source.subject,
            schema_id="chiplog.loop.original-obligation-stream.v1",
        )
        command_id = (
            "r14-original-resolution:"
            + runtime._tenant_id
            + ":"
            + original.obligation_id
            + ":"
            + closed.latest_outcome.record.head
        )
        cut = OriginalResolverCut(
            tenant_id=runtime._tenant_id,
            database_id=str(database_identity[0]),
            tenant_commit_sequence=source_sequence,
            materialization_commitment=source_commitment,
            original=original,
            obligation_stream=stream,
            current_obligation=opening_source,
            call_terminal=terminal_source,
            selected_witness=_selected_witness_source(closed),
            complete_ordered_evidence=closed.complete_ordered_evidence_sources,
            complete_selected_effects_reductions=(),
            expected_closure=Absent(),
            expected_recovered_outcome=Absent(),
            resolver_policy=resolver_source,
            reducer_policy=reducer_source,
            authority=IndependentResolverAuthority(
                registered_authority=resolver_source,
                authenticated_invocation=invocation_source,
            ),
        )
        request = PrepareOriginalCallResolution(command_id=command_id, cut=cut)
        with read_connection(runtime._database) as connection:
            after = capture_authority_snapshot_commitment(connection, runtime._tenant_id)
        runtime._check_database_identity()
        if (
            after != before
            or runtime._commitment_journal.load(runtime._tenant_id) != anchored
            or runtime._database_identity != database_identity
        ):
            raise LoopRejected("original resolution source cut changed during capture")
        return request
