"""Pure transition evidence only; no actual transport custody is claimed."""

from dataclasses import replace

import pytest

from chiplog.platform._ingress_contracts import (
    AdmissionBound,
    ClassReserve,
    CliAuthentication,
    DurableCustody,
    Head,
    LossObligation,
    PollMaterialized,
    PollMember,
    PollPageManifest,
    ProviderAuthentication,
    QuarantineCustody,
    QuarantineRetry,
    QuarantineTerminalProof,
    ReadyGeneration,
    ReceiptToken,
    RetainedRetry,
    SourceBinding,
    UnknownEndpoint,
)
from chiplog.platform._ingress_domain import (
    CustodySnapshot,
    DrainDeficit,
    DrainInventory,
    DrainQueueEntry,
    DrainQuiescence,
    IngressConflict,
    IngressHold,
    allocate_token,
    begin_drain,
    block_and_rebase,
    close_drain,
    complete_poll_page,
    custody_successor_head,
    drain_inventory_fingerprint,
    finish_quarantine_parser,
    publish_custody,
    quarantine_genesis,
    quiesce,
    select_quarantine_parser,
    selection_deadline,
    stage_raw,
    validate_reserve_accounting,
)


def _token(identity: str = "receipt") -> ReceiptToken:
    head = Head(identity="epoch", head="epoch-head", fingerprint="a" * 64)
    return ReceiptToken(
        token_id=identity,
        receive_slot=identity,
        maximum_bytes=3,
        predecessor=None,
        command_fingerprint="b" * 64,
        source=SourceBinding(
            manifest_row=head,
            source_class="CLI",
            tenant_id="tenant",
            database_id="database",
            source_identity="socket-frame",
            endpoint_account_binding=UnknownEndpoint(),
            broker_epoch="broker-epoch",
            broker_session="broker-session",
            admission_epoch=head,
            admission_fence=1,
            transport_version="unix.v1",
        ),
    )


def _snapshot() -> CustodySnapshot:
    return CustodySnapshot("epoch", "epoch-head", 1, "OPEN")


def test_quiesce_race_preserves_winner_and_rejects_later_destructive_slot() -> None:
    allocated = allocate_token(_snapshot(), _token(), "LOSS_SLOT")
    fenced = quiesce(allocated)
    assert allocate_token(fenced, _token(), "LOSS_SLOT") == fenced
    with pytest.raises(IngressHold):
        allocate_token(fenced, _token("later"), "LOSS_SLOT")
    with pytest.raises(IngressHold):
        begin_drain(fenced)
    loss = LossObligation(
        token=fenced.entries[0].state_head,
        receive_slot="receipt",
        custody_loss_cut="read-before-stage",
    )
    resolved = publish_custody(fenced, "receipt", loss)
    assert begin_drain(resolved).state == "DRAINING"
    assert publish_custody(resolved, "receipt", loss) == resolved
    with pytest.raises(IngressConflict):
        publish_custody(resolved, "receipt", loss.model_copy(update={"custody_loss_cut": "rival"}))


def test_raw_bytes_survive_non_utf8_and_changed_replay_never_rewrites() -> None:
    allocated = allocate_token(_snapshot(), _token(), "LOSS_SLOT")
    staged = stage_raw(allocated, "receipt", b"\xff\x00x")
    assert staged.entries[0].staged_bytes == b"\xff\x00x"
    assert stage_raw(staged, "receipt", b"\xff\x00x") == staged
    with pytest.raises(IngressConflict):
        stage_raw(staged, "receipt", b"new")
    with pytest.raises(IngressHold):
        stage_raw(allocated, "receipt", b"four")
    assert allocated.entries[0].staged_bytes is None


def test_missing_or_duplicate_authoritative_token_never_becomes_partial_result() -> None:
    with pytest.raises(IngressHold):
        stage_raw(_snapshot(), "receipt", b"x")
    allocated = allocate_token(_snapshot(), _token(), "LOSS_SLOT")
    corrupt = replace(allocated, entries=allocated.entries * 2)
    with pytest.raises(IngressHold):
        stage_raw(corrupt, "receipt", b"x")


