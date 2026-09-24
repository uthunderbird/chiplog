"""Closure tests for the H1 historical selected-source boundary."""

from __future__ import annotations

import json
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

import chiplog.composition.h1_historical_selected_sources as historical_sources
from chiplog.composition.h1_historical_selected_sources import (
    _open_historical_ports,
    _require_historical_ports,
    _verify_historical_scope,
    bind_selected_h1_completion,
    verify_h1_historical_sources,
)


class _NoHistoricalPorts:
    """Looks deliberately unlike an executable/current H1 runtime."""

    class _Gate:
        def hold(self) -> object:
            raise AssertionError("historical verifier must not enter a partial gate")

    def _authority_gate(self) -> _Gate:
        return self._Gate()

    def _require_dispatch_resources(self) -> object:  # pragma: no cover - must not be called
        raise AssertionError("historical verifier used current dispatch resources")

    def read_admitted_inbox(self, _: str) -> object:  # pragma: no cover - must not be called
        raise AssertionError("historical verifier used the ingress history facade")

    def _find(self, *_: object) -> object:  # pragma: no cover - must not be called
        raise AssertionError("historical verifier used the current H1 reader")


def test_verifier_rejects_untyped_batch_before_any_runtime_access() -> None:
    runtime = _NoHistoricalPorts()

    with pytest.raises(TypeError, match="CompleteDeliveryBatchV2"):
        verify_h1_historical_sources(object(), object(), runtime)  # type: ignore[arg-type]


def test_binder_rejects_untyped_batch_before_any_runtime_access() -> None:
    runtime = _NoHistoricalPorts()

    with pytest.raises(TypeError, match="CompleteDeliveryBatchV2"):
        bind_selected_h1_completion(object(), runtime)  # type: ignore[arg-type]


def test_historical_ports_are_not_silently_replaced_by_current_h1_services() -> None:
    """A runtime missing the registered raw seam fails before any current fallback."""
    runtime = _NoHistoricalPorts()

    # Deliberately bypass the typed wire boundary: this exercises only the
    # fail-closed runtime capability check and proves no current fallback runs.
    with pytest.raises(ValueError, match="historical custody"):
        _require_historical_ports(runtime)


class _NullCustodyPorts:
    class _Gate:
        def hold(self) -> object:
            raise AssertionError("test opens raw ports directly")

    def _authority_gate(self) -> _Gate:
        return self._Gate()

    def _h1_historical_custody(self) -> None:
        return None

    def _loop_decisions(self) -> object:
        return type("Loop", (), {"entries": lambda self: ()})()

    def _owner_decisions(self) -> object:
        return type("Owners", (), {"snapshot": lambda self: object()})()

    def _h1_historical_trust_reader(self) -> object:
        return object()


def test_raw_port_open_rejects_missing_custody_without_current_fallback() -> None:
    """The gate-held opener refuses a null custody binding rather than substituting resources."""
    ports = _require_historical_ports(_NullCustodyPorts())

    with pytest.raises(ValueError, match="unsupported raw selected-source reader"):
        _open_historical_ports(ports)


class _MissingTrustPorts(_NullCustodyPorts):
    def _h1_historical_custody(self) -> object:
        return object()


def test_raw_port_open_requires_historical_trust_methods() -> None:
    """An opaque trust object cannot be treated as a historical raw reader."""
    ports = _require_historical_ports(_MissingTrustPorts())

    with pytest.raises(ValueError, match="unsupported raw selected-source reader"):
        _open_historical_ports(ports)


def test_historical_scope_rejects_a_trust_reader_on_a_different_gate() -> None:
    """Raw trust facts from another authority cut cannot be joined to H1 evidence."""
    reader = SimpleNamespace(authority_gate=object())

    with pytest.raises(ValueError, match="different authority gate"):
        _verify_historical_scope(
            SimpleNamespace(),  # type: ignore[arg-type]
            trust_reader=reader,
            gate=object(),
        )


@dataclass(frozen=True)
class _Scope:
    database_id: str
    scope_id: str
    revision: int
    predecessor: object | None = None


class _ScopeCodec:
    @staticmethod
    def model_validate(value: dict[str, object]) -> _Scope:
        return _Scope(**value)


