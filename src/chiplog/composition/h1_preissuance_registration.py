"""Fail-closed source boundary for H1 pre-workspace registration.

The resolved V2 registration wire is deliberately inert.  This module holds
the corresponding live capability behind a runtime-private port.  Until the
canonical launcher mounts that port with the P0 custody reader, selected
R17/R16/native-Run capture, and the deployment-trust journal/materialization
reader, every operation refuses.  In particular, an owner IPC response is not
enough: the port must reopen its exact persisted scope anchor before it issues
one of the opaque values below.

No public DTO is accepted as authority.  The private port owns the gate-held
initial capture, releases the gate for owner IPC, reopens the persisted scope,
then performs the final source recheck under the same gate.  It must repeat
that complete fenced read while constructing a delivery observation; a prior
``check_current`` is only an advisory fast-path and cannot authorize a later
observation.
"""

from __future__ import annotations

import inspect
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol, cast

from chiplog.capabilities.agent_loop.delivery_preparation import DeliveryObservation


class H1PreissuanceSourceUnavailable(ValueError):
    """The deployment has no authenticated preissuance source mount."""


class H1PreissuanceSourceViolation(ValueError):
    """A caller attempted to use an inert value as preissuance authority."""


class H1PreissuanceSelection:
    """One issuer-held, one-use selection; it is never a durable wire value."""

    __slots__ = ()

    def __init__(self) -> None:
        raise TypeError("H1 preissuance selections are issuer-held capabilities")

    def __copy__(self) -> H1PreissuanceSelection:
        raise TypeError("H1 preissuance selection cannot be copied")

    def __deepcopy__(self, memo: dict[int, object]) -> H1PreissuanceSelection:
        del memo
        raise TypeError("H1 preissuance selection cannot be copied")

    def __reduce__(self) -> str | tuple[object, ...]:
        raise TypeError("H1 preissuance selection cannot be serialized")


class H1VerifiedOriginalWorkspaceIssuance:
    """Opaque result of a future issuer-owned V2 issuance reopen.

    Decoded V2 bytes, its public reference, and value equality are insufficient:
    the future reader must authenticate the physical original issuance before it
    can mint this input for ``reopen_original``.
    """

    __slots__ = ()

    def __init__(self) -> None:
        raise TypeError("H1 original issuances are issuer-held capabilities")

    def __copy__(self) -> H1VerifiedOriginalWorkspaceIssuance:
        raise TypeError("H1 original issuance cannot be copied")

    def __deepcopy__(self, memo: dict[int, object]) -> H1VerifiedOriginalWorkspaceIssuance:
        del memo
        raise TypeError("H1 original issuance cannot be copied")

    def __reduce__(self) -> str | tuple[object, ...]:
        raise TypeError("H1 original issuance cannot be serialized")


class H1AuthenticatedCompletionInputs:
    """Opaque, issuer-owned selectors for the final delivery-time fence."""

    __slots__ = ()

    def __init__(self) -> None:
        raise TypeError("H1 completion inputs are issuer-held capabilities")

    def __copy__(self) -> H1AuthenticatedCompletionInputs:
        raise TypeError("H1 completion inputs cannot be copied")

    def __deepcopy__(self, memo: dict[int, object]) -> H1AuthenticatedCompletionInputs:
        del memo
        raise TypeError("H1 completion inputs cannot be copied")

    def __reduce__(self) -> str | tuple[object, ...]:
        raise TypeError("H1 completion inputs cannot be serialized")


class H1PreissuanceRegistrationPort(Protocol):
    """Runtime-private complete source reader.

    ``prepare_preissuance`` must capture active P0 custody, selected R17,
    current R16, trusted ingress/database/native Run and trust observation
    under AuthorityGate; it must release that gate for the real owner IPC; and
    it must reopen the exact persisted journal/materialization anchor and
    recheck the entire cut under the gate before issuing its selection.

    ``reopen_original`` must reauthenticate the retained V2 physical issuance
    and repeat the exact persisted-scope and source checks.  ``build`` must do
    the same final fenced read itself, including recipient equality and exact
    owner-policy bytes/head, so callers cannot race a successful check.
    """

    async def prepare_preissuance(
        self, selected_run_identity: object, actor_observation: object | None = None
    ) -> H1PreissuanceSelection: ...

    def validate_actor_scope_refresh(
        self, selection: H1PreissuanceSelection, old_observation: object, new_observation: object
    ) -> bool: ...

    def reopen_original(
        self, verified_v2_issuance: H1VerifiedOriginalWorkspaceIssuance
    ) -> H1PreissuanceSelection: ...

    def check_current(self, selection: H1PreissuanceSelection) -> bool: ...

    def resolved_workspace_policy(self, selection: H1PreissuanceSelection) -> object: ...

    def validate_original_selection(
        self,
        selection: H1PreissuanceSelection,
        verified_v2_issuance: H1VerifiedOriginalWorkspaceIssuance,
    ) -> bool: ...

    def build_delivery_observation(
        self,
        selection: H1PreissuanceSelection,
        authenticated_completion_inputs: H1AuthenticatedCompletionInputs,
    ) -> DeliveryObservation: ...