def _bound() -> AdmissionBound:
    head = Head(identity="epoch", head="head", fingerprint="a" * 64)
    reserve = ClassReserve(
        source_class="CLI",
        byte_quantum=2,
        maximum_item_bytes=8,
        deficit_cap=8,
        physical_item_reserve=2,
        physical_byte_reserve=16,
        physical_quarantine_reserve=1,
        ready_depth_limit=2,
        descendant_snapshot_byte_limit=8192,
    )
    predecessor = ReadyGeneration(
        stable_work_id="first",
        durable_admission_commit_seq=1,
        stable_tie_identity="a",
        exact_head=head,
        accounted_bytes=3,
    )
    target = ReadyGeneration(
        stable_work_id="second",
        durable_admission_commit_seq=2,
        stable_tie_identity="b",
        exact_head=head,
        accounted_bytes=7,
    )
    return AdmissionBound(
        bound_id="bound",
        bound_lineage_id="lineage",
        epoch=head,
        fairness_version="v1",
        formula_version="v1",
        canonical_class_order=("CLI", "ORDINARY"),
        reserve=reserve,
        initial_deficit=1,
        origin_scheduler_slot=10,
        complete_ordered_predecessors=(predecessor,),
        target=target,
        absolute_selection_deadline_slot=20,
    )


def test_fifo_backlog_bound_counts_predecessor_service_and_rejects_overtaking() -> None:
    bound = _bound()
    # First item: one visit spends 3; second: four visits accrue 8 and spend 7.
    assert selection_deadline(bound) == (20, 1)
    reversed_bound = bound.model_copy(
        update={
            "complete_ordered_predecessors": (bound.target,),
            "target": bound.complete_ordered_predecessors[0],
        }
    )
    with pytest.raises(IngressHold):
        selection_deadline(reversed_bound)


def test_physical_reserve_boundary_does_not_consider_other_classes_capacity() -> None:
    reserve = _bound().reserve
    validate_reserve_accounting(reserve, 2, 16, 1)
    for accounting in ((3, 16, 1), (2, 17, 1), (2, 16, 2), (-1, 0, 0)):
        with pytest.raises(IngressHold):
            validate_reserve_accounting(reserve, *accounting)


def test_custody_rejects_each_token_head_component_substitution() -> None:
    snapshot = allocate_token(_snapshot(), _token(), "LOSS_SLOT")
    exact = snapshot.entries[0].state_head
    for mutation in ({"identity": "rival"}, {"head": "rival"}, {"fingerprint": "c" * 64}):
        loss = LossObligation(
            token=exact.model_copy(update=mutation),
            receive_slot="receipt",
            custody_loss_cut="cut",
        )
        with pytest.raises(IngressConflict):
            publish_custody(snapshot, "receipt", loss)
    staged = stage_raw(snapshot, "receipt", b"x")
    with pytest.raises(IngressConflict):
        publish_custody(
            staged,
            "receipt",
            LossObligation(
                token=exact,
                receive_slot="receipt",
                custody_loss_cut="cut",
            ),
        )


def test_retained_retry_cannot_substitute_proof_identity_head_or_fingerprint() -> None:
    proof = Head(identity="proof", head="proof-head", fingerprint="c" * 64)
    snapshot = allocate_token(_snapshot(), _token(), "RETAINED_SOURCE", proof)
    for mutation in ({"identity": "rival"}, {"head": "rival"}, {"fingerprint": "d" * 64}):
        retry = RetainedRetry(
            token=snapshot.entries[0].state_head,
            continuing_retention_proof=proof.model_copy(update=mutation),
        )
        with pytest.raises(IngressHold):
            publish_custody(snapshot, "receipt", retry)


def test_block_rebase_preserves_lineage_origin_deadline_and_complete_descendants() -> None:
    descendant = _bound()
    first = descendant.model_copy(
        update={
            "bound_id": "first-bound",
            "bound_lineage_id": "first-lineage",
            "target": descendant.complete_ordered_predecessors[0],
            "complete_ordered_predecessors": (),
            "absolute_selection_deadline_slot": 12,
        }
    )
    fifo = (first.target, descendant.target)
    rebased = block_and_rebase(fifo, (first, descendant), first.target.exact_head, 1, 10)
    assert rebased.resulting_deficit == 3  # skip accrues credit, consumes no blocked bytes
    assert len(rebased.closed_descendant_bounds) == 2
    replacement = rebased.replacement_descendant_bounds[0]
    assert replacement.complete_ordered_predecessors == ()
    assert replacement.bound_lineage_id == descendant.bound_lineage_id
    assert replacement.origin_scheduler_slot == 10
    assert replacement.absolute_selection_deadline_slot == 20
    assert replacement.evaluation_scheduler_slot == 11
    assert selection_deadline(replacement)[0] <= replacement.absolute_selection_deadline_slot
    assert block_and_rebase(fifo, (first, descendant), first.target.exact_head, 1, 10) == rebased
    for bounds in ((first,), (descendant, first)):
        with pytest.raises(IngressHold):
            block_and_rebase(fifo, bounds, first.target.exact_head, 1, 10)
    with pytest.raises(IngressHold):
        block_and_rebase(fifo, (first, descendant), first.target.exact_head, 1, 19)


