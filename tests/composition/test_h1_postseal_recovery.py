"""Pure recovery-record state machine; storage/mount authority is deliberately absent."""

from __future__ import annotations

import hashlib

import pytest

from chiplog.composition.h1_postseal_recovery import (
    H1PostSealRecoveryJournal,
    H1PostSealRecoveryRecordError,
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


def test_root_requires_the_installed_loop_head_shape() -> None:
    assert _root().selected_run_head == "loop:" + "d" * 64
    with pytest.raises(H1PostSealRecoveryRecordError, match="selected_run_head"):
        _root().model_copy(selected_run_head="d" * 64)


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
        H1PostSealRecoveryJournal.open_enrolled(object())

    assert H1PostSealRecoveryUnavailable.__name__ == "H1PostSealRecoveryUnavailable"
