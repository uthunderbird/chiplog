"""Historical reconstruction from real selected frames, not fresh admission proof.

Companion fixture bytes test the exact batch join. Their effects semantics remain
an obligation of the registered fresh publisher; no fixture invocation is authority.
"""

import hashlib
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from chiplog.adapters.driven.deployment_trust import IndependentTenantDecisionJournal
from chiplog.adapters.driven.effects_broker import EffectsIntegrityError
from chiplog.adapters.driven.loop_prompts import render_delivery_prompt, render_prompt
from chiplog.capabilities.agent_loop import domain
from chiplog.capabilities.agent_loop.contracts import (
    BudgetPolicy,
    Complete,
    Delivery,
    DisclosureLabel,
    EndpointSelection,
    RunRecord,
    VisibilityManifest,
    VisibilityMember,
)
from chiplog.capabilities.agent_loop.delivery_contracts import (
    ExactHead,
    OriginSelection,
    ProviderRecipient,
)
from chiplog.capabilities.agent_loop.delivery_preparation import (
    Commentary,
    DeliveryCompletion,
    DeliveryObservation,
    DeliveryPrepareRequest,
    HistoricalEnvelope,
    ProposedDelivery,
    complete_delivery,
)
from chiplog.composition.r16_effects import _reconstruct_runs
from chiplog.platform._owner_publication_contracts import (
    AuthoritativeReadManifest,
    CompleteDeliveryBatch,
    InvocationProofRef,
    OwnerCommandBytes,
    OwnerRecordBytes,
    PublicationIdentity,
    WorkerAuthentication,
)
from chiplog.platform._sqlite import (
    EventAppender,
    FenceAdvanceCommand,
    PhysicalPublicationCommand,
    PhysicalRecord,
    SQLiteMaterializer,
)
from chiplog.platform.authority_reads import capture_authority_storage_state
from chiplog.platform.owner_decision_journal import IndependentOwnerDecisionJournal
from chiplog.platform.owner_publications import PreparedOwnerPublication, SelectedOwnerDecision


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _head(identity: str) -> ExactHead:
    return ExactHead(identity=identity, head=identity, fingerprint=_sha(identity.encode()))


def _genesis(run_id: str) -> RunRecord:
    run = RunRecord(
        tenant="t",
        principal="p",
        run_id=run_id,
        state="CREATED",
        head="pending",
        predecessor=None,
        prompt="fixture",
        policy=BudgetPolicy(),
        origin=EndpointSelection(
            kind="ORIGIN_EXACT",
            ingress_binding_head="i",
            endpoint_head="e",
            endpoint_id="local",
            provider="hermetic-local",
            recipient="p",
            canonical_address="local://p",
            credential_binding_head="c",
        ),
        contour_head="contour",
        policy_head="policy",
        worker_session="historical-worker",
        event="RunCreated",
    )
    return run.model_copy(update={"head": "loop:" + run.digest()})