def _authentication() -> CliAuthentication:
    import hashlib

    head = Head(identity="proof", head="head", fingerprint="a" * 64)
    return CliAuthentication(
        proof=head,
        source=_token().source,
        raw_digest=hashlib.sha256(b"raw").hexdigest(),
        principal_contour=head,
        freshness=head,
        replay_identity="replay",
        original_subject="subject",
        contract_version="v1",
        canonicalization_version="v1",
        unix_endpoint=head,
        os_peer=head,
        authenticated_cli_session=head,
        credential_binding=head,
        policy_head=head,
    )


def test_poll_cursor_requires_full_ordered_raw_members_not_successfully_parsed_subset() -> None:
    head = Head(identity="page", head="head", fingerprint="a" * 64)
    members = (
        PollMember(identity="first", digest="a" * 64),
        PollMember(identity="second", digest="b" * 64),
    )
    cli = _authentication()
    auth = ProviderAuthentication(
        proof=cli.proof,
        source=cli.source.model_copy(update={"source_class": "PROVIDER_POLL"}),
        raw_digest=cli.raw_digest,
        principal_contour=cli.principal_contour,
        freshness=cli.freshness,
        replay_identity=cli.replay_identity,
        original_subject=cli.original_subject,
        contract_version=cli.contract_version,
        canonicalization_version=cli.canonicalization_version,
        provider_identity="provider",
        account_id="account",
        endpoint_binding=head,
        credential_key=head,
        audience="audience",
    )
    page = PollPageManifest(
        page_id="page",
        authentication=auth,
        transport_contract="DURABLE_CURSOR_BEFORE_REQUEST",
        request_attempt=head,
        predecessor_cursor=head,
        candidate_cursor=b"next",
        raw_page_digest=auth.raw_digest,
        complete_ordered_members=members,
        manifest_digest="d" * 64,
        schema_version="v1",
        canonicalization_version="v1",
    )
    dispositions = tuple(PollMaterialized(member=member, inbox=head) for member in members)
    assert complete_poll_page(page, dispositions) == b"next"
    for substituted in (
        page.model_copy(update={"raw_page_digest": "f" * 64}),
        page.model_copy(
            update={
                "authentication": auth.model_copy(update={"raw_digest": "f" * 64}),
            }
        ),
    ):
        with pytest.raises(IngressHold, match="raw page digest"):
            complete_poll_page(substituted, dispositions)
    for partial in (dispositions[:1], tuple(reversed(dispositions)), dispositions * 2):
        with pytest.raises(IngressHold):
            complete_poll_page(page, partial)


def test_quarantine_parser_cas_preserves_raw_bytes_and_terminal_cannot_reopen() -> None:
    auth = _authentication()
    head = Head(identity="custody", head="head", fingerprint="a" * 64)
    custody = QuarantineCustody(
        token=head,
        raw_bytes=b"raw",
        raw_digest=auth.raw_digest,
        authentication=auth,
        parser_id="parser",
        parser_version="old",
        error_type="unknown",
        inspection_retry_policy=head,
        quarantine_budget=head,
    )
    head = custody_successor_head(custody)
    original = quarantine_genesis(head, custody)
    selected = select_quarantine_parser(original, original.current_head, "attempt1", "parser", "v1")
    assert selected.current_head != original.current_head
    with pytest.raises(IngressConflict):
        select_quarantine_parser(selected, original.current_head, "rival", "parser", "v2")
    retry = QuarantineRetry(
        custody=head,
        failure_or_unknown="unknown",
        next_parser_constraints=head,
    )
    held = finish_quarantine_parser(selected, ("attempt1", "parser", "v1"), retry)
    assert held.custody == original.custody
    assert finish_quarantine_parser(held, ("attempt1", "parser", "v1"), retry) == held
    with pytest.raises(IngressConflict):
        select_quarantine_parser(held, held.current_head, "attempt1", "parser", "v2")
    selected2 = select_quarantine_parser(held, held.current_head, "attempt2", "parser", "v2")
    terminal = QuarantineTerminalProof(
        custody=head,
        byte_level_proof_kind="registered-empty-evidence",
        proof_version="v1",
        proof_digest="e" * 64,
    )
    closed = finish_quarantine_parser(selected2, ("attempt2", "parser", "v2"), terminal)
    assert closed.custody.raw_bytes == b"raw"
    with pytest.raises(IngressConflict):
        select_quarantine_parser(closed, closed.current_head, "attempt3", "parser", "v3")


