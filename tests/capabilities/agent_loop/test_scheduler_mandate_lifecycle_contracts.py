"""Consumer checks for closed scheduled-mandate lifecycle wires."""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError
from tests.support.scheduler_execution import DIGEST, call_head
from tests.support.scheduler_mandate_lifecycle import (
    admin_source,
    issue,
    prepared_issue,
    reference,
    selected_budget,
    selected_mandate,
)

from chiplog.capabilities.agent_loop.recovery_contracts import Absent
from chiplog.capabilities.agent_loop.scheduler_execution_contracts import (
    ScheduledMandateBudgetV1,
    ScheduledMandateRevocationV1,
    ScheduledSystemMandateV2,
    SelectedScheduledMandateBudgetV1,
    SelectedScheduledSystemMandateV2,
)
from chiplog.capabilities.agent_loop.scheduler_mandate_lifecycle_contracts import (
    IssueScheduledMandateResultV1,
    IssueScheduledMandateV1,
    PreparedMandateRevocationV1,
    PreparedMandateSupersessionV1,
    ProposedScheduledMandateRevocationV1,
    RevokeScheduledMandateV1,
    ScheduledMandateLifecycleRequestV1,
    SupersedeScheduledMandateV1,
    mandate_admission_is_fresh,
)


def _terminal_revocation(
    selected: SelectedScheduledSystemMandateV2,
) -> ProposedScheduledMandateRevocationV1:
    body = ScheduledMandateRevocationV1(
        mandate_id=selected.mandate.scope.mandate_id,
        mandate_head=selected.mandate_head,
        predecessor=Absent(),
        revocation_observation=call_head("revocation-observation"),
    )
    return _proposed_revocation(body)


def _proposed_revocation(
    body: ScheduledMandateRevocationV1,
) -> ProposedScheduledMandateRevocationV1:
    raw = body.canonical_bytes()
    return ProposedScheduledMandateRevocationV1(
        proposed_record_id="revocation/record",
        canonical_revocation_bytes=raw,
        revocation=body,
        external_reference=reference("revocation/record", raw),
    )


def _refresh_proposed_mandate(proposed: dict[str, Any]) -> None:
    original = ScheduledSystemMandateV2.model_validate_json(
        base64.b64decode(proposed["canonical_mandate_bytes"])
    )
    proposed_scope = proposed["mandate"]["scope"]
    scope = original.scope.model_copy(
        update={
            "mandate_id": proposed_scope["mandate_id"],
            "beneficiary_principal": proposed_scope["beneficiary_principal"],
        }
    )
    mandate = original.model_copy(update={"scope": scope})
    proposed["mandate"] = mandate.model_dump(mode="json")
    raw = mandate.canonical_bytes()
    proposed["canonical_mandate_bytes"] = base64.b64encode(raw).decode()
    proposed["external_reference"]["fingerprint"] = hashlib.sha256(raw).hexdigest()


def _refresh_proposed_budget(proposed: dict[str, Any]) -> None:
    original = ScheduledMandateBudgetV1.model_validate_json(
        base64.b64decode(proposed["canonical_budget_bytes"])
    )
    proposed_budget = proposed["budget"]
    budget = original.model_copy(
        update={
            "generation": proposed_budget["generation"],
            "cumulative_cycles": proposed_budget["cumulative_cycles"],
            "cumulative_runs": proposed_budget["cumulative_runs"],
            "cumulative_consequential_calls": proposed_budget["cumulative_consequential_calls"],
        }
    )
    proposed["budget"] = budget.model_dump(mode="json")
    proposed["canonical_budget_bytes"] = base64.b64encode(budget.canonical_bytes()).decode()


def _json_payload(value: Any) -> bytes:
    return json.dumps(value, separators=(",", ":")).encode()


@pytest.mark.parametrize("capacity", [0, 3])
def test_issue_represents_zero_and_nonzero_capacity_genesis_without_a_selected_self_head(
    capacity: int,
) -> None:
    selected = selected_mandate(max_cycles=capacity)
    prepared = prepared_issue(selected)
    parsed: object = TypeAdapter(IssueScheduledMandateResultV1).validate_json(
        prepared.canonical_bytes()
    )
    assert parsed == prepared
    assert prepared.budget_genesis.budget.generation == 0
    assert prepared.budget_genesis.budget.cumulative_cycles == 0
    assert prepared.mandate.mandate.scope.horizon.max_cycles == capacity
    assert prepared.mandate.proposed_record_id != prepared.mandate.mandate.scope.mandate_id