@dataclass(slots=True)
class _IssuedSelection:
    selection: H1PreissuanceSelection
    issuer: object
    consumed: bool = False


_issued_selections: dict[int, _IssuedSelection] = {}
_issued_originals: dict[int, tuple[H1VerifiedOriginalWorkspaceIssuance, object]] = {}
_issued_completion_inputs: dict[int, tuple[H1AuthenticatedCompletionInputs, object]] = {}


def _issue_selection_for_authenticated_port(*, issuer: object) -> H1PreissuanceSelection:
    """Private construction hook for a mounted port after persisted reopen.

    The port must not call this for an IPC DTO.  It is permitted only after the
    anchor's exact decision and materialized record bytes, policy bytes/head,
    custody generation, R17/R16/native Run and worker cut have all been
    revalidated under the authority gate.
    """

    selection = object.__new__(H1PreissuanceSelection)
    _issued_selections[id(selection)] = _IssuedSelection(selection, issuer)
    return selection


def _issue_verified_original_for_authenticated_port(
    *, issuer: object
) -> H1VerifiedOriginalWorkspaceIssuance:
    """Private hook after authenticating one physical original V2 issuance."""

    original = object.__new__(H1VerifiedOriginalWorkspaceIssuance)
    _issued_originals[id(original)] = (original, issuer)
    return original


def _issue_completion_inputs_for_authenticated_port(
    *, issuer: object
) -> H1AuthenticatedCompletionInputs:
    """Private hook for selectors independently authenticated by the owner."""

    inputs = object.__new__(H1AuthenticatedCompletionInputs)
    _issued_completion_inputs[id(inputs)] = (inputs, issuer)
    return inputs


def _selection_state(value: object, issuer: object) -> _IssuedSelection | None:
    if not isinstance(value, H1PreissuanceSelection):
        return None
    state = _issued_selections.get(id(value))
    if state is None or state.selection is not value or state.issuer is not issuer:
        return None
    return state


def _issued_by(value: object, issued: Mapping[int, tuple[object, object]], issuer: object) -> bool:
    stored = issued.get(id(value))
    return stored is not None and stored[0] is value and stored[1] is issuer


def _issued_any(value: object, issued: Mapping[int, tuple[object, object]]) -> bool:
    stored = issued.get(id(value))
    return stored is not None and stored[0] is value


def _selection_issued_any(value: object) -> bool:
    if not isinstance(value, H1PreissuanceSelection):
        return False
    state = _issued_selections.get(id(value))
    return state is not None and state.selection is value


