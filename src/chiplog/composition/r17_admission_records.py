"""Selected, physical broker admission aggregate for the hermetic R17 CLI path.

The records here are deliberately members of the selected owner batch.  They are
not an inbox DTO: replay reconstructs them from selected journal bytes and the
matching physical rows.
"""

from __future__ import annotations

import json
from hashlib import sha256
from typing import Final, Literal

from chiplog.platform._ingress_contracts import (
    AdmissionBound,
    ClassReserve,
    Head,
    IngressDTO,
    ReadyGeneration,
)
from chiplog.platform._ingress_domain import selection_deadline
from chiplog.platform._owner_publication_contracts import (
    OwnerCommandBytes,
    OwnerRecordBytes,
    PlanEffectBatch,
)
from chiplog.platform.owner_publications import SelectedOwnerDecision, selected_records

SCHEMA_EPOCH: Final = "chiplog.ingress.admission-epoch.v1"
SCHEMA_EPOCH_V2: Final = "chiplog.ingress.admission-epoch.v2"
SCHEMA_CAPACITY: Final = "chiplog.ingress.admission-capacity.v1"
SCHEMA_READY: Final = "chiplog.ingress.admission-ready.v1"
_ORDINARY_CLAIM_DOMAIN = b"chiplog.ingress.admission.ordinary-claim.v1\x00"


def _canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


