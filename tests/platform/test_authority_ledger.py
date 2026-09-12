from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from chiplog.platform.authority_ledger import (
    AuthorityLedgerConflict,
    AuthorityLedgerDenied,
    BrokerAuthorityLedger,
    OperationDisposition,
    OperationToken,
)


def _token(epoch: int, mode: str = "IDEMPOTENT_EXACT") -> OperationToken:
    return OperationToken.model_validate(
        {
            "mode": mode,
            "tenant_id": "tenant-1",
            "broker_epoch": epoch,
            "owner_id": "planning",
            "generation_id": "generation-1",
            "session_id": "session-1",
            "target_id": "planning-store",
            "capability_id": "event_appender",
            "operation_kind": "planning.commit",
            "payload_fingerprint": "a" * 64,
            "nonce": "nonce-1",
            "expiry_ns": 100,
        }
    )


def _ledger(path: Path) -> tuple[BrokerAuthorityLedger, int]:
    ledger = BrokerAuthorityLedger(path)
    epoch = ledger.allocate_epoch("tenant-1", "endpoint-1", "key-digest-1")
    return ledger, epoch


@pytest.mark.parametrize("mode", ["ONE_SHOT", "IDEMPOTENT_EXACT"])
def test_consumption_issue_and_response_slot_are_durable_before_execution(
    tmp_path: Path, mode: str
) -> None:
    path = tmp_path / "authority.sqlite3"
    ledger, epoch = _ledger(path)
    token = _token(epoch, mode)
    issued = ledger.admit("operation-1", "response-1", token, now_ns=1)
    assert issued.state == "ISSUED"

    reopened = BrokerAuthorityLedger(path)
    assert reopened.pending("tenant-1") == (issued,)
    assert reopened.admit("operation-1", "response-1", token, now_ns=2) == issued


def test_changed_replay_nonce_reuse_and_stale_epoch_are_denied(tmp_path: Path) -> None:
    ledger, epoch = _ledger(tmp_path / "authority.sqlite3")
    token = _token(epoch)
    ledger.admit("operation-1", "response-1", token, now_ns=1)
    with pytest.raises(AuthorityLedgerConflict, match="changed replay"):
        ledger.admit(
            "operation-1",
            "response-1",
            token.model_copy(update={"payload_fingerprint": "b" * 64}),
            now_ns=2,
        )
    with pytest.raises(AuthorityLedgerConflict, match="nonce"):
        ledger.admit("operation-2", "response-2", token, now_ns=2)
    ledger.allocate_epoch("tenant-1", "endpoint-2", "key-digest-2")
    with pytest.raises(AuthorityLedgerDenied, match="stale"):
        ledger.admit("operation-3", "response-3", token, now_ns=2)


def test_terminal_disposition_is_exact_and_never_reexecutes(tmp_path: Path) -> None:
    ledger, epoch = _ledger(tmp_path / "authority.sqlite3")
    token = _token(epoch)
    ledger.admit("operation-1", "response-1", token, now_ns=1)
    result = b"committed"
    definite = OperationDisposition(
        tenant_id="tenant-1",
        operation_id="operation-1",
        response_slot_id="response-1",
        state="DEFINITE",
        result_digest=hashlib.sha256(result).hexdigest(),
        result_bytes=result,
        recovery_obligation_id=None,
    )
    assert ledger.finish(definite) == definite
    assert ledger.finish(definite) == definite
    with pytest.raises(AuthorityLedgerConflict, match="rival"):
        ledger.finish(
            definite.model_copy(
                update={
                    "result_digest": hashlib.sha256(b"rival").hexdigest(),
                    "result_bytes": b"rival",
                }
            )
        )


def test_post_issue_crash_reduces_to_durable_unknown(tmp_path: Path) -> None:
    path = tmp_path / "authority.sqlite3"
    ledger, epoch = _ledger(path)
    ledger.admit("operation-1", "response-1", _token(epoch, "ONE_SHOT"), now_ns=1)
    reopened = BrokerAuthorityLedger(path)
    unknown = OperationDisposition(
        tenant_id="tenant-1",
        operation_id="operation-1",
        response_slot_id="response-1",
        state="OUTCOME_UNKNOWN",
        result_digest=None,
        result_bytes=None,
        recovery_obligation_id="recovery-1",
    )
    assert reopened.finish(unknown) == unknown
