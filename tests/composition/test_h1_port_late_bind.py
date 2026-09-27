"""Issuer-held late workspace binding on the installed H1 port."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from types import SimpleNamespace
from typing import Any, cast

import pytest

from chiplog.capabilities.agent_loop.delivery_contracts import ExactHead, ProviderRecipient
from chiplog.capabilities.deployment_trust import TrustReference
from chiplog.capabilities.deployment_trust.hermetic_output_scope_contracts import (
    HermeticOutputPolicyV1,
    HermeticOutputScopeAnchorV1,
    HermeticOutputSourceV1,
    SelectedHermeticResourceObservationRefV1,
)
from chiplog.capabilities.evidence_journal.commands import Heads, TrustedIngress
from chiplog.composition.h1_preissuance_registration import (
    H1PreissuanceSelection,
    H1PreissuanceSourceViolation,
    _issue_selection_for_authenticated_port,
    _selection_state,
)
from chiplog.composition.h1_runtime_preissuance_port import (
    _H1RuntimePreissuancePort,
    _PreparedCut,
    _SelectionCut,
)
from chiplog.composition.h1_workspace_policy_v2 import H1WorkspacePolicyV2
from chiplog.domain_primitives import PrincipalId, TenantId


class _Gate:
    def hold(self) -> _Gate:
        return self

    def __enter__(self) -> None: ...

    def __exit__(self, *args: object) -> None: ...


def _head(identity: str) -> ExactHead:
    return ExactHead(identity=identity, head=identity + "/head", fingerprint="a" * 64)


def _bound_port(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[_H1RuntimePreissuancePort, H1PreissuanceSelection, TrustedIngress]:
    port = object.__new__(_H1RuntimePreissuancePort)
    selected = SelectedHermeticResourceObservationRefV1(
        signature_domain="dispatch-resources.v1",
        selected_initialization=_head("initialization"),
        signed_observation_fingerprint="b" * 64,
    )
    policy = HermeticOutputPolicyV1(
        endpoint_ref=_head("endpoint"),
        selected_resource_observation_ref=selected,
        selection="ORIGIN_EXACT",
        ingress_class="AUTHENTICATED_R17_CLI",
        payload_class="NonAuthoritativeText",
        purpose="H1_LOCAL_COMMENTARY",
        external_delivery=False,
        attempt_ordinal=0,
        call_count=0,
    )
    policy_bytes = policy.canonical_bytes()
    policy_ref = ExactHead(
        identity="h1-disclosure-policy",
        head="h1-disclosure-policy/" + hashlib.sha256(policy_bytes).hexdigest(),
        fingerprint=hashlib.sha256(policy_bytes).hexdigest(),
    )
    anchor = HermeticOutputScopeAnchorV1(
        owner_id="deployment_trust",
        decision=_head("decision"),
        record_ordinal=0,
        record_type_id="chiplog.deployment_trust.hermetic_output_scope",
        schema_id="chiplog.deployment_trust.record.v1",
        record=_head("record"),
        scope_revision=0,
        predecessor=None,
        selected_resource_observation_ref=selected,
    )
    scope_ref = _head("scope")
    scope = SimpleNamespace(
        database_id="database",
        tenant_id="hermetic-tenant",
        principal_id="hermetic-principal",
        authenticated_cli_state=SimpleNamespace(
            credential_head="credential", session_head="session"
        ),
        contour_head="contour",
        recipient=ProviderRecipient(
            provider_id="hermetic-effects",
            account_id="hermetic-account",
            recipient_id="hermetic-principal",
            endpoint=_head("endpoint"),
            canonical_address=b"hermetic://effects/hermetic-principal",
            credential_binding=_head("credential-binding"),
        ),
        disclosure_policy=HermeticOutputSourceV1(
            field_path="disclosure_policy", ref=policy_ref, canonical_source_bytes=policy_bytes
        ),
        selected_resource_observation_ref=selected,
        admitted_authentication=_head("authentication"),
    )
    prepared = _PreparedCut(
        run=cast(
            Any,
            SimpleNamespace(
                run_id="run",
                head="run-head",
                tenant="hermetic-tenant",
                principal="hermetic-principal",
            ),
        ),
        source=cast(
            Any,
            SimpleNamespace(
                verified=SimpleNamespace(
                    authenticated_cli_ref=TrustReference(
                        tenant_id=TenantId("hermetic-tenant"),
                        principal_id=PrincipalId("hermetic-principal"),
                        contour="contour",
                        credential_head="credential",
                        session_head="session",
                        source_head="local",
                        trust_head="trust",
                        materialization_head="materialization",
                        freshness_sequence=1,
                        peer_credential="uid:1",
                    )
                )
            ),
        ),
        custody_digest="custody",
        custody_generation=0,
        trust=cast(Any, SimpleNamespace()),
    )
    issued = SimpleNamespace(anchor=anchor, scope_head=scope_ref)
    selection = _issue_selection_for_authenticated_port(issuer=port)
    port._gate = cast(Any, _Gate())
    port._launch = cast(
        Any,
        SimpleNamespace(_slot=SimpleNamespace(database_id="database", tenant_id="hermetic-tenant")),
    )
    entry = SimpleNamespace(
        channel_id="hermetic-local",
        visible_channels=("hermetic-local",),
        origin_recipient_id="hermetic-principal",
        registration_id="registration",
        generation=7,
        conversation_id="conversation",
        accepted_policy_selector="H1_OWNER_ISSUED_ORIGIN_EXACT_V1",
        canonical_bytes=lambda: b"entry",
    )
    port._custody = cast(
        Any,
        SimpleNamespace(
            _raw=b"custody",
            _registry=SimpleNamespace(
                deployment_id="deployment",
                database_id="database",
                database_genesis_digest="c" * 64,
            ),
            select=lambda tenant, principal, channel: (
                entry
                if (tenant, principal, channel)
                == ("hermetic-tenant", "hermetic-principal", "hermetic-local")
                else (_ for _ in ()).throw(ValueError("foreign registration"))
            ),
        ),
    )
    port._cuts = {
        id(selection): (
            selection,
            _SelectionCut(
                prepared=prepared,
                issued=cast(Any, issued),
                current=cast(
                    Any,
                    SimpleNamespace(
                        scope_ref=scope_ref,
                        source_anchor=anchor,
                        ordered_current_source_refs=(
                            scope.admitted_authentication,
                            scope.recipient.endpoint,
                            scope.recipient.credential_binding,
                        ),
                    ),
                ),
                scope=cast(Any, scope),
            ),
        )
    }
    monkeypatch.setattr(_H1RuntimePreissuancePort, "_assert_launch_and_trust", lambda self: None)
    monkeypatch.setattr(_H1RuntimePreissuancePort, "_selected_cut", lambda self, locator: prepared)
    monkeypatch.setattr(_H1RuntimePreissuancePort, "_reopen_scope", lambda *_: scope)
    identity = TrustedIngress(
        tenant="hermetic-tenant",
        principal="hermetic-principal",
        heads=Heads(
            journal=1,
            policy="policy",
            credential="credential",
            session="session",
            contour="contour",
            deletion="deletion",
        ),
        items=(),
        sources=(),
        endpoint="endpoint",
    )
    return port, selection, identity


def test_late_bind_retains_exact_issuer_derived_v2_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    port, selection, identity = _bound_port(monkeypatch)

    port.bind_workspace_cut(selection, identity, "hermetic-local", "database")
    resolved = cast(H1WorkspacePolicyV2, port.resolved_workspace_policy(selection))

    assert resolved.registration.registration_id == "registration"
    assert resolved.registration.generation == 7
    assert resolved.registration.origin_recipient_id == "hermetic-principal"
    assert resolved.registration.output_scope_ref == _head("scope")
    assert resolved.heads.session == "session"


def test_selected_workspace_authentication_heads_reopen_the_retained_current_cut(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    port, selection, _ = _bound_port(monkeypatch)

    assert port.selected_workspace_authentication_heads(selection) == ("credential", "session")
    cut = port._cuts[id(selection)][1]
    port._cuts[id(selection)] = (
        selection,
        _SelectionCut(
            prepared=cut.prepared,
            issued=cut.issued,
            current=cast(
                Any,
                SimpleNamespace(
                    scope_ref=cut.current.scope_ref,
                    source_anchor=cut.current.source_anchor,
                    ordered_current_source_refs=(),
                ),
            ),
            scope=cut.scope,
        ),
    )
    with pytest.raises(H1PreissuanceSourceViolation, match="stale"):
        port.selected_workspace_authentication_heads(selection)
    with pytest.raises(H1PreissuanceSourceViolation, match="stale"):
        port.selected_workspace_identity(selection)


def test_selected_workspace_identity_is_the_retained_selected_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    port, selection, _ = _bound_port(monkeypatch)

    profile = port.selected_workspace_identity(selection)

    assert (
        profile.tenant,
        profile.principal,
        profile.credential_head,
        profile.session_head,
        profile.contour_head,
        profile.database_id,
        profile.channel,
    ) == (
        "hermetic-tenant",
        "hermetic-principal",
        "credential",
        "session",
        "contour",
        "database",
        "hermetic-local",
    )
    cut = port._cuts[id(selection)][1]
    authenticated = cut.prepared.source.verified.authenticated_cli_ref
    source = cast(Any, cut.prepared.source)
    source.verified.authenticated_cli_ref = replace(authenticated, contour="other")
    with pytest.raises(H1PreissuanceSourceViolation, match="identity differs"):
        port.selected_workspace_identity(selection)


def test_identical_late_bind_is_idempotent_but_conflict_refuses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    port, selection, identity = _bound_port(monkeypatch)

    port.bind_workspace_cut(selection, identity, "hermetic-local", "database")
    retained = cast(H1WorkspacePolicyV2, port.resolved_workspace_policy(selection))
    port.bind_workspace_cut(selection, identity, "hermetic-local", "database")
    assert cast(H1WorkspacePolicyV2, port.resolved_workspace_policy(selection)) is retained
    with pytest.raises(H1PreissuanceSourceViolation, match="conflicts"):
        port.bind_workspace_cut(
            selection,
            identity.model_copy(update={"endpoint": "other"}),
            "hermetic-local",
            "database",
        )


def test_late_bind_refuses_foreign_or_consumed_selection(monkeypatch: pytest.MonkeyPatch) -> None:
    port, selection, identity = _bound_port(monkeypatch)
    foreign = _issue_selection_for_authenticated_port(issuer=object())

    with pytest.raises(H1PreissuanceSourceViolation, match="foreign"):
        port.bind_workspace_cut(foreign, identity, "hermetic-local", "database")
    with pytest.raises(H1PreissuanceSourceViolation, match="foreign"):
        port.selected_workspace_authentication_heads(foreign)
    with pytest.raises(H1PreissuanceSourceViolation, match="foreign"):
        port.selected_workspace_identity(foreign)
    state = _selection_state(selection, port)
    assert state is not None
    state.consumed = True
    with pytest.raises(H1PreissuanceSourceViolation, match="consumed"):
        port.bind_workspace_cut(selection, identity, "hermetic-local", "database")
    with pytest.raises(H1PreissuanceSourceViolation, match="consumed"):
        port.selected_workspace_authentication_heads(selection)
    with pytest.raises(H1PreissuanceSourceViolation, match="consumed"):
        port.selected_workspace_identity(selection)