def _drain_inventory() -> DrainInventory:
    allocated = allocate_token(_snapshot(), _token(), "LOSS_SLOT")
    loss = LossObligation(
        token=allocated.entries[0].state_head,
        receive_slot="receipt",
        custody_loss_cut="cut",
    )
    resolved = publish_custody(allocated, "receipt", loss)
    draining = begin_drain(quiesce(resolved))
    classes = (
        "TELEGRAM_PUSH",
        "TELEGRAM_POLL",
        "CLI",
        "PROVIDER_CALLBACK",
        "PROVIDER_POLL",
        "RECONCILIATION",
        "TOOL_RESULT",
        "ORDINARY",
    )
    reserves = tuple(
        ClassReserve.model_validate(
            {
                **_bound().reserve.model_dump(),
                "source_class": source,
            }
        )
        for source in classes
    )
    proof = Head(identity="completed", head="head", fingerprint="a" * 64)
    return DrainInventory(
        custody=draining,
        epoch=_token().source.admission_epoch,
        canonical_reserves=reserves,
        ready=(),
        blocked=(),
        bounds=(),
        deficits=tuple(DrainDeficit(source, proof, 0) for source in classes),
        scheduler_trace=(),
        quarantines=(),
        parser_remainder_fences=(),
        settled_tokens=((allocated.entries[0].state_head, proof),),
    )


def _quiescence(inventory: DrainInventory) -> DrainQuiescence:
    return DrainQuiescence(
        inventory.epoch,
        inventory.custody.fence,
        drain_inventory_fingerprint(inventory),
        Head(identity="producer-proof", head="proof", fingerprint="a" * 64),
    )


def test_closed_drain_requires_complete_work_token_reserve_and_proof_joins() -> None:
    inventory = _drain_inventory()
    proof = _quiescence(inventory)
    manifest = close_drain(inventory, proof)
    assert manifest.state == "CLOSED"
    assert len(manifest.all_tokens) == len(manifest.all_successors) == 1
    assert close_drain(inventory, proof).model_dump_json() == manifest.model_dump_json()
    for changed in (
        replace(inventory, settled_tokens=()),
        replace(inventory, settled_tokens=inventory.settled_tokens * 2),
        replace(inventory, canonical_reserves=inventory.canonical_reserves[:-1]),
        replace(inventory, deficits=inventory.deficits[:-1]),
        replace(
            inventory, custody=replace(inventory.custody, entries=inventory.custody.entries * 2)
        ),
    ):
        with pytest.raises(IngressHold):
            close_drain(changed, _quiescence(changed))
    changed = replace(inventory, scheduler_trace=(proof.issued_proof,))
    with pytest.raises(IngressConflict):
        close_drain(changed, proof)