class H1PreissuanceRegistrationSource:
    """Public facade over the one runtime-private preissuance source port."""

    def __init__(self, runtime: object) -> None:
        self._runtime = runtime

    def _port(self) -> H1PreissuanceRegistrationPort:
        port = getattr(self._runtime, "_h1_preissuance_registration_source_port", None)
        methods = (
            "prepare_preissuance",
            "reopen_original",
            "check_current",
            "resolved_workspace_policy",
            "validate_original_selection",
            "build_delivery_observation",
        )
        if port is None or any(not callable(getattr(port, method, None)) for method in methods):
            raise H1PreissuanceSourceUnavailable(
                "H1 preissuance canonical runtime custody/source mount is absent"
            )
        return cast(H1PreissuanceRegistrationPort, port)

    async def prepare_preissuance(
        self, selected_run_identity: object, actor_observation: object | None = None
    ) -> H1PreissuanceSelection:
        """Prepare the original pre-workspace owner scope or fail closed."""

        port = self._port()
        result = (
            port.prepare_preissuance(selected_run_identity)
            if actor_observation is None
            else port.prepare_preissuance(selected_run_identity, actor_observation)
        )
        if not inspect.isawaitable(result):
            raise H1PreissuanceSourceViolation("H1 preissuance port returned a non-async result")
        selection = await result
        state = _selection_state(selection, port)
        if state is None or state.consumed:
            raise H1PreissuanceSourceViolation("H1 preissuance port returned an unissued selection")
        return selection

    def validate_actor_scope_refresh(
        self, selection: object, old_observation: object, new_observation: object
    ) -> bool:
        try:
            port = self._port()
        except H1PreissuanceSourceUnavailable:
            return False
        state = _selection_state(selection, port)
        validator = getattr(port, "validate_actor_scope_refresh", None)
        return (
            state is not None
            and not state.consumed
            and callable(validator)
            and validator(state.selection, old_observation, new_observation)
            is True
        )

    def reopen_original(self, verified_v2_issuance: object) -> H1PreissuanceSelection:
        """Mint a fresh process capability only from an issuer-verified V2 cut."""

        if not _issued_any(verified_v2_issuance, _issued_originals):
            raise H1PreissuanceSourceViolation(
                "H1 preissuance requires an issuer-verified original issuance"
            )
        port = self._port()
        if not _issued_by(verified_v2_issuance, _issued_originals, port):
            raise H1PreissuanceSourceViolation(
                "H1 preissuance requires an issuer-verified original issuance"
            )
        selection = port.reopen_original(
            cast(H1VerifiedOriginalWorkspaceIssuance, verified_v2_issuance)
        )
        state = _selection_state(selection, port)
        if state is None or state.consumed:
            raise H1PreissuanceSourceViolation("H1 preissuance port returned an unissued selection")
        return selection

    def check_current(self, selection: object) -> bool:
        """Synchronously ask the owner whether its retained cut still matches."""

        try:
            port = self._port()
        except H1PreissuanceSourceUnavailable:
            return False
        state = _selection_state(selection, port)
        if state is None or state.consumed:
            return False
        return port.check_current(state.selection) is True

    def resolved_workspace_policy(self, selection: object) -> object:
        """Return the port-derived V2 policy retaining this exact selection."""

        port = self._port()
        state = _selection_state(selection, port)
        if state is None or state.consumed:
            raise H1PreissuanceSourceViolation("H1 preissuance requires an issuer-held selection")
        from chiplog.composition.h1_workspace_policy_v2 import H1WorkspacePolicyV2

        policy = port.resolved_workspace_policy(state.selection)
        if type(policy) is not H1WorkspacePolicyV2:
            raise H1PreissuanceSourceViolation(
                "H1 preissuance port returned an unsupported workspace policy"
            )
        return policy

    def validate_original_selection(
        self, selection: object, verified_v2_issuance: object
    ) -> bool:
        """Recheck the persisted V2 join before a later H1 transition."""

        try:
            port = self._port()
        except H1PreissuanceSourceUnavailable:
            return False
        state = _selection_state(selection, port)
        if state is None or state.consumed or not _issued_by(
            verified_v2_issuance, _issued_originals, port
        ):
            return False
        return port.validate_original_selection(
            state.selection,
            cast(H1VerifiedOriginalWorkspaceIssuance, verified_v2_issuance),
        ) is True

    def build_delivery_observation(
        self, selection: object, authenticated_completion_inputs: object
    ) -> DeliveryObservation:
        """Build once, consuming the selection at the final owner-held fence."""

        if not _selection_issued_any(selection):
            raise H1PreissuanceSourceViolation("H1 preissuance requires an issuer-held selection")
        port = self._port()
        state = _selection_state(selection, port)
        if state is None or state.consumed:
            raise H1PreissuanceSourceViolation("H1 preissuance requires an issuer-held selection")
        if not _issued_by(authenticated_completion_inputs, _issued_completion_inputs, port):
            raise H1PreissuanceSourceViolation(
                "H1 preissuance requires issuer-authenticated completion inputs"
            )
        state.consumed = True
        observation = port.build_delivery_observation(
            state.selection,
            cast(H1AuthenticatedCompletionInputs, authenticated_completion_inputs),
        )
        if type(observation) is not DeliveryObservation:
            raise H1PreissuanceSourceViolation(
                "H1 preissuance port returned an unsupported delivery observation"
            )
        return observation


__all__ = [
    "H1AuthenticatedCompletionInputs",
    "H1PreissuanceRegistrationPort",
    "H1PreissuanceRegistrationSource",
    "H1PreissuanceSelection",
    "H1PreissuanceSourceUnavailable",
    "H1PreissuanceSourceViolation",
    "H1VerifiedOriginalWorkspaceIssuance",
]
