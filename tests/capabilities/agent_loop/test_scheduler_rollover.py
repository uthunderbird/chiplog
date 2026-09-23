"""Physical rollover proposals preserve Run identity and exactly bind the old epoch."""

import base64
import hashlib
import json

import pytest
from tests.capabilities.agent_loop.support import view

from chiplog.capabilities.agent_loop.recovery_contracts import (
    ExhaustionBinding,
    PhysicalRootRolloverFence,
    Present,
    RolloverAuthorityRef,
    RolloverPredecessor,
)
from chiplog.capabilities.agent_loop.scheduler_contracts import (
    ExhaustedLease,
    PhysicalRootRolloverCommand,
    SchedulerCommandIdentity,
    SchedulerLineageView,
)
from chiplog.capabilities.agent_loop.scheduler_domain import SchedulerDomainError
from chiplog.capabilities.agent_loop.scheduler_rollover import (
    IssuedRolloverObservation,
    prepare_rollover,
    rollover_payload_fingerprint,
    rollover_snapshot_fingerprint,
)

MAX = 2**64 - 1


def fixture() -> tuple[
    SchedulerLineageView, PhysicalRootRolloverCommand, IssuedRolloverObservation
]:
    current = view(MAX)
    assert current.lease.kind == "HELD"
    held = current.lease.binding
    current = current.model_copy(
        update={
            "lease": ExhaustedLease(
                lease_head="exhausted",
                exhausted_command_id="takeover",
                trusted_expiry=100,
                authority_epoch="authority",
                preceding_held_lease=held,
            )
        }
    )
    authority = RolloverAuthorityRef(
        proof_id="proof",
        proof_fingerprint="a" * 64,
        authority_head="operator",
        command_id="rollover",
        command_payload_fingerprint="a" * 64,
        predecessor_rollover=current.predecessor_rollover,
    )
    command = PhysicalRootRolloverCommand(
        identity=SchedulerCommandIdentity(
            command_id="rollover", schema_version="1", canonicalization_version="1"
        ),
        fence=PhysicalRootRolloverFence(
            lineage=current.lineage,
            physical_root=current.physical_root,
            exhaustion=ExhaustionBinding(
                hold_head="exhausted",
                exhausted_command_id="takeover",
                lease=held,
                authority_epoch="authority",
            ),
            authority=authority,
        ),
    )
    authority = authority.model_copy(
        update={"command_payload_fingerprint": rollover_payload_fingerprint(command)}
    )
    command = command.model_copy(
        update={"fence": command.fence.model_copy(update={"authority": authority})}
    )
    issued = IssuedRolloverObservation(
        authority=authority,
        snapshot_fingerprint=rollover_snapshot_fingerprint(current),
        submission_id="submission",
        authority_epoch="authority",
        used_epoch_ids=("epoch",),
        used_lease_heads=("held-head", "exhausted"),
    )
    return current, command, issued


