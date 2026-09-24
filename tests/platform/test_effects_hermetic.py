"""Offline raw leaf observations only; no broker admission or runtime DoD claim."""

import asyncio
import hashlib
import hmac
import json
from dataclasses import replace

import pytest

from chiplog.adapters.driven.effects_hermetic import (
    HermeticEffectsProvider,
    HermeticReceiptIntegrityError,
    HermeticResponseLost,
    verify_hermetic_receipt,
)
from tests.support.dispatch import _ticket as _ticket


def test_response_loss_retains_one_external_effect_and_no_hidden_retry() -> None:
    leaf = HermeticEffectsProvider(
        receipt_key=b"independent-fixture-key", scenarios=("LOST_RESPONSE_AFTER_EFFECT",)
    )
    with pytest.raises(HermeticResponseLost):
        asyncio.run(leaf.emit_issued(_ticket()))
    assert len(leaf.transfers) == 1
    assert leaf.transfers[0].occurred_members == ("effect",)
    receipt = leaf.reconcile("child0")
    assert receipt is not None
    envelope = json.loads(receipt)
    raw = bytes.fromhex(envelope["observation_hex"])
    assert hmac.compare_digest(
        hmac.digest(
            b"independent-fixture-key", b"chiplog.hermetic-effects.receipt.v1\x00" + raw, "sha256"
        ).hex(),
        envelope["signature"],
    )
    assert json.loads(raw)["payload_fingerprint"] == _ticket().payload_fingerprint
    verified = verify_hermetic_receipt(
        _ticket(), receipt, registered_receipt_key=b"independent-fixture-key"
    )
    assert verified.occurred_members == ("effect",)
    assert verified.permanently_incapable_members == ()
    assert leaf.reconcile("missing") is None
    assert len(leaf.transfers) == 1
    with pytest.raises(ValueError, match="reenqueue"):
        asyncio.run(leaf.emit_issued(_ticket()))


def test_real_recipient_changed_bytes_and_n_plus_one_never_reach_transfer_log() -> None:
    leaf = HermeticEffectsProvider(
        receipt_key=b"key", scenarios=("CONFIRM",), maximum_payload_bytes=3
    )
    for mutated in (
        replace(_ticket(), recipient="real-recipient"),
        replace(_ticket(), provider="calendar"),
        replace(_ticket(), payload=b"changed"),
        replace(
            _ticket(), payload=b"four", payload_fingerprint=hashlib.sha256(b"four").hexdigest()
        ),
    ):
        with pytest.raises(ValueError):
            asyncio.run(leaf.emit_issued(mutated))
        assert leaf.transfers == ()
    asyncio.run(leaf.emit_issued(_ticket()))
    assert len(leaf.transfers) == 1


def test_receipt_verifier_rejects_rebinding_every_issued_ticket_field() -> None:
    from dataclasses import fields

    leaf = HermeticEffectsProvider(receipt_key=b"registered", scenarios=("CONFIRM",))
    ticket = _ticket()
    receipt = asyncio.run(leaf.emit_issued(ticket))
    for field in fields(ticket):
        value = getattr(ticket, field.name)
        changed = (
            value + b"x"
            if isinstance(value, bytes)
            else value + 1
            if isinstance(value, int)
            else (*value, "extra")
            if isinstance(value, tuple)
            else value + "x"
        )
        mutated = replace(ticket)
        object.__setattr__(mutated, field.name, changed)
        with pytest.raises(HermeticReceiptIntegrityError) as error:
            verify_hermetic_receipt(
                mutated,
                receipt,
                registered_receipt_key=b"registered",
            )
        assert error.value.__cause__ is not None
    assert len(leaf.transfers) == 1


@pytest.mark.parametrize(
    "mutation",
    (
        "unknown_field",
        "unknown_member",
        "duplicate_member",
        "overlap",
        "changed_child",
        "changed_payload",
        "wrong_schema",
        "wrong_key",
        "unsigned_domain",
        "noncanonical",
    ),
)
def test_even_signed_malformed_receipt_cannot_change_child_or_outcome(mutation: str) -> None:
    leaf = HermeticEffectsProvider(receipt_key=b"registered", scenarios=("CONFIRM",))
    ticket = _ticket()
    receipt = asyncio.run(leaf.emit_issued(ticket))
    envelope = json.loads(receipt)
    observation = json.loads(bytes.fromhex(envelope["observation_hex"]))
    if mutation == "unknown_field":
        observation["authority"] = True
    elif mutation == "unknown_member":
        observation["occurred_members"] = ["not-issued"]
    elif mutation == "duplicate_member":
        observation["occurred_members"] = ["effect", "effect"]
    elif mutation == "overlap":
        observation["permanently_incapable_members"] = ["effect"]
    elif mutation == "changed_child":
        observation["transmission_id"] = "sibling"
    elif mutation == "changed_payload":
        observation["payload_fingerprint"] = "a" * 64
    elif mutation == "wrong_schema":
        observation["schema_id"] = "other.v1"
    raw = json.dumps(observation, sort_keys=True, separators=(",", ":")).encode()
    envelope["observation_hex"] = raw.hex()
    signed = (
        raw if mutation == "unsigned_domain" else b"chiplog.hermetic-effects.receipt.v1\x00" + raw
    )
    envelope["signature"] = hmac.digest(b"registered", signed, "sha256").hex()
    forged = json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode()
    if mutation == "noncanonical":
        forged += b" "
    with pytest.raises(HermeticReceiptIntegrityError):
        verify_hermetic_receipt(
            ticket,
            forged,
            registered_receipt_key=(b"other-key" if mutation == "wrong_key" else b"registered"),
        )
    assert len(leaf.transfers) == 1
    assert leaf.reconcile("missing") is None


def test_read_only_probe_preserves_mixed_and_permanent_outcomes_without_retry() -> None:
    from typing import Literal

    ticket = replace(_ticket(), bundle_members=("a", "b"))
    scenarios: tuple[Literal["MIXED", "PERMANENT_NO_EFFECT"], ...] = (
        "MIXED",
        "PERMANENT_NO_EFFECT",
    )
    for scenario in scenarios:
        expected = (("a",), ("b",)) if scenario == "MIXED" else ((), ("a", "b"))
        leaf = HermeticEffectsProvider(receipt_key=b"registered", scenarios=(scenario,))
        asyncio.run(leaf.emit_issued(ticket))
        receipt = leaf.reconcile(ticket.transmission_id)
        assert receipt is not None
        observed = verify_hermetic_receipt(ticket, receipt, registered_receipt_key=b"registered")
        assert (observed.occurred_members, observed.permanently_incapable_members) == expected
        assert len(leaf.transfers) == 1