async def _chain(run_id: str, *, expanded: bool) -> tuple[list[RunRecord], DeliveryObservation]:
    rows = [_genesis(run_id)]
    rows.append(domain.transition(rows[-1], "ACTIVE"))
    rows.append(domain.start_turn(rows[-1]))
    started = rows[-1]
    artifact = await (render_delivery_prompt("fixture") if expanded else render_prompt("fixture"))
    restricted = domain.context_label(started)
    members = (
        VisibilityMember(
            record_id=run_id + "/context",
            revision_head=started.head,
            content="fixture",
            provenance_head=started.head,
            label_head="contour",
            label=restricted,
            producer="agent_loop",
            surface="context",
        ),
        VisibilityMember(
            record_id=run_id + "/prompt",
            revision_head=artifact.content_hash,
            content=artifact.rendered,
            provenance_head=artifact.content_hash,
            label_head="policy",
            label=restricted,
            producer="promptstrings",
            surface="prompt",
        ),
        VisibilityMember(
            record_id=run_id + "/schema",
            revision_head=artifact.digest(),
            content=artifact.response_schema_json,
            provenance_head=artifact.digest(),
            label_head="policy",
            label=DisclosureLabel(value="UNRESTRICTED", allowed_endpoints=()),
            producer="agent_loop",
            surface="schema",
        ),
    )
    rows.append(domain.accumulate(started, members))
    manifest = VisibilityManifest(
        tenant="t",
        principal="p",
        contour_head="contour",
        run_id=run_id,
        turn_id=rows[-1].turns[-1].turn_id,
        generation=0,
        worker_session="historical-worker",
        members=members,
        joined_label=domain.join_labels(tuple(m.label for m in members)),
        artifact=artifact,
    )
    rows.append(domain.prepare(rows[-1], manifest))
    rows.append(domain.emit(rows[-1]))
    response = (
        DeliveryCompletion(
            tenant="t",
            run_id=run_id,
            turn_id=manifest.turn_id,
            deliveries=(ProposedDelivery(payload=(Commentary(text="hello"),)),),
        )
        if expanded
        else Complete(
            kind="Complete", deliveries=(Delivery(kind="NonAuthoritativeText", text="legacy"),)
        )
    )
    rows.append(domain.capture(rows[-1], response.canonical_bytes(), "historical-receipt"))
    captured = rows[-1]
    recipient = ProviderRecipient(
        provider_id="hermetic-local",
        account_id="account",
        recipient_id="p",
        endpoint=_head("local"),
        canonical_address=b"local://p",
        credential_binding=_head("c"),
    )
    observation = DeliveryObservation(
        tenant="t",
        run=ExactHead(identity=run_id, head=captured.head, fingerprint=captured.digest()),
        turn_id=manifest.turn_id,
        captured_response=response.canonical_bytes(),
        source_frontier=0,
        origin=OriginSelection(ingress_binding=_head("i"), recipient=recipient),
        recipients=(recipient,),
        history=tuple(
            HistoricalEnvelope(
                content=ExactHead(
                    identity=m.record_id, head=m.revision_head, fingerprint=m.digest()
                ),
                visibility=ExactHead(
                    identity=captured.turns[-1].attempts[-1].attempt_id,
                    head=manifest.digest(),
                    fingerprint=manifest.digest(),
                ),
                provenance=_head(m.provenance_head),
                disclosure=_head(m.label_head),
                label=m.label,
                narrowing=(),
            )
            for m in members
        ),
        queries=(),
        policy=_head("historical-policy"),
        worker_fence=_head("old-worker-lease"),
    )
    if not expanded:
        assert isinstance(response, Complete)
        rows.append(domain.complete(captured, response))
    return rows, observation


