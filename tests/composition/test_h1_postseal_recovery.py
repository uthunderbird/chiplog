"""Pure recovery-record state machine; storage/mount authority is deliberately absent."""

from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from typing import Literal

import pytest

from chiplog.composition.h1_postseal_recovery import (
    H1PostSealRecoveryJournal,
    H1PostSealRecoveryRecordError,
    H1PostSealRecoveryRecordV2,
    H1PostSealRecoveryRootV1,
    H1PostSealRecoveryState,
    H1PostSealRecoveryTransition,
    H1PostSealRecoveryUnavailable,
    apply_authenticated_prefix,
    denied_production_recovery_journal,
    scan_authenticated_prefix,
)


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _root() -> H1PostSealRecoveryRootV1:
    return H1PostSealRecoveryRootV1(
        tenant_id="tenant",
        database_id="database",
        database_identity=("/canonical/database.sqlite", 12, 34),
        journal_instance_id="h1-postseal-recovery",
        selected_seal_subject_id="seal",
        selected_seal_head="record:" + "a" * 64,
        selected_seal_fingerprint="a" * 64,
        selected_decision_id="b" * 64,
        selected_decision_digest="c" * 64,
        selected_run_head="loop:" + "d" * 64,
        original_command_id="original-command",
        original_command_fingerprint="e" * 64,
        source_commitment="f" * 64,
        publication_command_id="publication-command",
        publication_command_fingerprint="0" * 64,
    )


def _other_root() -> H1PostSealRecoveryRootV1:
    return _root().model_copy(
        selected_seal_subject_id="other-seal",
        selected_seal_head="record:" + "1" * 64,
        selected_seal_fingerprint="1" * 64,
        selected_decision_id="2" * 64,
        selected_decision_digest="3" * 64,
        selected_run_head="loop:" + "4" * 64,
        original_command_id="other-original-command",
        original_command_fingerprint="5" * 64,
        source_commitment="6" * 64,
        publication_command_id="other-publication-command",
        publication_command_fingerprint="7" * 64,
    )