def test_issue_json_mutants_reach_absence_id_and_tenant_joins() -> None:
    baseline = issue()
    assert IssueScheduledMandateV1.model_validate_json(baseline.canonical_bytes()) == baseline

    absence_payload = json.loads(baseline.canonical_bytes())
    absence_payload["expected_absences"]["mandate_record"] = {
        "kind": "PRESENT",
        "head": "x",
        "fingerprint": DIGEST,
    }
    with pytest.raises(ValidationError):
        IssueScheduledMandateV1.model_validate_json(_json_payload(absence_payload))

    mandate_id_payload = json.loads(baseline.canonical_bytes())
    mandate_id_payload["proposed_mandate"]["mandate"]["scope"]["mandate_id"] = "other"
    mandate_id_payload["administrative_source"]["semantic_scope"] = mandate_id_payload[
        "proposed_mandate"
    ]["mandate"]["scope"]
    _refresh_proposed_mandate(mandate_id_payload["proposed_mandate"])
    with pytest.raises(ValidationError, match="command and proposed mandate IDs differ"):
        IssueScheduledMandateV1.model_validate_json(_json_payload(mandate_id_payload))

    tenant_payload = json.loads(baseline.canonical_bytes())
    tenant_payload["administrative_source"]["tenant_id"] = "other-tenant"
    with pytest.raises(ValidationError, match="source tenant differs"):
        IssueScheduledMandateV1.model_validate_json(_json_payload(tenant_payload))


def test_issue_requires_all_three_named_absences_and_zero_genesis() -> None:
    baseline = issue()
    assert IssueScheduledMandateV1.model_validate_json(baseline.canonical_bytes()) == baseline
    payload = json.loads(baseline.canonical_bytes())
    del payload["expected_absences"]["budget_record"]
    with pytest.raises(ValidationError):
        IssueScheduledMandateV1.model_validate_json(_json_payload(payload))

    prepared_baseline = prepared_issue()
    assert (
        TypeAdapter(IssueScheduledMandateResultV1).validate_json(
            prepared_baseline.canonical_bytes()
        )
        == prepared_baseline
    )
    prepared = json.loads(prepared_baseline.canonical_bytes())
    prepared["budget_genesis"]["budget"]["cumulative_runs"] = 1
    _refresh_proposed_budget(prepared["budget_genesis"])
    with pytest.raises(ValidationError):
        TypeAdapter(IssueScheduledMandateResultV1).validate_json(_json_payload(prepared))


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("budget_genesis", "budget", "generation"), 1),
        (("budget_genesis", "budget", "cumulative_cycles"), 1),
        (("budget_genesis", "budget", "cumulative_runs"), 1),
        (("budget_genesis", "budget", "cumulative_consequential_calls"), 1),
        (("budget_genesis", "canonical_budget_bytes"), "Zm9yZ2Vk"),
    ],
)
def test_issuance_rejects_budget_generation_counter_and_bytes_mutants(
    path: tuple[str, ...], value: object
) -> None:
    baseline = prepared_issue()
    assert (
        TypeAdapter(IssueScheduledMandateResultV1).validate_json(baseline.canonical_bytes())
        == baseline
    )
    payload = json.loads(baseline.canonical_bytes())
    cursor = payload
    for key in path[:-1]:
        cursor = cursor[key]
    cursor[path[-1]] = value
    if path[-1] != "canonical_budget_bytes":
        _refresh_proposed_budget(payload["budget_genesis"])
    with pytest.raises(ValidationError):
        TypeAdapter(IssueScheduledMandateResultV1).validate_json(_json_payload(payload))


