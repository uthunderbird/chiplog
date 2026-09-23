"""Owner preparation for independently authorized before-send dispositions.

No source authentication, IPC registration or durable publication occurs here.
The broker must authenticate and reproduce every input at the writer cut before
using these bytes. In particular this is not an alternate SEND authorization path.
"""

from .application import _bind_record
from .contracts import ExactHead, PreparedEffectPublication
from .denial_contracts import DenialPreparationRequest, SupersedeDecision
from .domain import EffectRuleViolation, require_semantics, require_transition


def prepare_denial(request: DenialPreparationRequest) -> PreparedEffectPublication:
    command, expected, current = request.command, request.expected, request.current
    identity, authority = command.identity, command.authority
    if (
        current.command_id != identity.command_id
        or current.command_fingerprint != identity.fingerprint
        or current.store_frontier != expected.tenant_head
        or identity.expected_tenant_head != expected.tenant_head
        or authority != current.authority
        or authority.fence != current.fence
        or authority.tenant_id != expected.tenant_id
        or authority.intent != command.intent
        or authority.expected_attempt != command.expected_attempt
    ):
        raise EffectRuleViolation("STALE", "denying operation or current observation differs")
    if (
        authority.clock_contract != current.clock_contract
        or authority.clock_epoch != current.clock_epoch
        or authority.valid_until_ns <= current.observed_time_ns
    ):
        raise EffectRuleViolation("STALE", "denying clock or authority lease is not current")
    for name in type(authority.sources).model_fields:
        source = getattr(authority.sources, name)
        if (
            source.clock_contract != current.clock_contract
            or source.clock_epoch != current.clock_epoch
            or source.valid_until_ns <= current.observed_time_ns
        ):
            raise EffectRuleViolation("STALE", "denying source lease or clock is not current")

    commands = tuple(row.command.command_id for row in expected.records)
    records = tuple(row.record for row in expected.records)
    if len(set(commands)) != len(commands) or len(set(records)) != len(records):
        raise EffectRuleViolation("STALE", "duplicate effects history identity")
    if identity.command_id in commands:
        raise EffectRuleViolation("CONFLICT", "existing identity requires authenticated replay")
    matches = tuple(
        row
        for row in expected.records
        if row.snapshot.intent.intent_id == command.intent.subject_id
    )
    if not matches:
        raise EffectRuleViolation("STALE", "denying subject is absent")
    previous = matches[-1]
    snapshot = previous.snapshot
    original = snapshot.intent
    exact_intent = ExactHead(
        subject_id=original.intent_id,
        head=original.intent_id + "/" + original.fingerprint,
        fingerprint=original.fingerprint,
    )
    if (
        command.intent != exact_intent
        or command.expected_attempt != snapshot.attempt
        or authority.tenant_id != original.authority.tenant_id
        or authority.principal_id != original.authority.principal_id
    ):
        raise EffectRuleViolation("STALE", "denying authority targets another immutable subject")
    require_semantics(original.semantics, current.supported_semantics, durable=True)
    # A new denial lease is independent of the historical SEND acquisition lease.
    # Never copy fresh values into original.authority or call require_current_authority.
    if snapshot.transmissions:
        raise EffectRuleViolation("DENIED", "a crossed transmission cannot become local no-send")
    if snapshot.state == "DISPATCH_AUTHORIZED":
        if (
            not snapshot.authorizations
            or command.authorization_to_retire != snapshot.authorizations[-1]
            or snapshot.authorizations[-1].intent != exact_intent
            or snapshot.authorizations[-1].semantics != original.semantics
        ):
            raise EffectRuleViolation("STALE", "exact latest authorization retirement required")
    elif command.authorization_to_retire is not None:
        raise EffectRuleViolation("STALE", "no live authorization to retire in this state")
    decision = authority.decision
    if isinstance(decision, SupersedeDecision) and (
        decision.successor_intent.subject_id == original.intent_id
    ):
        raise EffectRuleViolation("DENIED", "supersession requires a distinct adopted successor")
    require_transition(snapshot.state, decision.kind, writer="SEND_ADAPTER")
    return _bind_record(
        command,
        expected,
        previous,
        snapshot.model_copy(update={"state": decision.kind}),
        "BEFORE_SEND_DISPOSITION",
        (),
    )