type CanonicalClass = Literal[
    "TELEGRAM_PUSH",
    "TELEGRAM_POLL",
    "CLI",
    "PROVIDER_CALLBACK",
    "PROVIDER_POLL",
    "RECONCILIATION",
    "TOOL_RESULT",
    "ORDINARY",
]
CANONICAL_CLASSES: tuple[CanonicalClass, ...] = (
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
    schema_id = value.model_dump(mode="json").get("schema_id")
    if not isinstance(schema_id, str):
        raise ValueError("admission record has no schema id")
    return OwnerRecordBytes(
        owner="broker_ingress",
        record_kind="ingress.admission",
        record_id=record_head(identity, value).head,
        schema_id=schema_id,
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


def hermetic_reserves_v2() -> tuple[ClassReserve, ...]:
    """V2 leaves custody classes unchanged and gives legacy PlanEffect its own bound."""
    return tuple(
        reserve.model_copy(
            update={
                "maximum_item_bytes": 8 * 1024 * 1024,
                "deficit_cap": 8 * 1024 * 1024,
                "byte_quantum": 64 * 1024,
                "physical_byte_reserve": 16 * 1024 * 1024,
            }
        )
        if reserve.source_class == "ORDINARY"
        else reserve
        for reserve in hermetic_reserves()
    )


class _EpochOpenedBase(IngressDTO):
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


class EpochOpened(_EpochOpenedBase):
    schema_id: Literal["chiplog.ingress.admission-epoch.v1"] = SCHEMA_EPOCH


class EpochOpenedV2(_EpochOpenedBase):
    """Versioned admission profile; v1 rows retain their original fixed reserves."""

    schema_id: Literal["chiplog.ingress.admission-epoch.v2"] = SCHEMA_EPOCH_V2


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


AdmissionEpoch = EpochOpened | EpochOpenedV2


class AdmissionAggregate(IngressDTO):
    """Reducer output from selected aggregate records only."""

    epoch: AdmissionEpoch | None
    claims: tuple[CapacityClaimed, ...]
    ready: tuple[ReadyAdmitted, ...]


def selected_aggregate(decisions: tuple[SelectedOwnerDecision, ...]) -> AdmissionAggregate:
    """Decode selected aggregate rows and reject missing, reordered or rival joins."""
    epoch: AdmissionEpoch | None = None
    claims: list[CapacityClaimed] = []
    ready: list[ReadyAdmitted] = []
    claim_heads: dict[str, Head] = {}
    for decision in decisions:
        for wire in selected_records(decision):
            if wire.schema_id in (SCHEMA_EPOCH, SCHEMA_EPOCH_V2):
                epoch_candidate = (
                    EpochOpenedV2.model_validate_json(wire.canonical_bytes)
                    if wire.schema_id == SCHEMA_EPOCH_V2
                    else EpochOpened.model_validate_json(wire.canonical_bytes)
                )
                if (
                    canonical(epoch_candidate) != wire.canonical_bytes
                    or wire.fingerprint != sha256(wire.canonical_bytes).hexdigest()
                    or wire.record_id
                    != record_head(
                        "ingress.admission.epoch:" + epoch_candidate.epoch.identity, epoch_candidate
                    ).head
                    or epoch is not None
                ):
                    raise ValueError("admission epoch is noncanonical or duplicated")
                epoch = epoch_candidate
            elif wire.schema_id == SCHEMA_CAPACITY:
                claim_candidate = CapacityClaimed.model_validate_json(wire.canonical_bytes)
                if (
                    canonical(claim_candidate) != wire.canonical_bytes
                    or claim_candidate.claim_id in claim_heads
                ):
                    raise ValueError("admission capacity claim is noncanonical or duplicated")
                head = record_head(claim_candidate.claim_id, claim_candidate)
                if head.head != wire.record_id or head.fingerprint != wire.fingerprint:
                    raise ValueError("capacity record physical identity differs")
                claim_heads[claim_candidate.claim_id] = head
                claims.append(claim_candidate)
            elif wire.schema_id == SCHEMA_READY:
                ready_candidate = ReadyAdmitted.model_validate_json(wire.canonical_bytes)
                if canonical(ready_candidate) != wire.canonical_bytes:
                    raise ValueError("ready admission is noncanonical")
                if ready_candidate.claim != claim_heads.get(ready_candidate.claim.identity):
                    raise ValueError("ready admission lacks its exact earlier capacity claim")
                ready.append(ready_candidate)
            elif wire.schema_id.startswith("chiplog.ingress.admission"):
                raise ValueError("unknown admission record schema")
    if epoch is not None and (
        epoch.canonical_class_order != CANONICAL_CLASSES
        or epoch.reserves
        != (hermetic_reserves_v2() if isinstance(epoch, EpochOpenedV2) else hermetic_reserves())
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
    epoch_schema: Literal["v1", "v2"] | None = None,
) -> tuple[IngressDTO, ...]:
    """Construct the new immutable aggregate members from selected predecessor state."""
    if epoch_schema is None:
        epoch_schema = "v1" if type(aggregate.epoch) is EpochOpened else "v2"
    reserves = hermetic_reserves_v2() if epoch_schema == "v2" else hermetic_reserves()
    if aggregate.epoch is None:
        epoch_type = EpochOpenedV2 if epoch_schema == "v2" else EpochOpened
        opened: AdmissionEpoch | None = epoch_type(
            tenant_id=tenant_id,
            database_id=database_id,
            epoch=epoch,
            fence=fence,
            manifest=manifest,
            canonical_class_order=CANONICAL_CLASSES,
            reserves=reserves,
        )
    else:
        opened = None
        if (
            aggregate.epoch.tenant_id != tenant_id
            or aggregate.epoch.database_id != database_id
            or aggregate.epoch.epoch != epoch
            or aggregate.epoch.fence != fence
            or aggregate.epoch.manifest != manifest
            or aggregate.epoch.reserves != reserves
            or (isinstance(aggregate.epoch, EpochOpenedV2) != (epoch_schema == "v2"))
        ):
            raise ValueError("admission epoch/fence/manifest changed")
    reserve = next(row for row in reserves if row.source_class == "CLI")
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


def _registered_ordinary_epoch(
    profile: object,
) -> tuple[str, str, Head, Head]:
    """Resolve the fixed shared epoch; CLI is a registry anchor, never an ordinary class."""
    from chiplog.composition.r17_ingress_registry import retained_cli_profile

    tenant_id = getattr(profile, "tenant_id", None)
    database_id = getattr(profile, "database_id", None)
    if not isinstance(tenant_id, str) or not isinstance(database_id, str):
        raise ValueError("ordinary admission requires the registered retained CLI profile")
    registered = retained_cli_profile(tenant_id, database_id)
    if profile != registered:
        raise ValueError("ordinary admission requires the exact registered retained CLI profile")
    return tenant_id, database_id, registered.epoch(), registered.head()


def _validate_ordinary_aggregate(
    aggregate: AdmissionAggregate,
    *,
    tenant_id: str,
    database_id: str,
    epoch: Head,
    manifest: Head,
) -> None:
    """Reject DTO-shaped predecessor state unless it is the exact hermetic aggregate."""
    if aggregate.epoch is None:
        if aggregate.claims or aggregate.ready:
            raise ValueError("admission aggregate has claims or ready rows without an epoch")
        return
    if (
        not isinstance(aggregate.epoch, EpochOpenedV2)
        or aggregate.epoch.tenant_id != tenant_id
        or aggregate.epoch.database_id != database_id
        or aggregate.epoch.epoch != epoch
        or aggregate.epoch.fence != 0
        or aggregate.epoch.manifest != manifest
        or aggregate.epoch.canonical_class_order != CANONICAL_CLASSES
        or aggregate.epoch.reserves != hermetic_reserves_v2()
    ):
        raise ValueError("admission aggregate epoch differs from the registered hermetic profile")
    reserves: dict[str, ClassReserve] = {
        reserve.source_class: reserve for reserve in hermetic_reserves_v2()
    }
    prior_by_class: dict[str, CapacityClaimed] = {}
    known_claims: dict[str, CapacityClaimed] = {}
    used_items: dict[str, int] = {source_class: 0 for source_class in CANONICAL_CLASSES}
    used_bytes: dict[str, int] = {source_class: 0 for source_class in CANONICAL_CLASSES}
    for claim in aggregate.claims:
        reserve = reserves.get(claim.source_class)
        if reserve is None:
            raise ValueError("admission aggregate has an unknown source class")
        expected_kind = "ORDINARY" if claim.source_class == "ORDINARY" else "ADMITTED_DURABLE"
        if (
            claim.epoch != epoch
            or claim.fence != 0
            or claim.occupancy_kind != expected_kind
            or claim.claim_id in known_claims
            or claim.predecessor
            != (
                record_head(
                    prior_by_class[claim.source_class].claim_id, prior_by_class[claim.source_class]
                )
                if claim.source_class in prior_by_class
                else None
            )
            or claim.accounted_bytes <= 0
            or claim.accounted_bytes > reserve.maximum_item_bytes
            or (
                claim.source_class == "ORDINARY"
                and (
                    claim.token_id is not None
                    or claim.custody_record is not None
                    or claim.inbox is not None
                )
            )
        ):
            raise ValueError(
                "admission aggregate claim differs from its exact class chain or reserve"
            )
        used_items[claim.source_class] += 1
        used_bytes[claim.source_class] += claim.accounted_bytes
        if (
            used_items[claim.source_class] > reserve.physical_item_reserve
            or used_bytes[claim.source_class] > reserve.physical_byte_reserve
        ):
            raise ValueError("admission aggregate exceeds a physical class reserve")
        known_claims[claim.claim_id] = claim
        prior_by_class[claim.source_class] = claim
    ready_generations: set[str] = set()
    for ready in aggregate.ready:
        prior_claim = known_claims.get(ready.claim.identity)
        if (
            prior_claim is None
            or ready.claim != record_head(prior_claim.claim_id, prior_claim)
            or ready.source_class != prior_claim.source_class
            or ready.generation.identity in ready_generations
            or ready.bound.epoch != epoch
            or ready.bound.fairness_version != "chiplog.r17.hermetic-deficit.v1"
            or ready.bound.formula_version != "chiplog.r17.hermetic-bound.v1"
            or ready.bound.canonical_class_order != CANONICAL_CLASSES
            or ready.bound.reserve != reserves[ready.source_class]
            or ready.bound.target.exact_head != ready.generation
            or ready.bound.target.durable_admission_commit_seq != ready.durable_admission_commit_seq
            or ready.bound.target.stable_tie_identity != ready.stable_tie_identity
            or ready.bound.target.accounted_bytes != prior_claim.accounted_bytes
        ):
            raise ValueError("admission aggregate ready row lacks its exact claim")
        ready_generations.add(ready.generation.identity)


def ordinary_plan_effect_events(
    *,
    batch: PlanEffectBatch,
    retained_cli_profile: object,
    aggregate: AdmissionAggregate,
) -> tuple[EpochOpened | EpochOpenedV2 | CapacityClaimed, ...]:
    """Build the pure ORDINARY admission prefix for one complete legacy PlanEffect batch."""
    tenant_id, database_id, epoch, manifest = _registered_ordinary_epoch(retained_cli_profile)
    if batch.effects_command.schema_id == "chiplog.effects.dispatch-preparation.v2":
        raise ValueError("dispatch effects batches cannot occupy the ordinary admission class")
    # Kept local: planning reads must stay independent of R17 admission records.
    from chiplog.composition.r16_planning_reads import validate_plan_effect_records

    validate_plan_effect_records(batch, tenant_id, batch.expected.tenant_frontier + 1)
    record_envelopes = tuple(row.model_dump(mode="json") for row in batch.complete_records)
    if (
        len(record_envelopes) != 6
        or sha256(_canonical_json(record_envelopes)).hexdigest() != batch.complete_batch_fingerprint
    ):
        raise ValueError("ordinary admission requires the complete original PlanEffect batch")
    _validate_ordinary_aggregate(
        aggregate,
        tenant_id=tenant_id,
        database_id=database_id,
        epoch=epoch,
        manifest=manifest,
    )
    reserve = next(row for row in hermetic_reserves_v2() if row.source_class == "ORDINARY")
    command_envelopes = (
        batch.planning_command.model_dump(mode="json"),
        batch.effects_command.model_dump(mode="json"),
    )
    raw_members: tuple[OwnerCommandBytes | OwnerRecordBytes, ...] = (
        batch.planning_command,
        batch.effects_command,
        *batch.complete_records,
    )
    accounted_bytes = sum(len(row.canonical_bytes) for row in raw_members)
    if accounted_bytes > reserve.maximum_item_bytes:
        raise ValueError("ordinary item exceeds its physical byte reserve")
    used = [claim for claim in aggregate.claims if claim.source_class == "ORDINARY"]
    if (
        sum(claim.accounted_bytes for claim in used) + accounted_bytes
        > reserve.physical_byte_reserve
    ):
        raise ValueError("ORDINARY physical byte reserve exhausted")
    if len(used) >= reserve.physical_item_reserve:
        raise ValueError("ORDINARY physical item reserve exhausted")
    claim_input = _canonical_json(
        {
            "domain": "chiplog.ingress.admission.ordinary-claim.v1",
            "schema_version": 1,
            "identity": batch.identity.model_dump(mode="json"),
            "commands": {"planning": command_envelopes[0], "effects": command_envelopes[1]},
            "records": record_envelopes,
        }
    )
    claim_id = (
        "ingress.admission.claim:ordinary:"
        + sha256(_ORDINARY_CLAIM_DOMAIN + claim_input).hexdigest()
    )
    claim = CapacityClaimed(
        claim_id=claim_id,
        epoch=epoch,
        fence=0,
        source_class="ORDINARY",
        token_id=None,
        custody_record=None,
        inbox=None,
        accounted_bytes=accounted_bytes,
        occupancy_kind="ORDINARY",
        predecessor=record_head(used[-1].claim_id, used[-1]) if used else None,
    )
    opened = (
        EpochOpenedV2(
            tenant_id=tenant_id,
            database_id=database_id,
            epoch=epoch,
            fence=0,
            manifest=manifest,
            canonical_class_order=CANONICAL_CLASSES,
            reserves=hermetic_reserves_v2(),
        )
        if aggregate.epoch is None
        else None
    )
    return tuple(item for item in (opened, claim) if item is not None)