def test_issuance_rejects_hash_recomputed_basis_mandate_id_mutant() -> None:
    prepared = prepared_issue()
    budget = prepared.budget_genesis.budget
    basis = budget.consumption_basis.model_copy(update={"mandate_id": "other"})
    changed_budget = budget.model_copy(update={"consumption_basis": basis})
    changed_proposal = prepared.budget_genesis.model_copy(
        update={
            "budget": changed_budget,
            "canonical_budget_bytes": changed_budget.canonical_bytes(),
        }
    )
    with pytest.raises(ValueError, match="zero-counter absent-predecessor genesis"):
        type(prepared)(
            source_request_fingerprint=prepared.source_request_fingerprint,
            mandate=prepared.mandate,
            budget_genesis=changed_proposal,
        )


def test_revocation_preserves_exact_selected_bytes_and_emits_one_terminal_record() -> None:
    selected = selected_mandate()
    budget = selected_budget(selected)
    request = RevokeScheduledMandateV1(
        command_id="revoke-command",
        selected_mandate=selected,
        selected_budget=budget,
        expected_revocation_absent=Absent(),
        administrative_source=admin_source(selected),
        reason="operator-request",
        reference="ticket-1",
        expected_authority_head=admin_source(selected).current_authority_head,
    )
    result = PreparedMandateRevocationV1(
        source_request_fingerprint=DIGEST,
        selected_mandate=request.selected_mandate,
        selected_budget=request.selected_budget,
        terminal_revocation=_terminal_revocation(selected),
    )
    assert RevokeScheduledMandateV1.model_validate_json(request.canonical_bytes()) == request
    assert PreparedMandateRevocationV1.model_validate_json(result.canonical_bytes()) == result
    assert result.terminal_revocation.revocation.mandate_head == selected.mandate_head

    payload = json.loads(request.canonical_bytes())
    payload["selected_mandate"]["canonical_mandate_bytes"] = "Zm9yZ2Vk"
    with pytest.raises(ValidationError, match="selected mandate bytes"):
        RevokeScheduledMandateV1.model_validate_json(_json_payload(payload))

    result_payload = json.loads(result.canonical_bytes())
    result_payload["selected_mandate"]["mandate_head"]["head"] = "other/head"
    with pytest.raises(ValidationError, match="terminal revocation"):
        PreparedMandateRevocationV1.model_validate_json(_json_payload(result_payload))

    changed_body = budget.budget.model_copy(update={"mandate_id": "other"})
    changed_raw = changed_body.canonical_bytes()
    changed_selected_budget = SelectedScheduledMandateBudgetV1(
        budget_head=reference("other/budget", changed_raw),
        canonical_budget_bytes=changed_raw,
        budget=changed_body,
    )
    with pytest.raises(ValueError, match="selected mandate"):
        PreparedMandateRevocationV1(
            source_request_fingerprint=DIGEST,
            selected_mandate=selected,
            selected_budget=changed_selected_budget,
            terminal_revocation=_terminal_revocation(selected),
        )