async def _seed(database: Path, *, mutation: str = "none") -> IndependentOwnerDecisionJournal:
    journal = IndependentOwnerDecisionJournal(
        IndependentTenantDecisionJournal(database.with_suffix(".owners")), "t"
    )
    legacy, _ = await _chain("legacy", expanded=False)
    expanded, observation = await _chain("expanded", expanded=True)
    accepted = complete_delivery(expanded[-1], observation)
    request = DeliveryPrepareRequest(previous=expanded[-1], observation=observation)
    if mutation == "previous":
        request = request.model_copy(update={"previous": expanded[-2]})
    elif mutation == "observation":
        request = request.model_copy(
            update={"observation": observation.model_copy(update={"policy": _head("rival")})}
        )
    raw = request.canonical_bytes()
    if mutation == "noncanonical":
        raw += b" "
    if mutation == "missing_command":
        raw = b"{}"
    with SQLiteMaterializer(
        database,
        record_contracts={
            "agent_loop": "chiplog.agent-loop.record.v1",
            "conversation": "fixture.companion.v1",
        },
    ) as store:
        async with EventAppender(store, capacity=4) as appender:
            await appender.advance_fence(FenceAdvanceCommand("t", "fence", 0))
            sequence = 0
            for row in (*legacy, *expanded):
                payload = row.canonical_bytes()
                result = await appender.submit(
                    PhysicalPublicationCommand(
                        "t",
                        "agent_loop",
                        row.head,
                        _sha(payload),
                        sequence,
                        "fence",
                        0,
                        0,
                        (
                            PhysicalRecord(
                                row.head,
                                "agent_loop",
                                "chiplog.agent-loop.record.v1",
                                payload,
                                _sha(payload),
                            ),
                        ),
                    )
                )
                assert result.disposition == "COMMITTED"
                sequence += 1
            anchor, _ = capture_authority_storage_state(database)
            own = OwnerRecordBytes(
                owner="agent_loop",
                record_kind="agent_loop.run",
                record_id=accepted.head,
                schema_id="chiplog.agent-loop.record.v1",
                canonical_bytes=accepted.canonical_bytes(),
                fingerprint=_sha(accepted.canonical_bytes()),
            )
            companion = OwnerRecordBytes(
                owner="conversation",
                record_kind="fixture.history",
                record_id="companion",
                schema_id="fixture.companion.v1",
                canonical_bytes=b'{"accepted":"fixture"}',
                fingerprint=_sha(b'{"accepted":"fixture"}'),
            )
            batch = CompleteDeliveryBatch(
                identity=PublicationIdentity(
                    tenant_id="t",
                    command_id="complete",
                    command_fingerprint=_sha(raw),
                    canonicalization_version="chiplog.owner-publication.v1",
                ),
                authentication=WorkerAuthentication(
                    invocation=InvocationProofRef(
                        issuance_id="fixture",
                        issuance_fingerprint=_sha(b"fixture"),
                        broker_epoch="old",
                        broker_session="old",
                        runtime_generation="old",
                        operation_subject="complete",
                    ),
                    applicability_schema="fixture.not-admission",
                    applicability_bytes=b"fixture",
                    applicability_fingerprint=_sha(b"fixture"),
                ),
                expected=AuthoritativeReadManifest(
                    tenant_id="t",
                    tenant_frontier=sequence,
                    expected_materialization_commitment=anchor,
                    registry_head="historical-registry",
                    registry_fingerprint=_sha(b"registry"),
                    ordered_heads=(),
                    complete_manifest_fingerprint=_sha(b"reads"),
                ),
                loop_command=OwnerCommandBytes(
                    owner="agent_loop",
                    schema_id=(
                        "chiplog.delivery.validate-completion.v1"
                        if mutation == "schema"
                        else "chiplog.delivery.prepare-completion.v1"
                    ),
                    canonical_bytes=raw,
                    fingerprint=_sha(raw),
                ),
                prepared_effects_commands=(
                    OwnerCommandBytes(
                        owner="effects",
                        schema_id="fixture.effects.v1",
                        canonical_bytes=b"{}",
                        fingerprint=_sha(b"{}"),
                    ),
                ),
                complete_records=(companion, own),
                complete_batch_fingerprint=_sha(b"fixture-batch"),
            )
            selected: list[SelectedOwnerDecision] = []

            def select(commitment: str) -> None:
                selected.append(
                    journal.select(
                        PreparedOwnerPublication(batch, "fixture", "fence", 0, anchor), commitment
                    )
                )

            result = await appender.submit(
                PhysicalPublicationCommand(
                    "t",
                    batch.operation,
                    "complete",
                    _sha(raw),
                    sequence,
                    "fence",
                    0,
                    0,
                    tuple(
                        PhysicalRecord(
                            r.record_id, r.owner, r.schema_id, r.canonical_bytes, r.fingerprint
                        )
                        for r in batch.complete_records
                    ),
                    decision_guard=select,
                )
            )
            assert result.disposition == "COMMITTED"
            journal.materialized(selected[0])
            sequence += 1
            active = _genesis("other-active")
            for row in (active, domain.transition(active, "ACTIVE")):
                payload = row.canonical_bytes()
                assert (
                    await appender.submit(
                        PhysicalPublicationCommand(
                            "t",
                            "agent_loop",
                            row.head,
                            _sha(payload),
                            sequence,
                            "fence",
                            0,
                            0,
                            (
                                PhysicalRecord(
                                    row.head,
                                    "agent_loop",
                                    "chiplog.agent-loop.record.v1",
                                    payload,
                                    _sha(payload),
                                ),
                            ),
                        )
                    )
                ).disposition == "COMMITTED"
                sequence += 1
    return journal


