"""Ingress-rooted executable Run initialization, with no publication authority.

Continuation after an existing Turn requires the independent call-accounting and
frontier operation. Initial-turn creation cannot be used as that operation.
"""

from .contracts import BudgetPolicy, LoopRejected, VisibilityMember
from .delivery_contracts import OriginSelection
from .execution_contracts import ExecutionRunRecord, ExecutionTurn


def _current(run: ExecutionRunRecord) -> ExecutionRunRecord:
    run = ExecutionRunRecord.model_validate_json(run.canonical_bytes())
    if run.head != "loop:" + run.model_copy(update={"head": "pending"}).digest():
        raise LoopRejected("execution Run self head differs")
    return run


def _next(run: ExecutionRunRecord, event: str, **changes: object) -> ExecutionRunRecord:
    values = run.model_dump()
    values.update(changes)
    values.update(head="pending", predecessor=run.head, event=event)
    result = ExecutionRunRecord.model_validate(values)
    return result.model_copy(update={"head": "loop:" + result.digest()})


def create_ingress_execution_run(
    *,
    tenant: str,
    principal: str,
    run_id: str,
    prompt: str,
    policy: BudgetPolicy,
    origin: OriginSelection,
    contour_head: str,
    policy_head: str,
    worker_session: str,
) -> ExecutionRunRecord:
    """Initialize from runtime-authenticated inputs; construction authenticates none."""
    result = ExecutionRunRecord(
        tenant=tenant,
        principal=principal,
        run_id=run_id,
        prompt=prompt,
        policy=policy,
        origin=origin,
        contour_head=contour_head,
        policy_head=policy_head,
        worker_session=worker_session,
        root_binding="NOT_APPLICABLE",
        state="CREATED",
        head="pending",
        predecessor=None,
        turns=(),
        delivery_acceptance=None,
        suspension_baseline=None,
        original_obligations=(),
        no_retry_references=(),
        event="RunCreated",
    )
    return result.model_copy(update={"head": "loop:" + result.digest()})


def activate_execution_run(run: ExecutionRunRecord) -> ExecutionRunRecord:
    run = _current(run)
    if (
        run.state != "CREATED"
        or run.event != "RunCreated"
        or run.predecessor is not None
        or run.turns
        or run.delivery_acceptance is not None
        or run.suspension_baseline is not None
        or run.original_obligations
        or run.no_retry_references
        or run.root_binding != "NOT_APPLICABLE"
    ):
        raise LoopRejected("activation requires an original empty ingress Run")
    return _next(run, "RunActivated", state="ACTIVE")


def start_initial_execution_turn(run: ExecutionRunRecord) -> ExecutionRunRecord:
    run = _current(run)
    if (
        run.state != "ACTIVE"
        or run.event != "RunActivated"
        or run.turns
        or run.original_obligations
        or run.no_retry_references
        or run.suspension_baseline is not None
        or run.delivery_acceptance is not None
        or run.root_binding != "NOT_APPLICABLE"
    ):
        raise LoopRejected("initial Turn cannot replace continuation or recovery")
    turn = ExecutionTurn(
        turn_id=run.run_id + "/turn/1",
        ordinal=1,
        head="pending",
        state="PREPARING",
        accumulator=(),
        attempts=(),
        selector=0,
        response_seal=None,
        initialized_calls=None,
    )
    turn = turn.model_copy(update={"head": "execution-turn:" + turn.digest()})
    return _next(run, "TurnStarted", turns=(turn,))


def accumulate_execution_visibility(
    run: ExecutionRunRecord, members: tuple[VisibilityMember, ...]
) -> ExecutionRunRecord:
    run = _current(run)
    if run.state != "ACTIVE" or not run.turns:
        raise LoopRejected("visibility requires an active Turn")
    turn = run.turns[-1]
    if (
        turn.state != "PREPARING"
        or turn.attempts
        or turn.response_seal is not None
        or turn.initialized_calls is not None
    ):
        raise LoopRejected("cannot rewrite sealed execution visibility")
    by_id = {member.record_id: member for member in turn.accumulator}
    if len(by_id) != len(turn.accumulator):
        raise LoopRejected("duplicate original visibility identity")
    accumulated = list(turn.accumulator)
    for member in members:
        member = VisibilityMember.model_validate_json(member.canonical_bytes())
        if member.record_id in by_id:
            if member != by_id[member.record_id]:
                raise LoopRejected("execution visibility identity rebound")
        else:
            accumulated.append(member)
            by_id[member.record_id] = member
    turn = turn.model_copy(update={"head": "pending", "accumulator": tuple(accumulated)})
    turn = turn.model_copy(update={"head": "execution-turn:" + turn.digest()})
    return _next(run, "TurnVisibilityAccumulated", turns=(*run.turns[:-1], turn))