def test_authenticated_prefix_round_trips_large_semantic_input_and_rejects_wrong_digest() -> None:
    root = _root()
    root_record = H1PostSealRecoveryTransition.begin(H1PostSealRecoveryState.empty(root))
    state = apply_authenticated_prefix((('entry-0', None, root_record.canonical_bytes()),), root)
    semantic_input = b"x" * 2_123_366
    input_record = H1PostSealRecoveryTransition.pin_input(
        state, stage="COMPLETION", semantic_input=semantic_input
    )

    recovered = apply_authenticated_prefix(
        (
            ("entry-0", None, root_record.canonical_bytes()),
            ("entry-1", "entry-0", input_record.canonical_bytes()),
        ),
        root,
    )
    assert len(semantic_input) > 256 * 1024
    assert recovered.stage_input("COMPLETION") == (semantic_input, None)

    wrong_digest = json.loads(input_record.canonical_bytes())
    wrong_digest["semantic_input_digest"] = "0" * 64
    malformed_record = json.dumps(
        wrong_digest, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    with pytest.raises(H1PostSealRecoveryRecordError, match="recovery record is invalid"):
        apply_authenticated_prefix(
            (
                ("entry-0", None, root_record.canonical_bytes()),
                ("entry-1", "entry-0", malformed_record),
            ),
            root,
        )


def test_root_requires_the_installed_loop_head_shape() -> None:
    assert _root().selected_run_head == "loop:" + "d" * 64
    with pytest.raises(H1PostSealRecoveryRecordError, match="selected_run_head"):
        _root().model_copy(selected_run_head="d" * 64)


def test_v1_root_and_stage_wire_fixtures_remain_stable_and_mean_local_v2() -> None:
    root = _root()
    v1_root = H1PostSealRecoveryTransition.begin(H1PostSealRecoveryState.empty(root))
    state = apply_authenticated_prefix((("root-entry", None, v1_root.canonical_bytes()),), root)
    stage = H1PostSealRecoveryTransition.pin_input(
        state, stage="COMPLETION", semantic_input=b"fixture-input"
    )

    assert _digest(v1_root.canonical_bytes()) == (
        "1f4c7148ce368dbcd38beff18de22ea3a5497d30268f42cf8fb587cbdc14d37a"
    )
    assert _digest(stage.canonical_bytes()) == (
        "092d678e9b977e32527119b53e3d6f4a393c8230714ef397748bcb7c09a784cc"
    )
    assert state.selected_producer == "LOCAL_V2"
    assert state.root_record_identity == "root-entry"
    assert state.root_record_bytes == v1_root.canonical_bytes()
    assert state.root_evidence is not None
    assert state.root_evidence.canonical_bytes == v1_root.canonical_bytes()


@pytest.mark.parametrize("producer_choice", ("LOCAL_V2", "SCOPED_V3"))
def test_v2_root_choice_and_evidence_survive_scan_and_stage_replay(
    producer_choice: Literal["LOCAL_V2", "SCOPED_V3"],
) -> None:
    root = _root()
    root_record = H1PostSealRecoveryTransition.begin_selected(
        H1PostSealRecoveryState.empty(root), producer_choice=producer_choice
    )
    assert type(root_record) is H1PostSealRecoveryRecordV2
    assert root_record.root_id == root.root_id()
    entries = (("root-entry", None, root_record.canonical_bytes()),)
    state = apply_authenticated_prefix(entries, root)
    stage = H1PostSealRecoveryTransition.pin_input(
        state, stage="COMPLETION", semantic_input=b"v2-root-stage"
    )
    replayed = scan_authenticated_prefix(
        (*entries, ("stage-entry", "root-entry", stage.canonical_bytes())),
        tenant_id="tenant",
        journal_instance_id="h1-postseal-recovery",
    ).state_for_root(root.root_id())
    reopened = scan_authenticated_prefix(
        (*entries, ("stage-entry", "root-entry", stage.canonical_bytes())),
        tenant_id="tenant",
        journal_instance_id="h1-postseal-recovery",
    ).state_for_root(root.root_id())

    assert replayed.selected_producer == producer_choice
    assert replayed.root_record_identity == "root-entry"
    assert replayed.root_record_bytes == root_record.canonical_bytes()
    assert replayed.root_evidence == reopened.root_evidence
    assert replayed.stage_input("COMPLETION") == (b"v2-root-stage", None)


def test_v2_root_rejects_missing_extra_duplicate_and_invalid_choice() -> None:
    root_record = H1PostSealRecoveryTransition.begin_selected(
        H1PostSealRecoveryState.empty(_root()), producer_choice="SCOPED_V3"
    )
    decoded = json.loads(root_record.canonical_bytes())
    malformed: list[bytes] = []
    missing = dict(decoded)
    del missing["producer_choice"]
    malformed.append(json.dumps(missing, sort_keys=True, separators=(",", ":")).encode())
    extra = dict(decoded, unexpected="value")
    malformed.append(json.dumps(extra, sort_keys=True, separators=(",", ":")).encode())
    invalid_choice = dict(decoded, producer_choice="UNAUTHORISED")
    malformed.append(json.dumps(invalid_choice, sort_keys=True, separators=(",", ":")).encode())
    malformed.append(
        root_record.canonical_bytes().replace(
            b'"producer_choice":"SCOPED_V3"',
            b'"producer_choice":"SCOPED_V3","producer_choice":"LOCAL_V2"',
        )
    )

    for raw in malformed:
        with pytest.raises(H1PostSealRecoveryRecordError):
            scan_authenticated_prefix(
                (("root-entry", None, raw),),
                tenant_id="tenant",
                journal_instance_id="h1-postseal-recovery",
            )


def test_same_selected_seal_with_competing_v2_choice_is_rejected() -> None:
    root = _root()
    local = H1PostSealRecoveryTransition.begin_selected(
        H1PostSealRecoveryState.empty(root), producer_choice="LOCAL_V2"
    )
    scoped = H1PostSealRecoveryTransition.begin_selected(
        H1PostSealRecoveryState.empty(root.model_copy(source_commitment="8" * 64)),
        producer_choice="SCOPED_V3",
    )

    with pytest.raises(H1PostSealRecoveryRecordError, match="competing recovery roots"):
        scan_authenticated_prefix(
            (
                ("local", None, local.canonical_bytes()),
                ("scoped", "local", scoped.canonical_bytes()),
            ),
            tenant_id="tenant",
            journal_instance_id="h1-postseal-recovery",
        )


def test_journal_rejects_competing_choice_before_second_append() -> None:
    class Gate:
        held = False

        def hold(self) -> Gate:
            return self

        def __enter__(self) -> None:
            self.held = True

        def __exit__(self, *_: object) -> None:
            self.held = False

        def require_held(self) -> None:
            assert self.held

    class Mount:
        tenant_id = "tenant"
        journal_instance_id = "h1-postseal-recovery"
        authority_gate = Gate()

        def assert_current(self) -> None:
            return None

    class Journal:
        def __init__(self) -> None:
            self.records: list[tuple[str, str | None, bytes]] = []
            self.append_calls = 0

        def entries(self) -> tuple[tuple[str, str | None, bytes], ...]:
            return tuple(self.records)

        def append(self, raw: bytes, predecessor: str | None) -> str:
            self.append_calls += 1
            entry_id = f"entry-{self.append_calls}"
            self.records.append((entry_id, predecessor, raw))
            return entry_id

        def close(self) -> None:
            return None

    root = _root()
    local = H1PostSealRecoveryTransition.begin_selected(
        H1PostSealRecoveryState.empty(root), producer_choice="LOCAL_V2"
    )
    scoped = H1PostSealRecoveryTransition.begin_selected(
        H1PostSealRecoveryState.empty(root.model_copy(source_commitment="8" * 64)),
        producer_choice="SCOPED_V3",
    )
    journal_backend = Journal()
    journal = H1PostSealRecoveryJournal(mount=Mount(), journal=journal_backend)  # type: ignore[arg-type]
    first = journal.append_transition(local, expected_global_tip=None)

    with pytest.raises(H1PostSealRecoveryRecordError, match="selected seal has a recovery root"):
        journal.append_transition(scoped, expected_global_tip=first.entry_id)
    assert journal_backend.append_calls == 1


def test_effects_input_pins_inner_command_id_and_exact_semantic_bytes_across_restart() -> None:
    root = _root()
    state = H1PostSealRecoveryState.empty(root)
    root_record = H1PostSealRecoveryTransition.begin(state)
    state = apply_authenticated_prefix((("entry-0", None, root_record.canonical_bytes()),), root)

    completion = H1PostSealRecoveryTransition.pin_input(
        state, stage="COMPLETION", semantic_input=b'{"completion":1}'
    )
    state = apply_authenticated_prefix(
        (
            ("entry-0", None, root_record.canonical_bytes()),
            ("entry-1", "entry-0", completion.canonical_bytes()),
        ),
        root,
    )
    completion_result = H1PostSealRecoveryTransition.commit_result(
        state, stage="COMPLETION", result_bytes=b'{"prepared":1}'
    )
    conversation = H1PostSealRecoveryTransition.pin_input(
        apply_authenticated_prefix(
            (
                ("entry-0", None, root_record.canonical_bytes()),
                ("entry-1", "entry-0", completion.canonical_bytes()),
                ("entry-2", "entry-1", completion_result.canonical_bytes()),
            ),
            root,
        ),
        stage="CONVERSATION",
        semantic_input=b'{"conversation":1}',
    )
    conversation_result = H1PostSealRecoveryTransition.commit_result(
        apply_authenticated_prefix(
            (
                ("entry-0", None, root_record.canonical_bytes()),
                ("entry-1", "entry-0", completion.canonical_bytes()),
                ("entry-2", "entry-1", completion_result.canonical_bytes()),
                ("entry-3", "entry-2", conversation.canonical_bytes()),
            ),
            root,
        ),
        stage="CONVERSATION",
        result_bytes=b'{"conversation-result":1}',
    )
    effects = H1PostSealRecoveryTransition.pin_input(
        apply_authenticated_prefix(
            (
                ("entry-0", None, root_record.canonical_bytes()),
                ("entry-1", "entry-0", completion.canonical_bytes()),
                ("entry-2", "entry-1", completion_result.canonical_bytes()),
                ("entry-3", "entry-2", conversation.canonical_bytes()),
                ("entry-4", "entry-3", conversation_result.canonical_bytes()),
            ),
            root,
        ),
        stage="EFFECTS",
        semantic_input=b'{"inner":"PrepareH1LocalCommentaryV1"}',
        effects_command_id="effects-command-id",
    )

    recovered = apply_authenticated_prefix(
        (
            ("entry-0", None, root_record.canonical_bytes()),
            ("entry-1", "entry-0", completion.canonical_bytes()),
            ("entry-2", "entry-1", completion_result.canonical_bytes()),
            ("entry-3", "entry-2", conversation.canonical_bytes()),
            ("entry-4", "entry-3", conversation_result.canonical_bytes()),
            ("entry-5", "entry-4", effects.canonical_bytes()),
        ),
        root,
    )

    assert recovered.stage_input("EFFECTS") == (
        b'{"inner":"PrepareH1LocalCommentaryV1"}',
        "effects-command-id",
    )
    assert recovered.next_stage == "EFFECTS"


def test_selected_root_entries_pin_durable_ids_bytes_and_reject_tampered_prefix() -> None:
    """The enrolled reader retains records, rather than lossy stage-state tuples."""

    class Gate:
        @contextmanager
        def hold(self):
            yield

        def require_held(self) -> None:
            return None

    class Mount:
        tenant_id = "tenant"
        journal_instance_id = "h1-postseal-recovery"
        authority_gate = Gate()

        def assert_current(self) -> None:
            return None

    class Journal:
        def __init__(self) -> None:
            self.records: list[tuple[str, str | None, bytes]] = []

        def entries(self) -> tuple[tuple[str, str | None, bytes], ...]:
            return tuple(self.records)

        def append(self, raw: bytes, predecessor: str | None) -> str:
            entry_id = "entry-" + str(len(self.records))
            self.records.append((entry_id, predecessor, raw))
            return entry_id

        def close(self) -> None:
            return None

    mount, backend = Mount(), Journal()
    journal = H1PostSealRecoveryJournal(mount=mount, journal=backend)  # type: ignore[arg-type]
    root = _root()
    state = H1PostSealRecoveryState.empty(root)
    root_receipt = journal.append_transition(
        H1PostSealRecoveryTransition.begin_selected(state, producer_choice="SCOPED_V3"),
        expected_global_tip=None,
    )
    state = root_receipt.scan.state_for_root(root.root_id())
    completion_input = H1PostSealRecoveryTransition.pin_input(
        state, stage="COMPLETION", semantic_input=b'{"completion":"input"}'
    )
    completion_input_receipt = journal.append_transition(
        completion_input, expected_global_tip=root_receipt.entry_id
    )
    state = completion_input_receipt.scan.state_for_root(root.root_id())
    completion_result = H1PostSealRecoveryTransition.commit_result(
        state, stage="COMPLETION", result_bytes=b'{"completion":"result"}'
    )
    completion_result_receipt = journal.append_transition(
        completion_result, expected_global_tip=completion_input_receipt.entry_id
    )
    state = completion_result_receipt.scan.state_for_root(root.root_id())
    conversation_input = H1PostSealRecoveryTransition.pin_input(
        state, stage="CONVERSATION", semantic_input=b'{"conversation":"input"}'
    )
    conversation_input_receipt = journal.append_transition(
        conversation_input, expected_global_tip=completion_result_receipt.entry_id
    )
    state = conversation_input_receipt.scan.state_for_root(root.root_id())
    conversation_result = H1PostSealRecoveryTransition.commit_result(
        state, stage="CONVERSATION", result_bytes=b'{"conversation":"result"}'
    )
    conversation_result_receipt = journal.append_transition(
        conversation_result, expected_global_tip=conversation_input_receipt.entry_id
    )

    reopened = H1PostSealRecoveryJournal(mount=mount, journal=backend)  # type: ignore[arg-type]
    with mount.authority_gate.hold():
        selected = reopened._read_selected_root_entries_held(root.root_id())
    assert [(entry.entry_id, entry.canonical_bytes) for entry in selected.records] == [
        (root_receipt.entry_id, root_receipt.scan.state_for_root(root.root_id()).root_record_bytes),
        (completion_input_receipt.entry_id, completion_input.canonical_bytes()),
        (completion_result_receipt.entry_id, completion_result.canonical_bytes()),
        (conversation_input_receipt.entry_id, conversation_input.canonical_bytes()),
        (conversation_result_receipt.entry_id, conversation_result.canonical_bytes()),
    ]

    tampered_id, predecessor, raw = backend.records[2]
    backend.records[2] = (tampered_id, predecessor, raw.replace(b"result", b"tamper"))
    with mount.authority_gate.hold(), pytest.raises(H1PostSealRecoveryRecordError):
        reopened._read_selected_root_entries_held(root.root_id())
    backend.records[2] = (tampered_id, predecessor, raw)

    removed = backend.records.pop(2)
    with mount.authority_gate.hold(), pytest.raises(H1PostSealRecoveryRecordError):
        reopened._read_selected_root_entries_held(root.root_id())
    backend.records.insert(2, removed)

    rival = completion_input.model_copy(
        predecessor_entry_id=conversation_result_receipt.entry_id,
        semantic_input=b'{"completion":"rival"}',
        semantic_input_digest=_digest(b'{"completion":"rival"}'),
    )
    backend.records.append(
        ("rival-entry", conversation_result_receipt.entry_id, rival.canonical_bytes())
    )
    with mount.authority_gate.hold(), pytest.raises(H1PostSealRecoveryRecordError):
        reopened._read_selected_root_entries_held(root.root_id())


def test_prefix_rejects_wrong_root_rival_pin_and_noncanonical_payload() -> None:
    root = _root()
    root_record = H1PostSealRecoveryTransition.begin(H1PostSealRecoveryState.empty(root))
    state = apply_authenticated_prefix((("entry-0", None, root_record.canonical_bytes()),), root)
    pin = H1PostSealRecoveryTransition.pin_input(state, stage="COMPLETION", semantic_input=b"input")
    rival = pin.model_copy(
        predecessor_entry_id="entry-1",
        semantic_input=b"other",
        semantic_input_digest=_digest(b"other"),
    )

    with pytest.raises(H1PostSealRecoveryRecordError, match="rival stage input"):
        apply_authenticated_prefix(
            (
                ("entry-0", None, root_record.canonical_bytes()),
                ("entry-1", "entry-0", pin.canonical_bytes()),
                ("entry-2", "entry-1", rival.canonical_bytes()),
            ),
            root,
        )

    malformed = root_record.canonical_bytes().replace(b'"kind":"ROOT"', b'"kind": "ROOT"')
    with pytest.raises(H1PostSealRecoveryRecordError, match="canonical"):
        apply_authenticated_prefix((("entry-0", None, malformed),), root)

    with pytest.raises(H1PostSealRecoveryRecordError, match="recovery root"):
        apply_authenticated_prefix(
            (("entry-0", None, root_record.canonical_bytes()),),
            _root().model_copy(tenant_id="other"),
        )


def test_global_prefix_interleaves_two_roots_and_a_bad_other_root_blocks_restart() -> None:
    first, second = _root(), _other_root()
    first_root = H1PostSealRecoveryTransition.begin(H1PostSealRecoveryState.empty(first))
    second_root = H1PostSealRecoveryTransition.begin(H1PostSealRecoveryState.empty(second))
    first_state = apply_authenticated_prefix(
        (("a-root", None, first_root.canonical_bytes()),), first
    )
    second_state = apply_authenticated_prefix(
        (("b-root", None, second_root.canonical_bytes()),), second
    )
    first_input = H1PostSealRecoveryTransition.pin_input(
        first_state, stage="COMPLETION", semantic_input=b"a-input"
    )
    second_input = H1PostSealRecoveryTransition.pin_input(
        second_state, stage="COMPLETION", semantic_input=b"b-input"
    )
    entries = (
        ("a-root", None, first_root.canonical_bytes()),
        ("b-root", "a-root", second_root.canonical_bytes()),
        ("a-input", "b-root", first_input.canonical_bytes()),
        ("b-input", "a-input", second_input.canonical_bytes()),
    )

    recovered = scan_authenticated_prefix(
        entries, tenant_id="tenant", journal_instance_id="h1-postseal-recovery"
    )
    assert recovered.tip == "b-input"
    assert recovered.state_for_root(first.root_id()).head == "a-input"
    assert recovered.state_for_root(second.root_id()).head == "b-input"

    first_result = H1PostSealRecoveryTransition.commit_result(
        recovered.state_for_root(first.root_id()), stage="COMPLETION", result_bytes=b"a-result"
    )
    restarted = scan_authenticated_prefix(
        (*entries, ("a-result", "b-input", first_result.canonical_bytes())),
        tenant_id="tenant",
        journal_instance_id="h1-postseal-recovery",
    )
    assert restarted.tip == "a-result"
    assert restarted.state_for_root(first.root_id()).next_stage == "CONVERSATION"

    rival_second = second_input.model_copy(
        predecessor_entry_id="b-input",
        semantic_input=b"rival-b-input",
        semantic_input_digest=_digest(b"rival-b-input"),
    )
    with pytest.raises(H1PostSealRecoveryRecordError, match="rival stage input"):
        scan_authenticated_prefix(
            (*entries, ("bad-b", "b-input", rival_second.canonical_bytes())),
            tenant_id="tenant",
            journal_instance_id="h1-postseal-recovery",
        )

    competing_same_seal = _root().model_copy(source_commitment="8" * 64)
    competing_root = H1PostSealRecoveryTransition.begin(
        H1PostSealRecoveryState.empty(competing_same_seal)
    )
    with pytest.raises(H1PostSealRecoveryRecordError, match="competing recovery roots"):
        scan_authenticated_prefix(
            (
                ("a-root", None, first_root.canonical_bytes()),
                ("competing-root", "a-root", competing_root.canonical_bytes()),
            ),
            tenant_id="tenant",
            journal_instance_id="h1-postseal-recovery",
        )


def test_transition_rejects_changed_effects_id_and_result_before_durable_append() -> None:
    root = _root()
    root_record = H1PostSealRecoveryTransition.begin(H1PostSealRecoveryState.empty(root))
    state = apply_authenticated_prefix((("entry-0", None, root_record.canonical_bytes()),), root)
    with pytest.raises(H1PostSealRecoveryRecordError, match="first stage"):
        H1PostSealRecoveryTransition.pin_input(
            state, stage="EFFECTS", semantic_input=b"input", effects_command_id="id"
        )

    with pytest.raises(H1PostSealRecoveryRecordError, match="production recovery journal"):
        denied_production_recovery_journal("arbitrary-path", object())

    with pytest.raises((H1PostSealRecoveryRecordError, H1PostSealRecoveryUnavailable)):
        H1PostSealRecoveryJournal.open_enrolled(object())  # type: ignore[arg-type]

    assert H1PostSealRecoveryUnavailable.__name__ == "H1PostSealRecoveryUnavailable"
