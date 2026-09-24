"""The H1 policy seam rejects authority-shaped caller inputs until mounted."""

from __future__ import annotations

import copy
import pickle

import pytest

from chiplog.composition.h1_conversation_policy import (
    H1ConversationPolicyCapture,
    H1ConversationPolicyUnavailable,
    H1ConversationPolicyViolation,
    capture_policy_current,
    check_policy_current,
    derive_entry,
)


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