def test_drain_joins_ready_inbox_and_exact_bound_and_never_runs_blocked_bound() -> None:
    inventory = _drain_inventory()
    allocated = stage_raw(allocate_token(_snapshot(), _token(), "LOSS_SLOT"), "receipt", b"raw")
    inbox = Head(identity="inbox", head="inbox-head", fingerprint="b" * 64)
    custody = DurableCustody(
        token=allocated.entries[0].state_head,
        raw_bytes=b"raw",
        raw_digest=_authentication().raw_digest,
        authentication=_authentication(),
        inbox=inbox,
    )
    snapshot = begin_drain(quiesce(publish_custody(allocated, "receipt", custody)))
    generation = ReadyGeneration(
        stable_work_id="work",
        durable_admission_commit_seq=1,
        stable_tie_identity="first",
        exact_head=inbox,
        accounted_bytes=3,
    )
    queue = DrainQueueEntry("CLI", generation, "receipt")
    bound = _bound().model_copy(
        update={
            "target": generation,
            "complete_ordered_predecessors": (),
            "epoch": inventory.epoch,
            "reserve": inventory.canonical_reserves[2],
            "canonical_class_order": tuple(
                row.source_class for row in inventory.canonical_reserves
            ),
            "origin_scheduler_slot": 0,
            "initial_deficit": 0,
            "absolute_selection_deadline_slot": 16,
        }
    )
    ready = replace(
        inventory,
        custody=snapshot,
        ready=(queue,),
        bounds=(bound,),
        settled_tokens=(),
        physical_occupancy=(snapshot.entries[0].state_head,),
    )
    assert close_drain(ready, _quiescence(ready)).ready_fifo == (generation,)
    for changed in (
        replace(ready, bounds=()),
        replace(
            ready,
            bounds=(
                bound.model_copy(
                    update={
                        "absolute_selection_deadline_slot": 0,
                    }
                ),
            ),
        ),
        replace(
            ready,
            bounds=(
                bound.model_copy(
                    update={
                        "initial_deficit": bound.reserve.deficit_cap + 1,
                    }
                ),
            ),
        ),
        replace(
            ready,
            bounds=(
                bound.model_copy(
                    update={
                        "evaluation_scheduler_slot": 2**64 - 1,
                    }
                ),
            ),
        ),
        replace(ready, ready=(), blocked=(replace(queue, blocked_head=inbox),)),
        replace(ready, ready=(replace(queue, source_class="PROVIDER_CALLBACK"),)),
        replace(
            ready,
            ready=(
                replace(
                    queue,
                    generation=generation.model_copy(
                        update={
                            "exact_head": inbox.model_copy(update={"head": "rival"}),
                        }
                    ),
                ),
            ),
        ),
    ):
        with pytest.raises(IngressHold):
            close_drain(changed, _quiescence(changed))
    bad_custody = custody.model_copy(
        update={
            "authentication": custody.authentication.model_copy(
                update={"raw_digest": "f" * 64},
            )
        }
    )
    corrupted = replace(
        ready,
        custody=replace(
            snapshot,
            entries=(
                replace(
                    snapshot.entries[0],
                    custody=bad_custody,
                ),
            ),
        ),
    )
    with pytest.raises(IngressConflict, match="immutable local custody"):
        close_drain(corrupted, _quiescence(corrupted))


def test_selected_parser_drain_requires_exact_durable_remainder_and_old_producer_fence() -> None:
    inventory = _drain_inventory()
    allocated = stage_raw(allocate_token(_snapshot(), _token(), "LOSS_SLOT"), "receipt", b"raw")
    head = inventory.epoch
    custody = QuarantineCustody(
        token=allocated.entries[0].state_head,
        raw_bytes=b"raw",
        raw_digest=_authentication().raw_digest,
        authentication=_authentication(),
        parser_id="parser",
        parser_version="old",
        error_type="unknown",
        inspection_retry_policy=head,
        quarantine_budget=head,
    )
    snapshot = begin_drain(quiesce(publish_custody(allocated, "receipt", custody)))
    state = quarantine_genesis(custody_successor_head(custody), custody)
    state = select_quarantine_parser(state, state.current_head, "attempt", "parser", "v1")
    pending = replace(
        inventory,
        custody=snapshot,
        settled_tokens=(),
        quarantines=(state,),
        physical_occupancy=(snapshot.entries[0].state_head,),
    )
    with pytest.raises(IngressHold):
        close_drain(pending, _quiescence(pending))
    fenced = replace(
        pending,
        parser_remainder_fences=(
            (
                state.current_head,
                Head(
                    identity="attempt",
                    head="old-producer-fenced",
                    fingerprint="c" * 64,
                ),
            ),
        ),
    )
    assert close_drain(fenced, _quiescence(fenced)).state == "CLOSED"
    bad_custody = custody.model_copy(
        update={
            "authentication": custody.authentication.model_copy(
                update={"raw_digest": "f" * 64},
            )
        }
    )
    corrupt_state = replace(
        state, custody=bad_custody, custody_head=custody_successor_head(bad_custody)
    )
    corrupted = replace(
        fenced,
        custody=replace(
            snapshot,
            entries=(
                replace(
                    snapshot.entries[0],
                    custody=bad_custody,
                ),
            ),
        ),
        quarantines=(corrupt_state,),
    )
    with pytest.raises(IngressConflict, match="immutable local custody"):
        close_drain(corrupted, _quiescence(corrupted))
    wrong = replace(
        fenced,
        parser_remainder_fences=(
            (
                state.current_head,
                Head(
                    identity="another-attempt",
                    head="old-producer-fenced",
                    fingerprint="c" * 64,
                ),
            ),
        ),
    )
    with pytest.raises(IngressHold):
        close_drain(wrong, _quiescence(wrong))
