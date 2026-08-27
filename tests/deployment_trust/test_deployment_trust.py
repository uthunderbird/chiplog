from __future__ import annotations

import inspect
import json
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from typing import Literal

import pytest

from chiplog.adapters.driven.deployment_trust import (
    IndependentTenantDecisionJournal,
    SQLiteTrustMaterializer,
)
from chiplog.architecture.r4_r5_freeze import R4_R5_RECORDS
from chiplog.capabilities.deployment_trust import (
    AuthenticationRequest,
    TenantDecisionJournalPort,
    TrustMaterializationPort,
    TrustReferenceRevalidation,
    TrustRevalidator,
)
from chiplog.capabilities.deployment_trust._model import (
    DatabaseGenesis,
    DeploymentTrustService,
    RecoveryRequired,
    TrustInvariantError,
)
from chiplog.capabilities.deployment_trust._records import RECORD_TYPE_IDS
from chiplog.domain_primitives import PrincipalId, RecordId, TenantId


def service(
    tmp_path: Path,
) -> tuple[DeploymentTrustService, IndependentTenantDecisionJournal, SQLiteTrustMaterializer]:
    journal = IndependentTenantDecisionJournal(tmp_path / "protected" / "journal.jsonl")
    materializer = SQLiteTrustMaterializer(tmp_path / "tenant.sqlite3")
    trust = DeploymentTrustService(
        journal,
        materializer,
        operator_key_id="operator:1",
        operator_secret=b"operator-secret",
        broker_secret=b"broker-secret",
    )
    return trust, journal, materializer


def active(
    tmp_path: Path,
) -> tuple[DeploymentTrustService, IndependentTenantDecisionJournal, SQLiteTrustMaterializer]:
    trust, journal, materializer = service(tmp_path)
    trust.initialize("tenant-1", "database-1")
    trust.bootstrap(
        token_fingerprint="token-1",
        peer="uid:1000",
        expected_peer="uid:1000",
        principal_id=PrincipalId("principal-1"),
        credential_id="credential-1",
        session_id="session-1",
        recovery_verifier=sha256(b"recovery").hexdigest(),
    )
    return trust, journal, materializer


