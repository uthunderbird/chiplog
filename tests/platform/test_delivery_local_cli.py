"""Public local-delivery consumer contracts and observable durable inbox behavior."""

import hashlib
import inspect
import os
from dataclasses import replace
from pathlib import Path

import pytest

from chiplog.adapters.driven.delivery_local_cli import (
    CONTRACT_VERSION,
    LocalCliDeliveryProvider,
    LocalCliReceiptObservation,
    verify_local_cli_receipt,
)
from chiplog.adapters.driven.effects_hermetic import IssuedEffectSendTicket


def test_public_delivery_surface() -> None:
    assert CONTRACT_VERSION == "chiplog.local-cli.delivery.v1"
    assert inspect.iscoroutinefunction(LocalCliDeliveryProvider.emit_issued)
    for method in ("reconcile", "read_payload", "verify_receipt"):
        assert callable(getattr(LocalCliDeliveryProvider, method))
    assert callable(verify_local_cli_receipt)
    assert LocalCliReceiptObservation(("member",), ()).occurred_members == ("member",)


KEY = b"operator-pinned-local-delivery-key"


def ticket(
    provider: LocalCliDeliveryProvider, payload: bytes = b"accepted\nrendering"
) -> IssuedEffectSendTicket:
    return IssuedEffectSendTicket(
        tenant_id="tenant",
        issued_operation_id="operation",
        journal_decision_id="decision",
        journal_decision_fingerprint="d" * 64,
        transmission_id="delivery/child/0",
        transmission_fingerprint="t" * 64,
        request_ordinal=0,
        intent_id="delivery",
        intent_fingerprint="i" * 64,
        provider=provider.provider_id,
        account=provider.account_id,
        recipient=provider.recipient_id,
        canonical_address=provider.canonical_address,
        endpoint_head=provider.endpoint_head,
        credential_binding_head=provider.credential_binding_head,
        adapter_contract_version=CONTRACT_VERSION,
        payload=payload,
        payload_fingerprint=hashlib.sha256(payload).hexdigest(),
        idempotency_key="first-send",
        bundle_members=("delivery",),
    )


async def test_actual_private_inbox_reopen_exact_replay_and_receipt(tmp_path: Path) -> None:
    directory = tmp_path / "inbox"
    provider = LocalCliDeliveryProvider(directory, KEY, "actual-trust-principal")
    selected = ticket(provider)
    assert provider.reconcile(selected.transmission_id) is None
    receipt = await provider.emit_issued(selected)
    assert provider.read_payload(selected.transmission_id) == selected.payload
    reopened = LocalCliDeliveryProvider(directory, KEY, "actual-trust-principal")
    assert reopened.reconcile(selected.transmission_id) == receipt
    assert await reopened.emit_issued(selected) == receipt
    assert len(list(directory.glob("*.json"))) == 1
    assert verify_local_cli_receipt(
        selected, receipt, registered_receipt_key=KEY
    ).occurred_members == ("delivery",)
    with pytest.raises(ValueError, match="ticket changed"):
        await reopened.emit_issued(replace(selected, issued_operation_id="substituted-operation"))
    with pytest.raises(ValueError, match="authentication failed"):
        verify_local_cli_receipt(selected, receipt, registered_receipt_key=b"wrong")
    with pytest.raises(PermissionError, match="key changed"):
        LocalCliDeliveryProvider(directory, b"wrong", "actual-trust-principal")


async def test_invalid_target_and_payload_cannot_publish(tmp_path: Path) -> None:
    provider = LocalCliDeliveryProvider(tmp_path / "inbox", KEY, "p")
    original = ticket(provider)
    for changed in (
        replace(original, recipient="another-principal"),
        replace(original, account="uid:another"),
        replace(original, canonical_address=b"another-inbox"),
    ):
        with pytest.raises(PermissionError, match="target"):
            await provider.emit_issued(changed)
    for changed in (
        replace(original, payload=b"substituted"),
        replace(original, adapter_contract_version="chiplog.hermetic-effects.v1"),
        replace(original, bundle_members=("delivery", "delivery")),
    ):
        with pytest.raises(ValueError):
            await provider.emit_issued(changed)
    assert not list(provider.directory.glob("*.json"))


async def test_corruption_and_physical_alias_fail_loudly(tmp_path: Path) -> None:
    provider = LocalCliDeliveryProvider(tmp_path / "inbox", KEY, "p")
    original = ticket(provider)
    await provider.emit_issued(original)
    path = next(provider.directory.glob("*.json"))
    other = "other-transmission"
    alias = provider.directory / (hashlib.sha256(other.encode()).hexdigest() + ".json")
    alias.write_bytes(path.read_bytes())
    alias.chmod(0o600)
    with pytest.raises(ValueError, match="physical and logical"):
        provider.reconcile(other)
    path.write_bytes(b"{}")
    with pytest.raises(ValueError):
        provider.reconcile(original.transmission_id)


def test_directory_permissions_and_owner_are_checked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path / "inbox"
    directory.mkdir(mode=0o755)
    directory.chmod(0o755)
    with pytest.raises(PermissionError, match="private"):
        LocalCliDeliveryProvider(directory, KEY, "p")
    directory.chmod(0o700)
    provider = LocalCliDeliveryProvider(directory, KEY, "p")
    uid = os.getuid()
    monkeypatch.setattr(os, "getuid", lambda: uid + 1)
    with pytest.raises(PermissionError, match="UID"):
        provider.reconcile("missing")


async def test_retained_file_permissions_and_symlink_rejected(tmp_path: Path) -> None:
    provider = LocalCliDeliveryProvider(tmp_path / "inbox", KEY, "p")
    original = ticket(provider)
    await provider.emit_issued(original)
    path = next(provider.directory.glob("*.json"))
    path.chmod(0o644)
    with pytest.raises(PermissionError, match="private"):
        provider.read_payload(original.transmission_id)
    path.chmod(0o600)
    raw = path.read_bytes()
    path.unlink()
    target = tmp_path / "outside"
    target.write_bytes(raw)
    path.symlink_to(target)
    with pytest.raises(OSError):
        provider.reconcile(original.transmission_id)


async def test_publication_failure_before_and_after_atomic_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = LocalCliDeliveryProvider(tmp_path / "inbox", KEY, "p")
    original = ticket(provider)
    real_link = os.link

    def before(*args: object, **kwargs: object) -> None:
        raise OSError("before publication")

    monkeypatch.setattr(os, "link", before)
    with pytest.raises(OSError, match="before publication"):
        await provider.emit_issued(original)
    assert provider.reconcile(original.transmission_id) is None
    monkeypatch.setattr(os, "link", real_link)

    def after() -> None:
        raise OSError("after publication")

    monkeypatch.setattr(provider, "_sync_directory", after)
    with pytest.raises(OSError, match="after publication"):
        await provider.emit_issued(original)
    with pytest.raises(OSError, match="after publication"):
        provider.reconcile(original.transmission_id)
    with pytest.raises(OSError, match="after publication"):
        await provider.emit_issued(original)
    reopened = LocalCliDeliveryProvider(provider.directory, KEY, "p")
    receipt = reopened.reconcile(original.transmission_id)
    assert receipt is not None
    assert await reopened.emit_issued(original) == receipt
    assert not list(provider.directory.glob(".pending-*"))


def test_operator_must_provision_parent(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        LocalCliDeliveryProvider(tmp_path / "missing-parent" / "inbox", KEY, "p")
