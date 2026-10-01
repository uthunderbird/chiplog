"""Selected, physical broker admission aggregate for the hermetic R17 CLI path.

The records here are deliberately members of the selected owner batch.  They are
not an inbox DTO: replay reconstructs them from selected journal bytes and the
matching physical rows.
"""

from __future__ import annotations

import json
from hashlib import sha256
from typing import Literal

from chiplog.platform._ingress_contracts import (
    AdmissionBound,
    ClassReserve,
    Head,
    IngressDTO,
    ReadyGeneration,
)
from chiplog.platform._ingress_domain import selection_deadline
from chiplog.platform._owner_publication_contracts import OwnerRecordBytes
from chiplog.platform.owner_publications import SelectedOwnerDecision, selected_records

SCHEMA_EPOCH = "chiplog.ingress.admission-epoch.v1"
SCHEMA_CAPACITY = "chiplog.ingress.admission-capacity.v1"
SCHEMA_READY = "chiplog.ingress.admission-ready.v1"

CANONICAL_CLASSES = (
    "TELEGRAM_PUSH",
    "TELEGRAM_POLL",
    "CLI",
    "PROVIDER_CALLBACK",
    "PROVIDER_POLL",
    "RECONCILIATION",
    "TOOL_RESULT",
    "ORDINARY",
)