def test_supersession_closes_old_lineage_and_uses_new_id_genesis() -> None:
    old = selected_mandate(generation=0, mandate_id="old")
    new = selected_mandate(generation=1, mandate_id="new")
    new_issue = issue(new)
    request = SupersedeScheduledMandateV1(
        command_id="supersede-command",
        old_selected_mandate=old,
        old_selected_budget=selected_budget(old),
        old_expected_revocation_absent=Absent(),
        terminal_revocation=_terminal_revocation(old),
        new_issue=new_issue,
        administrative_source=new_issue.administrative_source,
        expected_authority_head=new_issue.administrative_source.current_authority_head,
    )
    result = PreparedMandateSupersessionV1(
        source_request_fingerprint=DIGEST,
        old_selected_mandate=old,
        old_selected_budget=selected_budget(old),
        terminal_revocation=_terminal_revocation(old),
        new_issuance=prepared_issue(new),
    )
    assert SupersedeScheduledMandateV1.model_validate_json(request.canonical_bytes()) == request
    assert PreparedMandateSupersessionV1.model_validate_json(result.canonical_bytes()) == result
    assert result.new_issuance.budget_genesis.budget.mandate_id == "new"

    same_id_payload = json.loads(request.canonical_bytes())
    same_id_payload["new_issue"]["mandate_id"] = "old"
    same_id_payload["new_issue"]["proposed_mandate"]["mandate"]["scope"]["mandate_id"] = "old"
    same_id_payload["new_issue"]["administrative_source"]["semantic_scope"] = same_id_payload[
        "new_issue"
    ]["proposed_mandate"]["mandate"]["scope"]
    same_id_payload["administrative_source"]["semantic_scope"] = same_id_payload[
        "new_issue"
    ]["proposed_mandate"]["mandate"]["scope"]
    _refresh_proposed_mandate(same_id_payload["new_issue"]["proposed_mandate"])
    with pytest.raises(ValidationError, match="distinct mandate ID"):
        SupersedeScheduledMandateV1.model_validate_json(_json_payload(same_id_payload))

    changed_old = old.model_copy(
        update={
            "canonical_mandate_bytes": b"not-the-selected-canonical-mandate",
            "mandate_head": reference("old/head", b"not-the-selected-canonical-mandate"),
        }
    )
    with pytest.raises(ValueError, match="selected mandate bytes"):
        SupersedeScheduledMandateV1(
            command_id="supersede-command",
            old_selected_mandate=changed_old,
            old_selected_budget=selected_budget(old),
            old_expected_revocation_absent=Absent(),
            terminal_revocation=_terminal_revocation(old),
            new_issue=new_issue,
            administrative_source=new_issue.administrative_source,
            expected_authority_head=new_issue.administrative_source.current_authority_head,
        )

    predecessor_revocation = _proposed_revocation(
        ScheduledMandateRevocationV1(
            mandate_id=old.mandate.scope.mandate_id,
            mandate_head=old.mandate_head,
            predecessor=reference("prior/revocation", b"prior"),
            revocation_observation=call_head("revocation-observation"),
        )
    )
    with pytest.raises(ValueError, match="close the exact old mandate"):
        SupersedeScheduledMandateV1(
            command_id="supersede-command",
            old_selected_mandate=old,
            old_selected_budget=selected_budget(old),
            old_expected_revocation_absent=Absent(),
            terminal_revocation=predecessor_revocation,
            new_issue=new_issue,
            administrative_source=new_issue.administrative_source,
            expected_authority_head=new_issue.administrative_source.current_authority_head,
        )

    changed_head_revocation = _proposed_revocation(
        ScheduledMandateRevocationV1(
            mandate_id=old.mandate.scope.mandate_id,
            mandate_head=reference("other", b"other"),
            predecessor=Absent(),
            revocation_observation=call_head("revocation-observation"),
        )
    )
    with pytest.raises(ValueError, match="retain old lineage"):
        PreparedMandateSupersessionV1(
            source_request_fingerprint=DIGEST,
            old_selected_mandate=old,
            old_selected_budget=selected_budget(old),
            terminal_revocation=changed_head_revocation,
            new_issuance=prepared_issue(new),
        )

    beneficiary_payload = json.loads(request.canonical_bytes())
    beneficiary_payload["new_issue"]["proposed_mandate"]["mandate"]["scope"][
        "beneficiary_principal"
    ] = "other"
    beneficiary_payload["new_issue"]["administrative_source"]["semantic_scope"] = (
        beneficiary_payload["new_issue"]["proposed_mandate"]["mandate"]["scope"]
    )
    beneficiary_payload["administrative_source"]["semantic_scope"] = beneficiary_payload[
        "new_issue"
    ]["proposed_mandate"]["mandate"]["scope"]
    _refresh_proposed_mandate(beneficiary_payload["new_issue"]["proposed_mandate"])
    with pytest.raises(ValidationError, match="successor bound"):
        SupersedeScheduledMandateV1.model_validate_json(_json_payload(beneficiary_payload))


def test_admission_expiry_is_pure_and_exclusive_at_the_deadline() -> None:
    selected = selected_mandate()
    assert mandate_admission_is_fresh(selected, Absent(), 99)
    assert not mandate_admission_is_fresh(selected, Absent(), 100)
    assert not mandate_admission_is_fresh(selected, Absent(), 101)


def test_closed_request_union_round_trips_and_capability_leaf_has_no_composition_import() -> None:
    request = issue()
    assert (
        TypeAdapter(ScheduledMandateLifecycleRequestV1).validate_json(request.canonical_bytes())
        == request
    )
    source = Path(
        "src/chiplog/capabilities/agent_loop/scheduler_mandate_lifecycle_contracts.py"
    ).read_text()
    assert "chiplog.composition" not in source