def _scope_entry(decision_id: str, scope: _Scope) -> tuple[str, None, bytes]:
    return (
        decision_id,
        None,
        json.dumps(
            {
                "kind": "HERMETIC_OUTPUT_SCOPE_V1",
                "payload": {"scope": scope.__dict__},
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode(),
    )


def test_scope_lineage_rejects_a_later_self_consistent_scope_substitution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A newer same-lineage scope cannot replace the historical selected row."""
    monkeypatch.setattr(historical_sources, "HermeticOutputScopeV1", _ScopeCodec)
    selected = _Scope("db", "scope", 0)
    substituted = _Scope("db", "scope", 1)
    prefix = SimpleNamespace(
        physical_entries=(_scope_entry("selected", selected), _scope_entry("later", substituted)),
        observation=SimpleNamespace(physical_journal_head=SimpleNamespace(head="before")),
    )

    with pytest.raises(ValueError, match="supersedes"):
        historical_sources._verify_historical_scope_lineage(
            scope=selected,  # type: ignore[arg-type]
            selected_decision_id="selected",
            selected_predecessor="before",
            issue_prefix=prefix,
            current_prefix=prefix,
        )


def test_scope_lineage_rejects_new_scope_with_substituted_physical_predecessor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A recomputed scope body cannot detach a new decision from the issue cut."""
    monkeypatch.setattr(historical_sources, "HermeticOutputScopeV1", _ScopeCodec)
    selected = _Scope("db", "scope", 0)
    current = SimpleNamespace(
        physical_entries=(_scope_entry("selected", selected),),
        observation=SimpleNamespace(physical_journal_head=SimpleNamespace(head="issue-head")),
    )
    issue = SimpleNamespace(
        physical_entries=(),
        observation=SimpleNamespace(physical_journal_head=SimpleNamespace(head="issue-head")),
    )

    with pytest.raises(ValueError, match="wrong physical predecessor"):
        historical_sources._verify_historical_scope_lineage(
            scope=selected,  # type: ignore[arg-type]
            selected_decision_id="selected",
            selected_predecessor="substituted-head",
            issue_prefix=issue,
            current_prefix=current,
        )


def test_historical_scope_rejects_a_recomputed_owner_response_on_a_different_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A self-consistent owner response cannot be moved to a different broker route."""
    route = SimpleNamespace(request_id="issue")
    substituted_route = SimpleNamespace(request_id="issue", replacement=True)
    issue_wire = SimpleNamespace(
        canonical_bytes=lambda: b"issue-wire",
        mode="ISSUE_HERMETIC_OUTPUT_SCOPE_V1",
        request_bytes=b"issue-call",
    )
    current_wire = SimpleNamespace(
        canonical_bytes=lambda: b"current-wire",
        mode="READ_CURRENT_HERMETIC_OUTPUT_SCOPE_V1",
        request_bytes=b"current-call",
    )
    issue_call = SimpleNamespace(
        evidence=SimpleNamespace(route=route, selected_request_bytes=b"issue-intent")
    )
    current_call = SimpleNamespace(
        route=SimpleNamespace(request_id="current"), read_request_bytes=b"current-intent"
    )
    prefix = SimpleNamespace(snapshot_bytes=b"snapshot")
    reader = SimpleNamespace(
        authority_gate=None,
        historical_prefix=lambda _: prefix,
    )

    def exchange(payload: bytes, request_id: str) -> SimpleNamespace:
        return SimpleNamespace(
            sent=SimpleNamespace(
                canonical_payload=payload,
                request_id=request_id,
                caller=SimpleNamespace(owner_id="broker"),
                callee=SimpleNamespace(owner_id="deployment_trust"),
                held_resources=(),
                budget=SimpleNamespace(
                    remaining_calls=1,
                    remaining_depth=1,
                    policy_version=1,
                    absolute_deadline_ns=2,
                ),
            ),
            returned=SimpleNamespace(
                request_id=request_id,
                responder=SimpleNamespace(owner_id="deployment_trust"),
                canonical_payload=b"owner-response",
            ),
            sent_at_ns=1,
            returned_at_ns=1,
        )

    issuance = SimpleNamespace(
        scope_issue_exchange=exchange(b"issue-wire", "issue"),
        scope_current_exchange=exchange(b"current-wire", "current"),
    )
    monkeypatch.setattr(
        historical_sources.TrustOwnerCall,
        "model_validate_json",
        lambda raw: issue_wire if raw == b"issue-wire" else current_wire,
    )
    monkeypatch.setattr(
        historical_sources.H1OwnerCandidateCallV1,
        "model_validate_json",
        lambda _: issue_call,
    )
    monkeypatch.setattr(
        historical_sources.H1OwnerCurrentCallV1,
        "model_validate_json",
        lambda _: current_call,
    )
    monkeypatch.setattr(
        historical_sources.H1OwnerCandidateV1,
        "model_validate_json",
        lambda _: SimpleNamespace(route=substituted_route),
    )
    monkeypatch.setattr(
        historical_sources.H1OwnerCurrentCandidateV1,
        "model_validate_json",
        lambda _: SimpleNamespace(route=current_call.route),
    )
    monkeypatch.setattr(historical_sources, "PublicPortSuccess", object)
    monkeypatch.setattr(
        historical_sources.IssueHermeticOutputScopeV1,
        "model_validate_json",
        lambda _: SimpleNamespace(expected_trust_observation=object()),
    )
    monkeypatch.setattr(
        historical_sources.ReadCurrentHermeticExecutionScopeV1,
        "model_validate_json",
        lambda _: SimpleNamespace(expected_trust_observation=object()),
    )

    with pytest.raises(ValueError, match="prefix differs"):
        _verify_historical_scope(  # type: ignore[arg-type]
            issuance,
            trust_reader=reader,
            gate=None,
        )
