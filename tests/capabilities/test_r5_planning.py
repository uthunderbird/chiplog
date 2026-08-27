from __future__ import annotations

from dataclasses import replace

import pytest

from chiplog.capabilities.planning import (
    CreateIntentionLine,
    InvocationContext,
    PlanningCommands,
    PlanningTrustDecision,
    PlanningTrustReference,
    TrustRevalidationPort,
)
from chiplog.capabilities.planning._planning import (
    CREATE_INTENTION_LINE,
    PLANNING_RECORD_TYPES,
    _InMemoryPlanningRepository,
    _PlanningOwnerFactory,
    _PlanningUseCase,
)
from chiplog.domain_primitives import PermissionScope, PrincipalId, RecordId, TenantId

TENANT = TenantId("tenant-1")
PRINCIPAL = PrincipalId("principal-1")


class TrustFake:
    def __init__(self, disposition: str = "VALID") -> None:
        self.disposition = disposition
        self.calls: list[tuple[PlanningTrustReference, str, RecordId]] = []
        self.before_return: object | None = None

    def revalidate(
        self, reference: PlanningTrustReference, operation: str, subject_id: RecordId
    ) -> PlanningTrustDecision:
        self.calls.append((reference, operation, subject_id))
        if callable(self.before_return):
            self.before_return()
        return PlanningTrustDecision(self.disposition, None)  # type: ignore[arg-type]


def context(
    *, tenant: TenantId = TENANT, sequence: int = 0, contour: str = "CLI"
) -> InvocationContext:
    reference = PlanningTrustReference(
        tenant,
        PRINCIPAL,
        contour,
        "credential-head",
        "session-head",
        "source-head",
        "trust-head",
        "materialization-head",
        sequence,
    )
    return InvocationContext(
        tenant, PRINCIPAL, PermissionScope("planning.create_intention_line"), reference
    )


def command(
    *,
    tenant: TenantId = TENANT,
    command_value: str = "command-1",
    line_value: str = "line-1",
    revision_value: str = "revision-1",
    purpose: str = "Ship the first trustworthy slice",
) -> CreateIntentionLine:
    return CreateIntentionLine(
        RecordId(tenant, command_value),
        RecordId(tenant, line_value),
        RecordId(tenant, revision_value),
        purpose,
        "principal-act-1",
    )


def test_ports_are_structural_protocols() -> None:
    repository = _InMemoryPlanningRepository()
    trust = TrustFake()
    inbound: PlanningCommands = _PlanningUseCase(repository, trust)
    outbound: TrustRevalidationPort = trust
    assert inbound.execute(context(), command()).disposition == "COMMITTED"
    assert len(outbound.calls) == 1  # type: ignore[attr-defined]


def test_owner_factory_publishes_complete_closed_manifest() -> None:
    repository = _InMemoryPlanningRepository()
    outcome = _PlanningUseCase(repository, TrustFake()).execute(context(), command())
    assert outcome.disposition == "COMMITTED"
    publication = repository.committed_publications(TENANT)[0]
    assert {record.record_type_id for record in publication.records} == set(PLANNING_RECORD_TYPES)
    revision = publication.records[3]
    assert revision.fields["activity"] == "ACTIVE"
    assert revision.fields["personal_outcome"] == "OPEN"
    assert revision.fields["predecessor_revision_id"] is None
    assert outcome.result is not None
    assert [ordinal for ordinal, _, _ in outcome.result.allocation_manifest] == [0, 1]
    assert outcome.result.record_manifest == publication.record_manifest
    assert outcome.result.batch_fingerprint == publication.batch_fingerprint
    assert len(outcome.result.record_manifest) == 5
    assert len(publication.records[4].fields["record_manifest"]) == 4  # type: ignore[arg-type]
    assert publication.records[4].fields["batch_fingerprint"]
    with pytest.raises(TypeError):
        revision.fields["purpose"] = "mutated"  # type: ignore[index]