def poll_page(*member_ids: str) -> tuple[str, bytes]:
    raw = json.dumps(
        {"members": [{"id": member_id} for member_id in member_ids]},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return sha256(raw).hexdigest(), raw


def test_boundaries_are_protocols_and_adapters_are_structural(tmp_path: Path) -> None:
    assert (
        inspect.isclass(TenantDecisionJournalPort)
        and vars(TenantDecisionJournalPort)["_is_protocol"]
    )
    assert (
        inspect.isclass(TrustMaterializationPort) and vars(TrustMaterializationPort)["_is_protocol"]
    )
    assert inspect.isclass(TrustRevalidator) and vars(TrustRevalidator)["_is_protocol"]
    trust, journal, materializer = service(tmp_path)
    assert hasattr(journal, "append") and hasattr(materializer, "materialize")
    assert hasattr(trust, "revalidate")


def test_durable_record_discriminators_equal_the_freeze() -> None:
    frozen = tuple(
        item.record_type_id for item in R4_R5_RECORDS if item.owner == "deployment_trust"
    )
    assert RECORD_TYPE_IDS == frozen


def test_genesis_is_immutable_and_cross_tenant_relabel_is_denied(tmp_path: Path) -> None:
    trust, _, _ = active(tmp_path)
    trust.restore(DatabaseGenesis("tenant-1", "database-1"))
    with pytest.raises(RecoveryRequired, match="relabel is unsupported"):
        trust.restore(DatabaseGenesis("tenant-2", "database-1"))


def test_startup_requires_exact_signed_binding_and_materialized_head(tmp_path: Path) -> None:
    trust, journal, materializer = service(tmp_path)
    state = trust.initialize("tenant-1", "database-1")
    assert trust.genesis is not None and trust.binding is not None
    restarted = DeploymentTrustService(
        journal,
        materializer,
        operator_key_id="operator:1",
        operator_secret=b"operator-secret",
        broker_secret=b"broker-secret",
    )
    restarted.admit_startup(state, trust.genesis, trust.binding)
    forged = replace(trust.binding, signature="forged")
    with pytest.raises(RecoveryRequired, match="startup"):
        restarted.admit_startup(state, trust.genesis, forged)


def test_restart_rebuilds_principal_credentials_sources_replay_and_poll_state(
    tmp_path: Path,
) -> None:
    trust, journal, materializer = active(tmp_path)
    source = trust.register_evidence_source("poll")
    witness = trust.issue_transport_witness(
        witness_id="witness-1",
        kind="TELEGRAM_POLLING",
        candidate_id="candidate-1",
        bot_account="bot",
        endpoint="poll",
        raw_bytes=b"update",
        replay_identity="update-1",
    )
    page_id, raw_page = poll_page("update-1")
    trust.record_poll_response_page(
        "poll", source.head, page_id, raw_page, (("update-1", "PUBLISHED"),)
    )
    trust.authorize_poll_cursor("poll", "0001", page_id, 1)
    trust.apply_poll_cursor("poll", "0001")
    assert trust.state is not None and trust.genesis is not None and trust.binding is not None
    restarted = DeploymentTrustService(
        journal,
        materializer,
        operator_key_id="operator:1",
        operator_secret=b"operator-secret",
        broker_secret=b"broker-secret",
    )
    restarted.admit_startup(trust.state, trust.genesis, trust.binding)
    cli = AuthenticationRequest("CLI", "credential-1", "session-1", "", None)
    assert restarted.authenticate(cli).disposition == "VALID"
    telegram = AuthenticationRequest(
        "TELEGRAM", "credential-1", "session-1", "candidate-1", witness.witness_id
    )
    assert restarted.authenticate(telegram).disposition == "DENIED"
    assert restarted.may_emit_poll_request("poll", "0001")
    assert (
        restarted.bootstrap(
            token_fingerprint="token-1",
            peer="uid:1000",
            expected_peer="uid:1000",
            principal_id=PrincipalId("principal-1"),
            credential_id="credential-1",
            session_id="session-1",
            recovery_verifier=sha256(b"recovery").hexdigest(),
        )
        == trust.state.materialization_head
    )


def test_one_shot_bootstrap_replay_wrong_peer_expiry_and_second_principal(tmp_path: Path) -> None:
    trust, _, _ = service(tmp_path)
    trust.initialize("tenant-1", "database-1")
    kwargs = dict(
        token_fingerprint="token-1",
        peer="wrong",
        expected_peer="right",
        principal_id=PrincipalId("principal-1"),
        credential_id="credential-1",
        session_id="session-1",
        recovery_verifier="verifier",
    )
    with pytest.raises(TrustInvariantError, match="peer"):
        trust.bootstrap(**kwargs)  # type: ignore[arg-type]
    kwargs.update(peer="right", expires_at=1, now=2)
    with pytest.raises(TrustInvariantError, match="expired"):
        trust.bootstrap(**kwargs)  # type: ignore[arg-type]
    kwargs.update(now=1)
    first = trust.bootstrap(**kwargs)  # type: ignore[arg-type]
    assert trust.bootstrap(**kwargs) == first  # type: ignore[arg-type]
    kwargs.update(token_fingerprint="other", principal_id=PrincipalId("principal-2"))
    with pytest.raises(TrustInvariantError, match="conflict"):
        trust.bootstrap(**kwargs)  # type: ignore[arg-type]


def test_cli_authentication_and_complete_reference_revalidation(tmp_path: Path) -> None:
    trust, _, _ = active(tmp_path)
    decision = trust.authenticate(
        AuthenticationRequest("CLI", "credential-1", "session-1", "", None)
    )
    assert decision.disposition == "VALID" and decision.reference is not None
    request = TrustReferenceRevalidation(
        decision.reference,
        "CREATE_INTENTION_LINE",
        RecordId(TenantId("tenant-1"), "line-1"),
    )
    assert trust.revalidate(request) == decision
    foreign = TrustReferenceRevalidation(
        decision.reference,
        request.operation,
        RecordId(TenantId("tenant-2"), "line-1"),
    )
    assert trust.revalidate(foreign).disposition == "DENIED"
    substituted_operation = TrustReferenceRevalidation(
        decision.reference,
        "DELETE_INTENTION_LINE",
        request.subject_id,
    )
    assert trust.revalidate(substituted_operation).disposition == "DENIED"
    empty_subject = TrustReferenceRevalidation(
        decision.reference,
        request.operation,
        RecordId(TenantId("tenant-1"), ""),
    )
    assert trust.revalidate(empty_subject).disposition == "DENIED"


def test_rotation_revokes_old_credential_and_sessions(tmp_path: Path) -> None:
    trust, _, _ = active(tmp_path)
    before = trust.authenticate(AuthenticationRequest("CLI", "credential-1", "session-1", "", None))
    assert before.disposition == "VALID"
    trust.rotate_credential("credential:1", "credential-2")
    assert (
        trust.authenticate(
            AuthenticationRequest("CLI", "credential-1", "session-1", "", None)
        ).disposition
        == "STALE"
    )
    trust.revoke("credential:2")
    assert (
        trust.authenticate(AuthenticationRequest("CLI", "credential-2", "", "", None)).disposition
        == "STALE"
    )


def test_emergency_recovery_keeps_principal_and_rejects_wrong_secret(tmp_path: Path) -> None:
    trust, _, _ = active(tmp_path)
    with pytest.raises(TrustInvariantError, match="authentication"):
        trust.emergency_recover(
            peer="uid:1000",
            expected_peer="uid:1000",
            recovery_secret="wrong",
            expected_recovery_verifier=sha256(b"recovery").hexdigest(),
            new_credential_id="new",
        )
    recovered = trust.emergency_recover(
        peer="uid:1000",
        expected_peer="uid:1000",
        recovery_secret="recovery",
        expected_recovery_verifier=sha256(b"recovery").hexdigest(),
        new_credential_id="new",
    )
    assert recovered.credential_id == "new"


def test_telegram_requires_broker_witness_and_replay_is_closed(tmp_path: Path) -> None:
    trust, _, _ = active(tmp_path)
    request = AuthenticationRequest("TELEGRAM", "credential-1", "session-1", "candidate-1", None)
    assert trust.authenticate(request).disposition == "DENIED"
    witness = trust.issue_transport_witness(
        witness_id="witness-1",
        kind="TELEGRAM_POLLING",
        candidate_id="candidate-1",
        bot_account="bot",
        endpoint="poll",
        raw_bytes=b"update",
        replay_identity="update-1",
    )
    request = AuthenticationRequest(
        "TELEGRAM", "credential-1", "session-1", "candidate-1", witness.witness_id
    )
    authenticated = trust.authenticate(request)
    assert authenticated.disposition == "VALID" and authenticated.reference is not None
    assert (
        trust.revalidate(
            TrustReferenceRevalidation(
                authenticated.reference,
                "CREATE_INTENTION_LINE",
                RecordId(TenantId("tenant-1"), "line-1"),
            )
        ).disposition
        == "VALID"
    )
    substituted = AuthenticationRequest(
        "TELEGRAM", "credential-1", "session-1", "candidate-2", witness.witness_id
    )
    assert trust.authenticate(substituted).disposition == "DENIED"
    trust.register_evidence_source("later-source")
    assert trust.authenticate(request).disposition == "DENIED"
    assert (
        trust.issue_transport_witness(
            witness_id="witness-1",
            kind="TELEGRAM_POLLING",
            candidate_id="candidate-1",
            bot_account="bot",
            endpoint="poll",
            raw_bytes=b"update",
            replay_identity="update-1",
        )
        == witness
    )
    with pytest.raises(TrustInvariantError, match="conflict"):
        trust.issue_transport_witness(
            witness_id="witness-2",
            kind="TELEGRAM_POLLING",
            candidate_id="candidate-2",
            bot_account="bot",
            endpoint="poll",
            raw_bytes=b"changed",
            replay_identity="update-1",
        )
    with pytest.raises(TrustInvariantError, match="identity conflict"):
        trust.issue_transport_witness(
            witness_id="witness-1",
            kind="TELEGRAM_POLLING",
            candidate_id="candidate-2",
            bot_account="bot",
            endpoint="poll",
            raw_bytes=b"different update",
            replay_identity="update-2",
        )
    trust.rotate_credential("credential:1", "credential-2")
    stale_witness = AuthenticationRequest(
        "TELEGRAM", "credential-2", "", "candidate-1", witness.witness_id
    )
    assert trust.authenticate(stale_witness).disposition == "DENIED"
    with pytest.raises(TrustInvariantError, match="conflict"):
        trust.issue_transport_witness(
            witness_id="witness-1",
            kind="TELEGRAM_WEBHOOK",
            candidate_id="candidate-2",
            bot_account="bot",
            endpoint="other",
            raw_bytes=b"update",
            replay_identity="update-1",
        )


def test_evidence_source_authentication_and_late_evidence(tmp_path: Path) -> None:
    trust, _, _ = active(tmp_path)
    source = trust.register_evidence_source("provider-1")
    request = AuthenticationRequest("EVIDENCE", "credential-1", "session-1", "provider-1", None)
    assert trust.authenticate(request).disposition == "VALID"
    trust.mark_authenticated_late_evidence("provider-1", source.head)
    stale = AuthenticationRequest("EVIDENCE", "credential-1", "session-1", "missing", None)
    assert trust.authenticate(stale).disposition == "STALE"


def test_poll_cursor_is_durable_before_request_emission(tmp_path: Path) -> None:
    trust, _, _ = active(tmp_path)
    source = trust.register_evidence_source("poll")
    with pytest.raises(TrustInvariantError, match="durable page"):
        trust.authorize_poll_cursor("poll", "0001", "missing", 1)
    page_id, raw_page = poll_page("update-1")
    page_decision = trust.record_poll_response_page(
        "poll", source.head, page_id, raw_page, (("update-1", "PUBLISHED"),)
    )
    trust.authorize_poll_cursor("poll", "9", page_id, 1)
    assert not trust.may_emit_poll_request("poll", "9")
    trust.apply_poll_cursor("poll", "9")
    assert trust.may_emit_poll_request("poll", "9")
    assert (
        trust.record_poll_response_page(
            "poll", source.head, page_id, raw_page, (("update-1", "PUBLISHED"),)
        )
        == page_decision
    )
    second_page_id, second_raw_page = poll_page("update-2")
    trust.record_poll_response_page(
        "poll",
        source.head,
        second_page_id,
        second_raw_page,
        (("update-2", "PUBLISHED"),),
    )
    auth_decision = trust.authorize_poll_cursor("poll", "10", second_page_id, 2)
    assert trust.authorize_poll_cursor("poll", "10", second_page_id, 2) == auth_decision
    apply_decision = trust.apply_poll_cursor("poll", "10")
    assert trust.apply_poll_cursor("poll", "10") == apply_decision
    third_page_id, third_raw_page = poll_page("update-3")
    trust.record_poll_response_page(
        "poll",
        source.head,
        third_page_id,
        third_raw_page,
        (("update-3", "PUBLISHED"),),
    )
    with pytest.raises(TrustInvariantError, match="monotonic"):
        trust.authorize_poll_cursor("poll", "11", third_page_id, 4)


def test_poll_page_has_closed_unique_member_dispositions(tmp_path: Path) -> None:
    trust, _, _ = active(tmp_path)
    source = trust.register_evidence_source("poll")
    page_id, raw_page = poll_page("update-1")
    trust.record_poll_response_page(
        "poll", source.head, page_id, raw_page, (("update-1", "PUBLISHED"),)
    )
    with pytest.raises(TrustInvariantError, match="unique"):
        trust.record_poll_response_page(
            "poll",
            source.head,
            page_id,
            raw_page,
            (("update-1", "PUBLISHED"), ("update-1", "HELD")),
        )
    held_id, held_raw = poll_page("update-2")
    trust.record_poll_response_page("poll", source.head, held_id, held_raw, (("update-2", "HELD"),))
    with pytest.raises(TrustInvariantError, match="terminal"):
        trust.authorize_poll_cursor("poll", "0002", held_id, 1)
    incomplete_id, incomplete_raw = poll_page("one", "two")
    with pytest.raises(TrustInvariantError, match="incomplete"):
        trust.record_poll_response_page(
            "poll", source.head, incomplete_id, incomplete_raw, (("one", "PUBLISHED"),)
        )
    with pytest.raises(TrustInvariantError, match="authenticated source"):
        trust.record_poll_response_page(
            "poll", "stale-head", page_id, raw_page, (("update-1", "PUBLISHED"),)
        )


def test_journal_decision_precedes_materialization_and_replays_after_rollback(
    tmp_path: Path,
) -> None:
    trust, journal, materializer = service(tmp_path)
    trust.initialize("tenant-1", "database-1")
    with pytest.raises(RecoveryRequired, match="requires replay"):
        trust._commit("CRASH", {"value": 1}, fault="after_decision")
    decision_id = journal.entries()[-1][0]
    assert not materializer.materialized(decision_id)
    assert trust.recover() == 1
    assert materializer.materialized(decision_id)
    assert trust.recover() == 0


def test_authenticated_no_decision_aborts_and_ambiguous_observation_holds(tmp_path: Path) -> None:
    trust, _, _ = service(tmp_path)
    assert trust.observation("absent") == "ABORT"

    class AmbiguousJournal:
        def append(self, decision: bytes, predecessor: str | None) -> str:
            raise AssertionError

        def entries(self) -> tuple[tuple[str, str | None, bytes], ...]:
            return ()

        def observation(self, decision_id: str) -> Literal["DECIDED", "NO_DECISION", "AMBIGUOUS"]:
            return "AMBIGUOUS"

    held = DeploymentTrustService(
        AmbiguousJournal(),
        SQLiteTrustMaterializer(tmp_path / "held.sqlite3"),
        operator_key_id="k",
        operator_secret=b"s",
        broker_secret=b"b",
    )
    assert held.observation("unknown") == "HOLD"


def test_journal_rollback_and_fork_are_rejected(tmp_path: Path) -> None:
    trust, journal, _ = active(tmp_path)
    path = tmp_path / "protected" / "journal.jsonl"
    lines = path.read_bytes().splitlines()
    path.write_bytes(b"\n".join(lines[:-1]) + b"\n")
    with pytest.raises(RuntimeError, match="rollback"):
        journal.append(b"fork", "0" * 64)
    assert trust.state is not None


def test_journal_entry_cannot_be_resigned_by_recomputing_public_hash(tmp_path: Path) -> None:
    _, journal, _ = active(tmp_path)
    path = tmp_path / "protected" / "journal.jsonl"
    lines = path.read_bytes().splitlines()
    item = json.loads(lines[-1])
    changed = b"attacker replacement"
    predecessor = str(item["predecessor"])
    item["decision"] = changed.hex()
    item["decision_id"] = sha256(predecessor.encode() + b"\x00" + changed).hexdigest()
    lines[-1] = json.dumps(item, sort_keys=True, separators=(",", ":")).encode()
    path.write_bytes(b"\n".join(lines) + b"\n")
    with pytest.raises(RuntimeError, match="prefix"):
        journal.entries()


def test_independent_database_rollback_replays_from_protected_journal(tmp_path: Path) -> None:
    _, journal, materializer = active(tmp_path)
    expected_records = len(materializer.records())
    expected_decisions = len(journal.entries())
    materializer.close()
    database = tmp_path / "tenant.sqlite3"
    database.unlink()
    replacement = SQLiteTrustMaterializer(database)
    restarted = DeploymentTrustService(
        journal,
        replacement,
        operator_key_id="operator:1",
        operator_secret=b"operator-secret",
        broker_secret=b"broker-secret",
    )
    assert restarted.recover() == expected_decisions
    assert len(replacement.records()) == expected_records
    assert (
        restarted.authenticate(
            AuthenticationRequest("CLI", "credential-1", "session-1", "", None)
        ).disposition
        == "VALID"
    )


def test_binding_and_journal_rotations_are_monotonic(tmp_path: Path) -> None:
    trust, _, _ = active(tmp_path)
    binding = trust.rotate_binding_key("operator:2", b"new-secret")
    assert binding.predecessor is not None
    rotated = trust.rotate_journal_root(2)
    assert rotated.journal_epoch == 2
    with pytest.raises(TrustInvariantError, match="exactly once"):
        trust.rotate_journal_root(4)


def test_prepared_transition_blocks_work_and_ready_competes_with_abort(tmp_path: Path) -> None:
    trust, _, _ = active(tmp_path)
    trust.prepare_trust_transition(
        transition_id="transition-1",
        kind="OPERATOR_BINDING_KEY_ROTATION",
        proposed_key_id="operator:2",
    )
    with pytest.raises(TrustInvariantError, match="outside active"):
        trust.register_evidence_source("blocked")
    trust.mark_transition_ready("transition-1")
    with pytest.raises(TrustInvariantError, match="prepared"):
        trust.abort_trust_transition("transition-1")
    trust.accept_trust_transition("transition-1", new_secret=b"new-secret")
    assert trust.state is not None and trust.state.phase == "ACTIVE"


def test_prepared_transition_can_abort_to_same_current_binding(tmp_path: Path) -> None:
    trust, _, _ = active(tmp_path)
    before = trust.state
    trust.prepare_trust_transition(
        transition_id="transition-1",
        kind="JOURNAL_ROOT_ROTATION",
        proposed_epoch=2,
    )
    trust.abort_trust_transition("transition-1")
    assert trust.state is not None and before is not None
    assert trust.state.trust_head == before.trust_head


def test_future_contour_holds_all_authentication(tmp_path: Path) -> None:
    trust, _, _ = active(tmp_path)
    trust.hold_future_contour()
    decision = trust.authenticate(
        AuthenticationRequest("CLI", "credential-1", "session-1", "", None)
    )
    assert decision.disposition == "INDETERMINATE"