def test_complete_rollover_records_preserve_stable_lineage_and_derive_exact_hashes() -> None:
    current, command, issued = fixture()
    result = prepare_rollover(current, command, issued, submission_id="submission")
    assert result.view.lineage == current.lineage
    assert result.view.lease.kind == "UNLEASED" and result.view.lease.generation == 0
    assert result.view.physical_root.selector_version == 1
    assert result.view.physical_root.current_epoch_id != current.physical_root.current_epoch_id
    assert result.view.predecessor_rollover == RolloverPredecessor(
        decision=result.decision, edge=result.edge
    )
    assert len(result.records) == 6
    assert all("run" not in row.record_kind for row in result.records)
    decoded = {}
    for record in result.records:
        raw = base64.b64decode(record.canonical_base64)
        assert hashlib.sha256(raw).hexdigest() == record.fingerprint
        decoded[record.record_kind] = json.loads(raw)
    decision = decoded["scheduler.rollover-decision"]
    edge = decoded["scheduler.rollover-edge"]
    assert decision["reciprocal_edge_id"] == result.edge.head
    assert edge["decision"] == result.decision.model_dump(mode="json")
    primitive = json.dumps(
        decision["primitive"], sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    assert decision["primitive_fingerprint"] == hashlib.sha256(primitive).hexdigest()
    assert prepare_rollover(current, command, issued, submission_id="submission") == result
    with pytest.raises(SchedulerDomainError):
        prepare_rollover(result.view, command, issued, submission_id="submission")


@pytest.mark.parametrize("field", ["lineage", "physical_root", "exhaustion"])
def test_every_fence_domain_is_exact(field: str) -> None:
    current, command, issued = fixture()
    nested = getattr(command.fence, field)
    field_name = {
        "lineage": "current_run_id",
        "physical_root": "current_epoch_head",
        "exhaustion": "hold_head",
    }[field]
    fence = command.fence.model_copy(
        update={field: nested.model_copy(update={field_name: "rival"})}
    )
    with pytest.raises(SchedulerDomainError):
        prepare_rollover(
            current, command.model_copy(update={"fence": fence}), issued, submission_id="submission"
        )


def test_proof_binds_predecessor_pair_authority_and_command_without_hash_cycle() -> None:
    current, command, issued = fixture()
    authority = command.fence.authority
    pair = RolloverPredecessor(
        decision=Present(head="decision", fingerprint="b" * 64),
        edge=Present(head="edge", fingerprint="c" * 64),
    )
    for changed in (
        authority.model_copy(update={"predecessor_rollover": pair}),
        authority.model_copy(update={"authority_head": "other"}),
        authority.model_copy(update={"command_id": "other"}),
    ):
        mutated = command.model_copy(
            update={"fence": command.fence.model_copy(update={"authority": changed})}
        )
        assert rollover_payload_fingerprint(mutated) != rollover_payload_fingerprint(command)
        with pytest.raises(SchedulerDomainError):
            prepare_rollover(current, mutated, issued, submission_id="submission")
    for field in ("proof_id", "proof_fingerprint", "command_payload_fingerprint"):
        mutated = command.model_copy(
            update={
                "fence": command.fence.model_copy(
                    update={"authority": authority.model_copy(update={field: "f" * 64})}
                )
            }
        )
        assert rollover_payload_fingerprint(mutated) == rollover_payload_fingerprint(command)
        with pytest.raises(SchedulerDomainError):
            prepare_rollover(current, mutated, issued, submission_id="submission")


def test_missing_proof_cut_snapshot_or_history_rejects() -> None:
    current, command, issued = fixture()
    for observation in (
        None,
        issued.model_copy(update={"submission_id": "other"}),
        issued.model_copy(update={"snapshot_fingerprint": "f" * 64}),
        issued.model_copy(update={"authority_epoch": "other"}),
        issued.model_copy(update={"used_epoch_ids": ()}),
        issued.model_copy(update={"used_lease_heads": ("exhausted",)}),
        issued.model_copy(update={"used_epoch_ids": ("epoch", "epoch")}),
    ):
        with pytest.raises(SchedulerDomainError):
            prepare_rollover(current, command, observation, submission_id="submission")


def test_each_predecessor_member_is_in_proof_scope() -> None:
    current, command, issued = fixture()
    pair = RolloverPredecessor(
        decision=Present(head="previous-decision", fingerprint="b" * 64),
        edge=Present(head="previous-edge", fingerprint="c" * 64),
    )
    current = current.model_copy(update={"predecessor_rollover": pair})
    authority = command.fence.authority.model_copy(update={"predecessor_rollover": pair})
    command = command.model_copy(
        update={"fence": command.fence.model_copy(update={"authority": authority})}
    )
    authority = authority.model_copy(
        update={"command_payload_fingerprint": rollover_payload_fingerprint(command)}
    )
    command = command.model_copy(
        update={"fence": command.fence.model_copy(update={"authority": authority})}
    )
    issued = issued.model_copy(
        update={
            "authority": authority,
            "snapshot_fingerprint": rollover_snapshot_fingerprint(current),
        }
    )
    prepare_rollover(current, command, issued, submission_id="submission")
    for field in ("decision", "edge"):
        mutated_pair = pair.model_copy(update={field: Present(head="rival", fingerprint="d" * 64)})
        mutated = command.model_copy(
            update={
                "fence": command.fence.model_copy(
                    update={
                        "authority": authority.model_copy(
                            update={"predecessor_rollover": mutated_pair}
                        )
                    }
                )
            }
        )
        assert rollover_payload_fingerprint(mutated) != rollover_payload_fingerprint(command)
        with pytest.raises(SchedulerDomainError):
            prepare_rollover(current, mutated, issued, submission_id="submission")


def test_epoch_and_genesis_aliases_reject_before_candidate() -> None:
    current, command, issued = fixture()
    result = prepare_rollover(current, command, issued, submission_id="submission")
    assert result.view.lease.kind == "UNLEASED"
    for observation in (
        issued.model_copy(
            update={"used_epoch_ids": ("epoch", result.view.physical_root.current_epoch_id)}
        ),
        issued.model_copy(
            update={"used_lease_heads": ("held-head", "exhausted", result.view.lease.lease_head)}
        ),
    ):
        with pytest.raises(SchedulerDomainError, match="aliases"):
            prepare_rollover(current, command, observation, submission_id="submission")


def test_maximum_selector_and_inconsistent_exhaustion_never_wrap() -> None:
    current, command, issued = fixture()
    physical = current.physical_root.model_copy(update={"selector_version": MAX})
    exhausted = current.model_copy(update={"physical_root": physical})
    changed = command.model_copy(
        update={"fence": command.fence.model_copy(update={"physical_root": physical})}
    )
    authority = changed.fence.authority.model_copy(
        update={"command_payload_fingerprint": rollover_payload_fingerprint(changed)}
    )
    changed = changed.model_copy(
        update={"fence": changed.fence.model_copy(update={"authority": authority})}
    )
    observed = issued.model_copy(
        update={
            "authority": authority,
            "snapshot_fingerprint": rollover_snapshot_fingerprint(exhausted),
        }
    )
    with pytest.raises(SchedulerDomainError, match="selector version exhausted"):
        prepare_rollover(exhausted, changed, observed, submission_id="submission")
    assert current.lease.kind == "GENERATION_EXHAUSTED_HOLD"
    bad = current.model_copy(
        update={"lease": current.lease.model_copy(update={"trusted_expiry": 101})}
    )
    with pytest.raises(SchedulerDomainError, match="exhaustion tuple"):
        prepare_rollover(bad, command, issued, submission_id="submission")
