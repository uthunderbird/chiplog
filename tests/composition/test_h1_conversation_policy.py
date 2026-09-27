"""The H1 policy seam rejects authority-shaped caller inputs until mounted."""

from __future__ import annotations

import copy
import hashlib
import pickle

import pytest

from chiplog.capabilities.agent_loop.delivery_contracts import AcceptedDelivery, ExactHead
from chiplog.composition.h1_conversation_policy import (
    H1ConversationPolicyCapture,
    H1ConversationPolicyUnavailable,
    H1ConversationPolicyViolation,
    H1RegisteredConversationPolicy,
    _CaptureMaterial,
    _issue_for_authenticated_port,
    capture_policy_current,
    check_policy_current,
    derive_entry,
)
from tests.support.execution_versions import execution_run_v3


def test_capture_refuses_when_no_authenticated_runtime_policy_port_is_mounted() -> None:
    with pytest.raises(
        H1ConversationPolicyUnavailable, match="registered authenticated policy source port"
    ):
        capture_policy_current(object())


def test_capture_refuses_a_port_that_returns_a_dto_shaped_or_forged_capability() -> None:
    class ForgedPort:
        def capture_current(self) -> object:
            return object.__new__(H1ConversationPolicyCapture)

    class Runtime:
        _h1_conversation_policy_source_port = ForgedPort()

    with pytest.raises(H1ConversationPolicyViolation, match="unissued capture"):
        capture_policy_current(Runtime())


def test_unissued_capture_cannot_be_current_or_drive_the_pure_projection() -> None:
    forged = object.__new__(H1ConversationPolicyCapture)

    assert check_policy_current(object(), forged) is False
    with pytest.raises(H1ConversationPolicyViolation, match="unissued"):
        derive_entry(forged)


def test_capture_is_not_constructible_copyable_or_serializable() -> None:
    with pytest.raises(TypeError, match="issuer-held"):
        H1ConversationPolicyCapture()

    forged = object.__new__(H1ConversationPolicyCapture)
    with pytest.raises(TypeError, match="cannot be copied"):
        copy.copy(forged)
    with pytest.raises(TypeError, match="cannot be serialized"):
        pickle.dumps(forged)


def test_check_current_refuses_non_capture_input_with_a_lookalike_port() -> None:
    class LookalikePort:
        def check_current(self, capture: H1ConversationPolicyCapture) -> bool:
            del capture
            return True

    class Runtime:
        _h1_conversation_policy_source_port = LookalikePort()

    assert check_policy_current(Runtime(), object()) is False


def _policy_capture(*, selected_run_contour_head: str):
    run = execution_run_v3()
    policy = ExactHead(identity="policy", head="head:policy", fingerprint="a" * 64)
    registration = H1RegisteredConversationPolicy(
        tenant_id=run.tenant,
        principal_id=run.principal,
        origin_recipient_id=run.origin.recipient.recipient_id,
        accepted_policy=policy,
        conversation_id="conversation",
        origin_channel_id="channel",
        visible_channels=("channel",),
        workspace_policy_head="workspace-policy",
        contour_head="raw-workspace-contour",
        deletion_fence_head="deletion",
        disclosure_endpoint="channel",
    )
    delivery = AcceptedDelivery(
        delivery_id="delivery",
        acceptance=policy,
        selection=run.origin,
        rendered_bytes=b"accepted",
        render_digest=hashlib.sha256(b"accepted").hexdigest(),
        manifest_digest="c" * 64,
        visibility=(policy,),
        provenance=(policy,),
        disclosure=(policy,),
        narrowing=(),
        policy=policy,
    )
    return _issue_for_authenticated_port(
        _CaptureMaterial(
            registration=registration,
            run=run,
            selected_run_contour_head=selected_run_contour_head,
            accepted_delivery=delivery,
            authenticated_history=(),
            narrowing=run.turns[0].attempts[0].manifest.members[0].label,
        ),
        issuer=object(),
    )


def test_projection_keeps_raw_workspace_contour_but_checks_derived_run_contour_witness() -> None:
    entry = derive_entry(_policy_capture(selected_run_contour_head="contour"))

    assert entry.envelope.contour_head == "raw-workspace-contour"

    with pytest.raises(H1ConversationPolicyViolation, match="selected admission"):
        derive_entry(_policy_capture(selected_run_contour_head="foreign-derived-contour"))
