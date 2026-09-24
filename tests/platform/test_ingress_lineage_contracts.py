"""Nonempty C-level ingress lineage fixtures.

These checks join decoded contract rows only.  They intentionally make no
claim about a live writer, source authority, CAS, restart recovery, or release.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal, TypedDict

import pytest

from chiplog.platform._ingress_contracts import (
    AdmissionBound,
    BlockedRebase,
    ClassReserve,
    DrainManifest,
    DurableCustody,
    Head,
    LossSlot,
    ParserAttempt,
    PollMaterialized,
    PollMember,
    PollPageManifest,
    ProviderAuthentication,
    QuarantineCustody,
    QuarantineRetry,
    RawStaged,
    ReadyGeneration,
    ReceiptToken,
    SourceBinding,
    SourceClass,
    UnknownEndpoint,
)
from chiplog.platform.ingress_record_contracts import (
    AdmissionRecord,
    AppliedCursorRecord,
    BoundRebaseRecord,
    BoundRecord,
    CursorAuthorizationRecord,
    CustodySuccessorRecord,
    DeficitRecord,
    DrainRecord,
    EpochRecord,
    HandoffAttemptRecord,
    HandoffAuthorizationRecord,
    HandoffObservationRecord,
    InboxRecord,
    IngressRecord,
    ParserResultRecord,
    ParserSelectionRecord,
    PollDispositionRecord,
    PollPageRecord,
    PollRequestRecord,
    StagedRawRecord,
    TokenRecord,
    decode_ingress_member,
    decode_ingress_record,
    encode_ingress_member,
)
from chiplog.platform.ingress_runtime_snapshot import (
    AdmissionDeficit,
    AdmissionQueueEntry,
    IngressRuntimeSnapshot,
    ParserIdentity,
    QuarantineRuntimeState,
    SelectedDrainSnapshot,
    TokenRuntimeState,
)
from chiplog.platform.ingress_transition_contracts import IngressCommandIdentity


def digest(name: str | bytes) -> str:
    return hashlib.sha256(name if isinstance(name, bytes) else name.encode()).hexdigest()


def head(name: str) -> Head:
    raw = ("fixture/" + name).encode()
    return Head(identity=name, head="fixture-head/" + name, fingerprint=digest(raw))


def command(name: str) -> IngressCommandIdentity:
    return IngressCommandIdentity(tenant_id="tenant", database_id="database", command_id=name)


class RecordBaseKwargs(TypedDict):
    record_id: str
    tenant_id: str
    database_id: str
    command: IngressCommandIdentity
    command_fingerprint: str
    predecessor: Head | None
    source_heads: tuple[Head, ...]


type ReserveClass = SourceClass | Literal["ORDINARY"]


def base(name: str, _kind: str, predecessor: Head | None = None) -> RecordBaseKwargs:
    return {
        "record_id": "record-" + name,
        "tenant_id": "tenant",
        "database_id": "database",
        "command": command("command-" + name),
        "command_fingerprint": digest("command-bytes-" + name),
        "predecessor": predecessor or head("predecessor-" + name),
        "source_heads": (head("source-observation-" + name),),
    }


def binding(source_class: SourceClass = "TELEGRAM_PUSH") -> SourceBinding:
    return SourceBinding(
        manifest_row=head("manifest"),
        source_class=source_class,
        tenant_id="tenant",
        database_id="database",
        source_identity="source-" + source_class,
        endpoint_account_binding=UnknownEndpoint(),
        broker_epoch="epoch",
        broker_session="session",
        admission_epoch=head("epoch"),
        admission_fence=7,
        transport_version="transport-v1",
    )


def authentication() -> ProviderAuthentication:
    return ProviderAuthentication(
        proof=head("auth-proof"),
        source=binding(),
        raw_digest=digest("authenticated raw"),
        principal_contour=head("principal"),
        freshness=head("freshness"),
        replay_identity="replay",
        original_subject="subject",
        contract_version="auth-v1",
        canonicalization_version="canon-v1",
        provider_identity="provider",
        account_id="account",
        endpoint_binding=head("endpoint-binding"),
        credential_key=head("credential"),
        audience="audience",
    )


def reserve(source_class: ReserveClass) -> ClassReserve:
    return ClassReserve(
        source_class=source_class,
        byte_quantum=4,
        maximum_item_bytes=64,
        deficit_cap=16,
        physical_item_reserve=1,
        physical_byte_reserve=4,
        physical_quarantine_reserve=1,
        ready_depth_limit=8,
        descendant_snapshot_byte_limit=128,
    )


def generation(name: str, sequence: int) -> ReadyGeneration:
    return ReadyGeneration(
        stable_work_id="work-" + name,
        durable_admission_commit_seq=sequence,
        stable_tie_identity="tie-" + name,
        exact_head=head("generation-" + name),
        accounted_bytes=5,
    )


def bound(
    name: str, target: ReadyGeneration, predecessors: tuple[ReadyGeneration, ...], *, slot: int = 9
) -> AdmissionBound:
    return AdmissionBound(
        bound_id="bound-" + name,
        bound_lineage_id="lineage-" + name,
        epoch=head("epoch"),
        fairness_version="fair-v1",
        formula_version="formula-v1",
        canonical_class_order=("ORDINARY", "TELEGRAM_PUSH"),
        reserve=reserve("ORDINARY"),
        initial_deficit=8,
        origin_scheduler_slot=slot,
        complete_ordered_predecessors=predecessors,
        target=target,
        absolute_selection_deadline_slot=slot + 5,
        evaluation_scheduler_slot=slot + 1,
    )


@dataclass(frozen=True)
class FixtureRows:
    token: TokenRecord
    staged_raw: StagedRawRecord
    custody: CustodySuccessorRecord
    inbox: InboxRecord
    admission: AdmissionRecord
    bound: BoundRecord
    bound_rebase: BoundRebaseRecord
    deficit: DeficitRecord
    epoch: EpochRecord
    drain: DrainRecord
    poll_page: PollPageRecord
    poll_disposition: PollDispositionRecord
    handoff_authorization: HandoffAuthorizationRecord
    cursor_authorization: CursorAuthorizationRecord
    handoff_attempt: HandoffAttemptRecord
    handoff_observation: HandoffObservationRecord
    applied_cursor: AppliedCursorRecord
    poll_request: PollRequestRecord
    parser_selection: ParserSelectionRecord
    parser_result: ParserResultRecord

    def all(self) -> tuple[IngressRecord, ...]:
        return (
            self.token,
            self.staged_raw,
            self.custody,
            self.inbox,
            self.admission,
            self.bound,
            self.bound_rebase,
            self.deficit,
            self.epoch,
            self.drain,
            self.poll_page,
            self.poll_disposition,
            self.handoff_authorization,
            self.cursor_authorization,
            self.handoff_attempt,
            self.handoff_observation,
            self.applied_cursor,
            self.poll_request,
            self.parser_selection,
            self.parser_result,
        )


def records() -> FixtureRows:
    auth = authentication()
    token = ReceiptToken(
        token_id="token",
        source=binding(),
        receive_slot="receive-slot",
        maximum_bytes=32,
        predecessor=head("token-prior"),
        command_fingerprint=digest("token-command"),
    )
    token_head, custody_head, inbox_head = (
        head("token-record"),
        head("custody-record"),
        head("inbox-record"),
    )
    ordinary, telegram = generation("ordinary", 11), generation("telegram", 12)
    initial = bound("initial", telegram, (ordinary,))
    replacement = bound("replacement", telegram, (ordinary,), slot=9).model_copy(
        update={"bound_lineage_id": "lineage-initial"}
    )
    deficit_head, page_head, cursor_head, authorization_head = (
        head("deficit"),
        head("page"),
        head("cursor"),
        head("cursor-authorization"),
    )
    member_one, member_two = (
        PollMember(identity="member-one", digest=digest("member-one")),
        PollMember(identity="member-two", digest=digest("member-two")),
    )
    dispositions = (
        PollMaterialized(member=member_one, inbox=head("member-one-inbox")),
        PollMaterialized(member=member_two, inbox=head("member-two-inbox")),
    )
    page = PollPageManifest(
        page_id="page",
        authentication=auth,
        transport_contract="DURABLE_CURSOR_BEFORE_REQUEST",
        request_attempt=head("poll-request"),
        predecessor_cursor=cursor_head,
        candidate_cursor=b"next-cursor\x00",
        raw_page_digest=digest("raw-page"),
        complete_ordered_members=(member_one, member_two),
        manifest_digest=digest("page-manifest"),
        schema_version="page-v1",
        canonicalization_version="canon-v1",
    )
    parser = ParserIdentity(attempt_id="attempt-1", parser_id="parser", parser_version="v1")
    attempt = ParserAttempt(
        attempt_id=parser.attempt_id,
        exact_prior_retry_head=head("retry-prior"),
        custody=head("quarantine-custody"),
        parser_id=parser.parser_id,
        parser_version=parser.parser_version,
        original_authentication=auth,
        result=QuarantineRetry(
            custody=head("quarantine-custody"),
            failure_or_unknown="unknown",
            next_parser_constraints=head("next-parser"),
        ),
    )
    rebase = BlockedRebase(
        blocked_generation=ordinary,
        blocked_head=head("blocked-ordinary"),
        consumed_skip_slot=10,
        resulting_deficit=3,
        closed_descendant_bounds=(head("closed-bound"),),
        replacement_descendant_bounds=(replacement,),
        batch_fingerprint=digest("rebase-batch"),
    )
    manifest = DrainManifest(
        epoch=head("epoch"),
        state="DRAINING",
        fence=7,
        canonical_reserves=(reserve("ORDINARY"), reserve("TELEGRAM_PUSH")),
        scheduler_trace=(head("schedule-1"),),
        all_tokens=(token_head,),
        all_successors=(custody_head,),
        ready_fifo=(ordinary, telegram),
        blocked_holds=(rebase.blocked_head,),
        bound_snapshots=(initial, replacement),
        deficit_heads=(deficit_head,),
        producer_quiescence=head("quiescence"),
        complete_inventory_fingerprint=digest("inventory"),
        quarantine_heads=(head("quarantine-custody"),),
        parser_remainder_fences=((head("parser-fence-a"), head("parser-fence-b")),),
        settled_token_heads=(),
        physical_occupancy=(token_head,),
        ready_token_ids=("token", None),
        blocked_token_ids=("token",),
    )
    return FixtureRows(
        token=TokenRecord(
            **base("token", "TOKEN"),
            command_kind="ingress.allocate_receipt_token",
            token=token,
            acquisition=LossSlot(token=token),
        ),
        staged_raw=StagedRawRecord(
            **base("raw", "STAGED_RAW", token_head),
            command_kind="ingress.stage_raw_bytes",
            staged=RawStaged(
                token=token,
                raw_bytes=b"raw\x00",
                raw_digest=digest(b"raw\x00"),
                predecessor=token_head,
            ),
        ),
        custody=CustodySuccessorRecord(
            **base("custody", "CUSTODY", token_head),
            command_kind="ingress.publish_custody_successor",
            token=token_head,
            expected_token_state=head("raw-state"),
            custody=DurableCustody(
                token=token_head,
                raw_bytes=b"raw\x00",
                raw_digest=digest(b"raw\x00"),
                authentication=auth,
                inbox=inbox_head,
            ),
        ),
        inbox=InboxRecord(
            **base("inbox", "INBOX", custody_head),
            command_kind="ingress.publish_custody_successor",
            inbox=inbox_head,
            custody=custody_head,
            token=token_head,
            raw_bytes=b"raw\x00",
            authentication=auth,
        ),
        admission=AdmissionRecord(
            **base("admission", "ADMISSION"),
            command_kind="ingress.admit_ready_generation",
            source_class="TELEGRAM_PUSH",
            generation=telegram,
            token=token_head,
            dependency_readiness=head("readiness"),
            bound=initial,
        ),
        bound=BoundRecord(
            **base("bound", "BOUND"), command_kind="ingress.admit_ready_generation", bound=initial
        ),
        bound_rebase=BoundRebaseRecord(
            **base("rebase", "BOUND"),
            command_kind="ingress.block_and_rebase_prefix",
            rebase=rebase,
            expected_deficit=deficit_head,
        ),
        deficit=DeficitRecord(
            **base("deficit", "DEFICIT"),
            command_kind="ingress.block_and_rebase_prefix",
            deficit=AdmissionDeficit(source_class="ORDINARY", head=deficit_head, value=3),
            expected_deficit=deficit_head,
            scheduler_slot=10,
            selected_generation=None,
            blocked_generation=rebase.blocked_head,
        ),
        epoch=EpochRecord(
            **base("epoch", "EPOCH"),
            command_kind="ingress.quiesce_admission",
            epoch=head("epoch"),
            fence=7,
            state="QUIESCING",
            expected_epoch=head("epoch-prior"),
            expected_fence=6,
            quiescence_command=head("quiescence"),
        ),
        drain=DrainRecord(
            **base("drain", "DRAIN"),
            command_kind="ingress.publish_drain_manifest",
            manifest=manifest,
            expected_epoch=head("epoch"),
            expected_drain_manifest=None,
        ),
        poll_page=PollPageRecord(
            **base("page", "POLL_PAGE"),
            command_kind="ingress.create_poll_page_manifest",
            page=page,
            raw_page_custody=head("raw-page-custody"),
            raw_page_bytes=b"raw-page\x00",
        ),
        poll_disposition=PollDispositionRecord(
            **base("disposition-one", "POLL_DISPOSITION"),
            command_kind="ingress.publish_poll_member_disposition",
            page=page_head,
            member_index=0,
            disposition=dispositions[0],
        ),
        handoff_authorization=HandoffAuthorizationRecord(
            **base("handoff-auth", "HANDOFF_AUTHORIZATION"),
            command_kind="ingress.authorize_local_ack",
            handoff_id="handoff",
            source_class="TELEGRAM_PUSH",
            authorization_kind="ingress.authorize_local_ack",
            token=token_head,
            custody=custody_head,
            source_boundary=head("source-boundary"),
        ),
        cursor_authorization=CursorAuthorizationRecord(
            **base("cursor-auth", "HANDOFF_AUTHORIZATION"),
            command_kind="ingress.authorize_poll_cursor",
            page=page_head,
            expected_applied_cursor=cursor_head,
            complete_ordered_dispositions=dispositions,
        ),
        handoff_attempt=HandoffAttemptRecord(
            **base("handoff-attempt", "HANDOFF_ATTEMPT"),
            command_kind="ingress.issue_push_response_attempt",
            handoff_id="handoff",
            attempt_id="send-1",
            authorization=head("handoff-authorization"),
            endpoint=head("endpoint"),
            payload=b"ack\x00",
        ),
        handoff_observation=HandoffObservationRecord(
            **base("handoff-observation", "HANDOFF_OBSERVATION"),
            command_kind="ingress.observe_authenticated_provider_receipt",
            handoff_id="handoff",
            issued_attempt=head("handoff-attempt"),
            authorization=None,
            result="AUTHENTICATED_RECEIPT",
            observation=head("receipt-source"),
            raw_observation_bytes=b"receipt\x00",
            authenticated_source=head("receipt-source"),
            selected_raw_custody=head("receipt-raw-custody"),
        ),
        applied_cursor=AppliedCursorRecord(
            **base("applied-cursor", "APPLIED_CURSOR"),
            command_kind="ingress.apply_poll_cursor",
            source=binding("TELEGRAM_POLL"),
            page=page_head,
            authorization=authorization_head,
            expected_applied_cursor=cursor_head,
            cursor_bytes=page.candidate_cursor,
        ),
        poll_request=PollRequestRecord(
            **base("poll-request", "POLL_REQUEST"),
            command_kind="ingress.issue_poll_request",
            request_id="request-1",
            applied_cursor=cursor_head,
            source_contract=head("poll-source-contract"),
        ),
        parser_selection=ParserSelectionRecord(
            **base("parser-selection", "PARSER_SELECTION"),
            command_kind="ingress.select_quarantine_parser_attempt",
            custody=attempt.custody,
            expected_retry_head=attempt.exact_prior_retry_head,
            parser=parser,
        ),
        parser_result=ParserResultRecord(
            **base("parser-result", "PARSER_RESULT"),
            command_kind="ingress.publish_quarantine_result",
            custody=attempt.custody,
            selected_attempt_head=head("parser-selection"),
            attempt=attempt,
        ),
    )


def test_every_nonempty_record_payload_roundtrips_through_its_exact_registered_row() -> None:
    rows = records().all()
    assert len(rows) == 20
    assert all(record.source_heads for record in rows)
    for record in rows:
        member = encode_ingress_member(record)
        assert (
            decode_ingress_record(record.record_kind, record.schema_id, record.canonical_bytes())
            == record
        )
        assert decode_ingress_member(member) == record


def assert_page_lineage(
    page: PollPageRecord,
    request: PollRequestRecord,
    dispositions: Iterable[PollDispositionRecord],
    cursor: AppliedCursorRecord,
    authorization: CursorAuthorizationRecord,
    page_head: Head,
    authorization_head: Head,
) -> None:
    ordered = tuple(dispositions)
    members = tuple(item.disposition.member for item in ordered)
    if (
        page.page.complete_ordered_members != members
        or len(members) != len({item.identity for item in members})
        or tuple(item.member_index for item in ordered) != tuple(range(len(ordered)))
        or any(item.page != page_head for item in ordered)
        or page.page.request_attempt != head("poll-request")
        or page.page.predecessor_cursor != request.applied_cursor
        or cursor.page != page_head
        or cursor.authorization != authorization_head
        or cursor.cursor_bytes != page.page.candidate_cursor
        or authorization.page != page_head
        or authorization.expected_applied_cursor != request.applied_cursor
    ):
        raise ValueError("page lineage structural join differs")


def test_nonempty_page_cursor_lineage_rejects_each_join_mutant() -> None:
    fixture = records()
    page = fixture.poll_page
    request = fixture.poll_request
    cursor = fixture.applied_cursor
    authorization = fixture.cursor_authorization
    dispositions = (
        fixture.poll_disposition,
        PollDispositionRecord(
            **base("disposition-two", "POLL_DISPOSITION"),
            command_kind="ingress.publish_poll_member_disposition",
            page=head("page"),
            member_index=1,
            disposition=authorization.complete_ordered_dispositions[1],
        ),
    )
    assert_page_lineage(
        page,
        request,
        dispositions,
        cursor,
        authorization,
        head("page"),
        head("cursor-authorization"),
    )
    for mutant in (dispositions[::-1], dispositions[:1], (dispositions[0], dispositions[0])):
        with pytest.raises(ValueError):
            assert_page_lineage(
                page,
                request,
                mutant,
                cursor,
                authorization,
                head("page"),
                head("cursor-authorization"),
            )
    with pytest.raises(ValueError):
        assert_page_lineage(
            page,
            request,
            dispositions,
            cursor.model_copy(update={"cursor_bytes": b"old"}),
            authorization,
            head("page"),
            head("cursor-authorization"),
        )


def assert_parser_lineage(
    selection: ParserSelectionRecord,
    result: ParserResultRecord,
    selection_head: Head,
    completed: tuple[str, ...],
) -> None:
    attempt = result.attempt
    if (
        selection.custody != attempt.custody
        or selection.expected_retry_head != attempt.exact_prior_retry_head
        or (
            selection.parser.attempt_id,
            selection.parser.parser_id,
            selection.parser.parser_version,
        )
        != (attempt.attempt_id, attempt.parser_id, attempt.parser_version)
        or result.selected_attempt_head != selection_head
        or len(completed) != len(set(completed))
        or attempt.attempt_id not in completed
    ):
        raise ValueError("parser lineage structural join differs")


def test_nonempty_parser_selection_result_and_quarantine_completion_reject_mutants() -> None:
    fixture = records()
    selection, result = fixture.parser_selection, fixture.parser_result
    assert_parser_lineage(selection, result, head("parser-selection"), ("attempt-1",))
    for result_mutant, completed in (
        (result.model_copy(update={"selected_attempt_head": head("other")}), ("attempt-1",)),
        (result, ("attempt-1", "attempt-1")),
        (result, ()),
    ):
        with pytest.raises(ValueError):
            assert_parser_lineage(selection, result_mutant, head("parser-selection"), completed)


def assert_fifo_drain(
    snapshot: IngressRuntimeSnapshot,
    rebase: BoundRebaseRecord,
    drain: DrainRecord,
    selected: ReadyGeneration,
) -> None:
    manifest = drain.manifest
    initial, replacement = manifest.bound_snapshots
    if (
        manifest.ready_fifo != tuple(x.generation for x in snapshot.ready)
        or manifest.bound_snapshots != snapshot.bounds
        or manifest.deficit_heads != tuple(x.head for x in snapshot.deficits)
        or manifest.all_tokens != tuple(x.current_head for x in snapshot.tokens)
        or manifest.quarantine_heads != tuple(x.custody_head for x in snapshot.quarantines)
        or manifest.scheduler_trace != snapshot.scheduler_trace
        or manifest.epoch != snapshot.epoch
        or manifest.fence != snapshot.fence
        or manifest.complete_inventory_fingerprint != snapshot.complete_inventory_fingerprint
        or initial.target != selected
        or initial.complete_ordered_predecessors != (manifest.ready_fifo[0],)
        or initial.epoch != snapshot.epoch
        or initial.canonical_class_order != ("ORDINARY", "TELEGRAM_PUSH")
        or rebase.rebase.blocked_generation != manifest.ready_fifo[0]
        or rebase.rebase.blocked_head != head("blocked-ordinary")
        or rebase.rebase.consumed_skip_slot != 10
        or rebase.rebase.replacement_descendant_bounds != (replacement,)
        or replacement.bound_lineage_id != initial.bound_lineage_id
        or replacement.origin_scheduler_slot != initial.origin_scheduler_slot
        or replacement.absolute_selection_deadline_slot != initial.absolute_selection_deadline_slot
    ):
        raise ValueError("FIFO/drain structural join differs")


def test_nonempty_fifo_reserve_rebase_deficit_epoch_and_drain_reject_mutants() -> None:
    fixture = records()
    drain, rebase, deficit = fixture.drain, fixture.bound_rebase, fixture.deficit
    ordinary, telegram = drain.manifest.ready_fifo
    snapshot = IngressRuntimeSnapshot(
        tenant_id="tenant",
        database_id="database",
        tenant_commit_sequence=4,
        journal_head=head("journal"),
        materialization_commitment=digest("materialization"),
        ingress_manifest=head("manifest"),
        epoch=head("epoch"),
        fence=7,
        state="DRAINING",
        tokens=(
            TokenRuntimeState(
                token=fixture.token.token,
                allocation=fixture.token.acquisition,
                current_head=head("token-record"),
                staged=None,
                custody=None,
            ),
        ),
        canonical_reserves=drain.manifest.canonical_reserves,
        ready=(
            AdmissionQueueEntry(
                source_class="ORDINARY", generation=ordinary, token_id="token", blocked_head=None
            ),
            AdmissionQueueEntry(
                source_class="TELEGRAM_PUSH", generation=telegram, token_id=None, blocked_head=None
            ),
        ),
        blocked=(
            AdmissionQueueEntry(
                source_class="ORDINARY",
                generation=ordinary,
                token_id="token",
                blocked_head=rebase.rebase.blocked_head,
            ),
        ),
        bounds=drain.manifest.bound_snapshots,
        deficits=(deficit.deficit,),
        scheduler_trace=drain.manifest.scheduler_trace,
        quarantines=(
            QuarantineRuntimeState(
                custody_head=head("quarantine-custody"),
                custody=QuarantineCustody(
                    token=head("token-record"),
                    raw_bytes=b"raw",
                    raw_digest=digest("raw"),
                    authentication=authentication(),
                    parser_id="parser",
                    parser_version="v1",
                    error_type="error",
                    inspection_retry_policy=head("policy"),
                    quarantine_budget=head("budget"),
                ),
                current_head=head("quarantine-current"),
                selected_attempt=fixture.parser_selection.parser,
                result=None,
                last_completed_attempt=None,
                selected_retry_head=head("retry-prior"),
                completed_attempt_ids=("attempt-1",),
            ),
        ),
        parser_remainder_fences=drain.manifest.parser_remainder_fences,
        settled_tokens=(),
        physical_occupancy=drain.manifest.physical_occupancy,
        poll_pages=(),
        poll_cursors=(),
        handoffs=(),
        drain=SelectedDrainSnapshot(head=head("drain"), manifest=drain.manifest),
        producer_quiescence=drain.manifest.producer_quiescence,
        complete_inventory_fingerprint=drain.manifest.complete_inventory_fingerprint,
    )
    assert (
        snapshot.canonical_reserves[0].source_class == "ORDINARY"
        and snapshot.canonical_reserves[1].source_class == "TELEGRAM_PUSH"
    )
    assert_fifo_drain(snapshot, rebase, drain, telegram)
    with pytest.raises(ValueError):
        assert_fifo_drain(
            snapshot.model_copy(update={"scheduler_trace": (head("changed"),)}),
            rebase,
            drain,
            telegram,
        )
    with pytest.raises(ValueError):
        assert_fifo_drain(
            snapshot,
            rebase,
            drain.model_copy(
                update={
                    "manifest": drain.manifest.model_copy(update={"epoch": head("changed-epoch")})
                }
            ),
            telegram,
        )
    with pytest.raises(ValueError):
        assert_fifo_drain(
            snapshot,
            rebase,
            drain.model_copy(
                update={
                    "manifest": drain.manifest.model_copy(
                        update={
                            "bound_snapshots": (
                                drain.manifest.bound_snapshots[0].model_copy(
                                    update={"canonical_class_order": ("TELEGRAM_PUSH", "ORDINARY")}
                                ),
                                drain.manifest.bound_snapshots[1],
                            )
                        }
                    )
                }
            ),
            telegram,
        )
    with pytest.raises(ValueError):
        assert_fifo_drain(
            snapshot,
            rebase,
            drain.model_copy(
                update={
                    "manifest": drain.manifest.model_copy(
                        update={
                            "bound_snapshots": (
                                drain.manifest.bound_snapshots[0],
                                drain.manifest.bound_snapshots[1].model_copy(
                                    update={"absolute_selection_deadline_slot": 15}
                                ),
                            )
                        }
                    )
                }
            ),
            telegram,
        )
    with pytest.raises(ValueError):
        assert_fifo_drain(
            snapshot,
            rebase.model_copy(
                update={
                    "rebase": rebase.rebase.model_copy(
                        update={
                            "replacement_descendant_bounds": (
                                drain.manifest.bound_snapshots[1].model_copy(
                                    update={"complete_ordered_predecessors": ()}
                                ),
                            )
                        }
                    )
                }
            ),
            drain,
            telegram,
        )
    with pytest.raises(ValueError):
        assert_fifo_drain(
            snapshot,
            rebase.model_copy(
                update={"rebase": rebase.rebase.model_copy(update={"consumed_skip_slot": 11})}
            ),
            drain,
            telegram,
        )