def canonical(value: IngressDTO) -> bytes:
    return json.dumps(
        value.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()


def fingerprint(value: IngressDTO) -> str:
    return sha256(canonical(value)).hexdigest()


def record_head(identity: str, value: IngressDTO) -> Head:
    digest = fingerprint(value)
    return Head(identity=identity, head=identity + "/" + digest, fingerprint=digest)


def wire(identity: str, value: IngressDTO) -> OwnerRecordBytes:
    raw = canonical(value)
    return OwnerRecordBytes(
        owner="broker_ingress",
        record_kind="ingress.admission",
        record_id=record_head(identity, value).head,
        schema_id=value.schema_id,
        canonical_bytes=raw,
        fingerprint=sha256(raw).hexdigest(),
    )


def hermetic_reserves() -> tuple[ClassReserve, ...]:
    """Small fixed test profile; every class remains independently nonborrowable."""
    return tuple(
        ClassReserve(
            source_class=source_class,
            byte_quantum=1024,
            maximum_item_bytes=65536,
            deficit_cap=65536,
            physical_item_reserve=2,
            physical_byte_reserve=131072,
            physical_quarantine_reserve=1,
            ready_depth_limit=2,
            descendant_snapshot_byte_limit=262144,
        )
        for source_class in CANONICAL_CLASSES
    )


class EpochOpened(IngressDTO):
    schema_id: Literal["chiplog.ingress.admission-epoch.v1"] = SCHEMA_EPOCH
    tenant_id: str
    database_id: str
    epoch: Head
    fence: int
    manifest: Head
    canonical_class_order: tuple[
        Literal[
            "TELEGRAM_PUSH",
            "TELEGRAM_POLL",
            "CLI",
            "PROVIDER_CALLBACK",
            "PROVIDER_POLL",
            "RECONCILIATION",
            "TOOL_RESULT",
            "ORDINARY",
        ],
        ...,
    ]
    reserves: tuple[ClassReserve, ...]
    fairness_version: str = "chiplog.r17.hermetic-deficit.v1"
    formula_version: str = "chiplog.r17.hermetic-bound.v1"


class CapacityClaimed(IngressDTO):
    schema_id: Literal["chiplog.ingress.admission-capacity.v1"] = SCHEMA_CAPACITY
    claim_id: str
    epoch: Head
    fence: int
    source_class: str
    token_id: str | None
    custody_record: Head | None
    inbox: Head | None
    accounted_bytes: int
    occupancy_kind: Literal["ADMITTED_DURABLE", "ORDINARY"]
    predecessor: Head | None


class ReadyAdmitted(IngressDTO):
    schema_id: Literal["chiplog.ingress.admission-ready.v1"] = SCHEMA_READY
    claim: Head
    source_class: str
    durable_admission_commit_seq: int
    stable_tie_identity: str
    generation: Head
    bound: AdmissionBound


class AdmissionAggregate(IngressDTO):
    """Reducer output from selected aggregate records only."""

    epoch: EpochOpened | None
    claims: tuple[CapacityClaimed, ...]
    ready: tuple[ReadyAdmitted, ...]


def selected_aggregate(decisions: tuple[SelectedOwnerDecision, ...]) -> AdmissionAggregate:
    """Decode selected aggregate rows and reject missing, reordered or rival joins."""
    epoch: EpochOpened | None = None
    claims: list[CapacityClaimed] = []
    ready: list[ReadyAdmitted] = []
    claim_heads: dict[str, Head] = {}
    for decision in decisions:
        for wire in selected_records(decision):
            if wire.schema_id == SCHEMA_EPOCH:
                candidate = EpochOpened.model_validate_json(wire.canonical_bytes)
                if canonical(candidate) != wire.canonical_bytes or epoch is not None:
                    raise ValueError("admission epoch is noncanonical or duplicated")
                epoch = candidate
            elif wire.schema_id == SCHEMA_CAPACITY:
                candidate = CapacityClaimed.model_validate_json(wire.canonical_bytes)
                if (
                    canonical(candidate) != wire.canonical_bytes
                    or candidate.claim_id in claim_heads
                ):
                    raise ValueError("admission capacity claim is noncanonical or duplicated")
                head = record_head(candidate.claim_id, candidate)
                if head.head != wire.record_id or head.fingerprint != wire.fingerprint:
                    raise ValueError("capacity record physical identity differs")
                claim_heads[candidate.claim_id] = head
                claims.append(candidate)
            elif wire.schema_id == SCHEMA_READY:
                candidate = ReadyAdmitted.model_validate_json(wire.canonical_bytes)
                if canonical(candidate) != wire.canonical_bytes:
                    raise ValueError("ready admission is noncanonical")
                if candidate.claim != claim_heads.get(candidate.claim.identity):
                    raise ValueError("ready admission lacks its exact earlier capacity claim")
                ready.append(candidate)
    if epoch is not None and (
        epoch.canonical_class_order != CANONICAL_CLASSES or epoch.reserves != hermetic_reserves()
    ):
        raise ValueError("admission epoch has another canonical reserve profile")
    if len({item.claim_id for item in claims}) != len(claims):
        raise ValueError("duplicate capacity claim")
    if len({item.generation.identity for item in ready}) != len(ready):
        raise ValueError("duplicate ready generation")
    return AdmissionAggregate(epoch=epoch, claims=tuple(claims), ready=tuple(ready))


def cli_batch_events(
    *,
    tenant_id: str,
    database_id: str,
    epoch: Head,
    fence: int,
    manifest: Head,
    command_id: str,
    token_id: str,
    custody_record: Head,
    inbox: Head,
    raw_bytes: bytes,
    commit_sequence: int,
    aggregate: AdmissionAggregate,
) -> tuple[IngressDTO, ...]:
    """Construct the new immutable aggregate members from selected predecessor state."""
    if aggregate.epoch is None:
        opened: EpochOpened | None = EpochOpened(
            tenant_id=tenant_id,
            database_id=database_id,
            epoch=epoch,
            fence=fence,
            manifest=manifest,
            canonical_class_order=CANONICAL_CLASSES,
            reserves=hermetic_reserves(),
        )
    else:
        opened = None
        if (
            aggregate.epoch.tenant_id != tenant_id
            or aggregate.epoch.database_id != database_id
            or aggregate.epoch.epoch != epoch
            or aggregate.epoch.fence != fence
            or aggregate.epoch.manifest != manifest
        ):
            raise ValueError("admission epoch/fence/manifest changed")
    reserve = next(row for row in hermetic_reserves() if row.source_class == "CLI")
    used = [claim for claim in aggregate.claims if claim.source_class == "CLI"]
    if len(used) >= reserve.physical_item_reserve:
        raise ValueError("CLI physical item reserve exhausted")
    if (
        sum(claim.accounted_bytes for claim in used) + len(raw_bytes)
        > reserve.physical_byte_reserve
    ):
        raise ValueError("CLI physical byte reserve exhausted")
    claim_id = "ingress.admission.claim:" + token_id
    predecessor = record_head(used[-1].claim_id, used[-1]) if used else None
    claim = CapacityClaimed(
        claim_id=claim_id,
        epoch=epoch,
        fence=fence,
        source_class="CLI",
        token_id=token_id,
        custody_record=custody_record,
        inbox=inbox,
        accounted_bytes=len(raw_bytes),
        occupancy_kind="ADMITTED_DURABLE",
        predecessor=predecessor,
    )
    claim_head = record_head(claim_id, claim)
    predecessors = tuple(
        ReadyGeneration(
            stable_work_id=row.generation.identity,
            durable_admission_commit_seq=row.durable_admission_commit_seq,
            stable_tie_identity=row.stable_tie_identity,
            exact_head=row.generation,
            accounted_bytes=next(
                item.accounted_bytes
                for item in aggregate.claims
                if record_head(item.claim_id, item) == row.claim
            ),
        )
        for row in aggregate.ready
        if row.source_class == "CLI"
    )
    generation = ReadyGeneration(
        stable_work_id="ingress.ready:" + token_id,
        durable_admission_commit_seq=commit_sequence,
        stable_tie_identity=token_id,
        exact_head=inbox,
        accounted_bytes=len(raw_bytes),
    )
    draft_bound = AdmissionBound(
        bound_id="ingress.admission.bound:" + token_id,
        bound_lineage_id="ingress.admission.lineage:CLI",
        epoch=epoch,
        fairness_version="chiplog.r17.hermetic-deficit.v1",
        formula_version="chiplog.r17.hermetic-bound.v1",
        canonical_class_order=CANONICAL_CLASSES,
        reserve=reserve,
        initial_deficit=0,
        origin_scheduler_slot=commit_sequence,
        complete_ordered_predecessors=predecessors,
        target=generation,
        # The immutable deadline is calculated below from this complete prefix.
        absolute_selection_deadline_slot=0,
    )
    deadline, _ = selection_deadline(draft_bound)
    bound = draft_bound.model_copy(update={"absolute_selection_deadline_slot": deadline})
    ready = ReadyAdmitted(
        claim=claim_head,
        source_class="CLI",
        durable_admission_commit_seq=commit_sequence,
        stable_tie_identity=token_id,
        generation=inbox,
        bound=bound,
    )
    return tuple(item for item in (opened, claim, ready) if item is not None)
