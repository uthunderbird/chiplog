"""Fail-closed diagnostics for the pre-workspace H1 source seam."""

from __future__ import annotations

import asyncio
import copy
import pickle

import pytest

from chiplog.composition.h1_preissuance_registration import (
    H1PreissuanceRegistrationSource,
    H1PreissuanceSelection,
    H1PreissuanceSourceUnavailable,
    H1PreissuanceSourceViolation,
    H1VerifiedOriginalWorkspaceIssuance,
)


def test_diagnostic_positive_refuses_without_canonical_custody_and_runtime_mount() -> None:
    source = H1PreissuanceRegistrationSource(object())

    with pytest.raises(
        H1PreissuanceSourceUnavailable, match="canonical runtime custody/source mount"
    ):
        asyncio.run(source.prepare_preissuance("selected-native-run"))


def test_dto_only_original_issuance_cannot_reopen_a_preissuance_selection() -> None:
    source = H1PreissuanceRegistrationSource(object())

    with pytest.raises(H1PreissuanceSourceViolation, match="issuer-verified original issuance"):
        source.reopen_original(
            {
                "schema_id": "chiplog.execution.h1-original-workspace-issuance.v2",
                "registration": "self-consistent but caller supplied",
            }
        )


@pytest.mark.parametrize(
    "selected_run_identity",
    [
        pytest.param("run-with-r16-mutated-during-owner-ipc", id="r16-r17-source-mutation"),
        pytest.param("run-for-foreign-recipient", id="foreign-recipient"),
        pytest.param("run-with-revoked-custody", id="revoked-custody"),
        pytest.param("run-with-wrong-owner-policy-head", id="wrong-policy-head"),
    ],
)
def test_unmounted_source_refuses_each_preissuance_authority_claim(
    selected_run_identity: str,
) -> None:
    """No fixture-shaped mutation or policy claim opens a partial positive path."""

    source = H1PreissuanceRegistrationSource(object())
    with pytest.raises(H1PreissuanceSourceUnavailable):
        asyncio.run(source.prepare_preissuance(selected_run_identity))


def test_selection_is_not_constructible_copyable_or_serializable() -> None:
    with pytest.raises(TypeError, match="issuer-held"):
        H1PreissuanceSelection()

    forged = object.__new__(H1PreissuanceSelection)
    with pytest.raises(TypeError, match="cannot be copied"):
        copy.copy(forged)
    with pytest.raises(TypeError, match="cannot be serialized"):
        pickle.dumps(forged)


def test_unissued_or_foreign_selection_never_becomes_current_or_delivery_authority() -> None:
    source = H1PreissuanceRegistrationSource(object())
    forged = object.__new__(H1PreissuanceSelection)

    assert source.check_current(forged) is False
    with pytest.raises(H1PreissuanceSourceViolation, match="issuer-held selection"):
        source.build_delivery_observation(forged, object())


def test_forged_port_response_cannot_turn_an_unpersisted_owner_reply_into_a_selection() -> None:
    class ForgedPort:
        async def prepare_preissuance(self, selected_run_identity: object) -> object:
            del selected_run_identity
            # Represents a correlated but unpersisted owner reply.  It has no
            # trusted journal/materialization reopen behind an issued capability.
            return object.__new__(H1PreissuanceSelection)

        def reopen_original(self, original: object) -> object:
            del original
            raise AssertionError("not reached")

        def check_current(self, selection: object) -> bool:
            del selection
            return False

        def resolved_workspace_policy(self, selection: object) -> object:
            del selection
            raise AssertionError("not reached")

        def validate_original_selection(self, selection: object, original: object) -> bool:
            del selection, original
            return False

        def build_delivery_observation(self, selection: object, inputs: object) -> object:
            del selection, inputs
            raise AssertionError("not reached")

    class Runtime:
        _h1_preissuance_registration_source_port = ForgedPort()

    source = H1PreissuanceRegistrationSource(Runtime())
    with pytest.raises(H1PreissuanceSourceViolation, match="unissued selection"):
        asyncio.run(source.prepare_preissuance("selected-native-run"))


def test_forged_verified_issuance_is_not_constructible_or_accepted() -> None:
    source = H1PreissuanceRegistrationSource(object())
    with pytest.raises(TypeError, match="issuer-held"):
        H1VerifiedOriginalWorkspaceIssuance()

    forged = object.__new__(H1VerifiedOriginalWorkspaceIssuance)
    with pytest.raises(H1PreissuanceSourceViolation, match="issuer-verified original issuance"):
        source.reopen_original(forged)