async def test_full_history_reconstructs_expanded_completion_before_other_active_run(
    tmp_path: Path,
) -> None:
    database = tmp_path / "history.sqlite"
    await _seed(database)
    # Reopen the independent journal and physical database, with no current
    # trust/session/lease arguments available to historical reconstruction.
    reopened = IndependentOwnerDecisionJournal(
        IndependentTenantDecisionJournal(database.with_suffix(".owners")), "t"
    )
    with sqlite3.connect(database) as connection:
        runs = _reconstruct_runs(connection, "t", reopened.snapshot())
    assert [(r.run_id, r.state) for r in runs] == [
        ("legacy", "SUCCEEDED"),
        ("expanded", "SUCCEEDED"),
        ("other-active", "ACTIVE"),
    ]
    assert runs[0].accepted_delivery_binding == "LEGACY_R13"
    assert runs[1].accepted_delivery_binding != "LEGACY_R13"


@pytest.mark.parametrize(
    "mutation",
    (
        "previous",
        "observation",
        "schema",
        "missing_command",
        "noncanonical",
        "missing_decision",
        "duplicate_decision",
        "companion_missing",
        "companion_bytes",
        "run_owner",
        "run_missing",
        "run_schema",
        "order",
    ),
)
async def test_historical_substitution_or_omission_rejects_entire_scan(
    tmp_path: Path, mutation: str
) -> None:
    database = tmp_path / "history.sqlite"
    journal = await _seed(database, mutation=mutation)
    snapshot = journal.snapshot()
    if mutation == "missing_decision":
        snapshot = replace(snapshot, decisions=())
    elif mutation == "duplicate_decision":
        snapshot = replace(snapshot, decisions=(*snapshot.decisions, *snapshot.decisions))
    with sqlite3.connect(database) as connection:
        if mutation == "companion_missing":
            connection.execute("DELETE FROM records WHERE record_id='companion'")
        elif mutation == "companion_bytes":
            connection.execute(
                "UPDATE records SET canonical_bytes=? WHERE record_id='companion'", (b"changed",)
            )
        elif mutation in {"run_owner", "run_missing", "run_schema"}:
            run_id = next(
                row.record_id
                for row in snapshot.decisions[0].prepared.request.complete_records
                if row.owner == "agent_loop"
            )
            if mutation == "run_missing":
                connection.execute("DELETE FROM records WHERE record_id=?", (run_id,))
            elif mutation == "run_owner":
                connection.execute(
                    "UPDATE records SET owner='conversation' WHERE record_id=?", (run_id,)
                )
            else:
                connection.execute(
                    "UPDATE records SET schema_id='unknown.v9' WHERE record_id=?", (run_id,)
                )
        elif mutation == "order":
            manifest = connection.execute(
                "SELECT record_ids FROM publications WHERE idempotency_key='complete'"
            ).fetchone()[0]
            connection.execute(
                "UPDATE publications SET record_ids=? WHERE idempotency_key='complete'",
                ("\n".join(reversed(manifest.split("\n"))),),
            )
        with pytest.raises(EffectsIntegrityError, match="historical_delivery") as error:
            _reconstruct_runs(connection, "t", snapshot)
        assert error.value.__cause__ is not None