@pytest.mark.parametrize(
    ("bad_context", "bad_command", "reason"),
    [
        (context(), command(tenant=TenantId("foreign")), "foreign tenant"),
        (
            replace(
                context(),
                trust_reference=replace(
                    context().trust_reference, principal_id=PrincipalId("other")
                ),
            ),
            command(),
            "principal mismatch",
        ),
        (
            replace(context(), permission_scope=PermissionScope("planning.read")),
            command(),
            "forbidden actor",
        ),
        (context(contour="EVIDENCE"), command(), "wrong direct-principal source"),
        (
            context(),
            replace(command(), intention_line_id=RecordId(TENANT, "command-1")),
            "distinct",
        ),
        (context(), replace(command(), purpose=" trailing "), "normalized"),
    ],
)
def test_construction_rejects_foreign_wrong_source_and_forbidden_predecessor(
    bad_context: InvocationContext,
    bad_command: CreateIntentionLine,
    reason: str,
) -> None:
    repository = _InMemoryPlanningRepository()
    result = _PlanningUseCase(repository, TrustFake()).execute(bad_context, bad_command)
    assert result.disposition == "DENIED"
    assert reason in str(result.reason)
    assert repository.committed_publications(TENANT) == ()


def test_create_intention_line_public_port_has_no_predecessor_escape_hatch() -> None:
    use_case = _PlanningUseCase(_InMemoryPlanningRepository(), TrustFake())
    with pytest.raises(TypeError):
        use_case.execute(  # type: ignore[call-arg]
            context(), command(), predecessor_result_id=RecordId(TENANT, "prior-result")
        )


@pytest.mark.parametrize("disposition", ["DENIED", "STALE", "INDETERMINATE"])
def test_nonvalid_trust_is_a_typed_no_write(disposition: str) -> None:
    repository = _InMemoryPlanningRepository()
    result = _PlanningUseCase(repository, TrustFake(disposition)).execute(context(), command())
    assert result.disposition == disposition
    assert repository.committed_publications(TENANT) == ()


def test_exact_replay_returns_result_and_changed_replay_conflicts() -> None:
    repository = _InMemoryPlanningRepository()
    use_case = _PlanningUseCase(repository, TrustFake())
    first = use_case.execute(context(), command())
    replay = use_case.execute(context(), command())
    conflict = use_case.execute(context(), replace(command(), purpose="Changed"))
    assert first.disposition == "COMMITTED"
    assert replay.disposition == "REPLAY"
    assert replay.result == first.result
    assert conflict.disposition == "CONFLICT"
    assert len(repository.committed_publications(TENANT)) == 1


@pytest.mark.parametrize("disposition", ["DENIED", "STALE", "INDETERMINATE"])
def test_replay_revalidates_current_trust_and_withholds_result(disposition: str) -> None:
    repository = _InMemoryPlanningRepository()
    trust = TrustFake()
    use_case = _PlanningUseCase(repository, trust)
    assert use_case.execute(context(), command()).disposition == "COMMITTED"
    trust.disposition = disposition
    replay = use_case.execute(context(), command())
    assert replay.disposition == disposition
    assert replay.result is None
    assert len(repository.committed_publications(TENANT)) == 1


def test_stale_head_and_revalidation_race_write_nothing() -> None:
    repository = _InMemoryPlanningRepository()
    trust = TrustFake()
    trust.before_return = lambda: repository.invalidate_head(TENANT)
    raced = _PlanningUseCase(repository, trust).execute(context(), command())
    assert raced.disposition == "STALE"
    assert repository.committed_publications(TENANT) == ()


def test_trust_revalidation_binds_exact_operation_and_allocation_subject() -> None:
    repository = _InMemoryPlanningRepository()
    trust = TrustFake()
    _PlanningUseCase(repository, trust).execute(context(), command())
    assert trust.calls == [
        (context().trust_reference, CREATE_INTENTION_LINE, command().intention_line_id)
    ]


def test_two_allocations_can_share_one_still_valid_trust_reference() -> None:
    repository = _InMemoryPlanningRepository()
    use_case = _PlanningUseCase(repository, TrustFake())
    assert use_case.execute(context(), command()).disposition == "COMMITTED"
    second = command(command_value="command-2", line_value="line-2", revision_value="revision-2")
    assert use_case.execute(context(), second).disposition == "COMMITTED"


def test_repository_never_constructs_owner_records() -> None:
    class FactorySpy(_PlanningOwnerFactory):
        calls = 0

        def construct(self, *args: object, **kwargs: object):  # type: ignore[no-untyped-def]
            self.calls += 1
            return super().construct(*args, **kwargs)  # type: ignore[arg-type]

    repository = _InMemoryPlanningRepository()
    factory = FactorySpy()
    _PlanningUseCase(repository, TrustFake(), factory).execute(context(), command())
    assert factory.calls == 1
    assert not hasattr(repository, "construct")
