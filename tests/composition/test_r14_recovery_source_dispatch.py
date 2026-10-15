"""Native command reuse must still honor substituted source authentication."""

from types import SimpleNamespace

import pytest

from chiplog.composition import r14_native_recovery_historical as historical
from chiplog.composition import r14_recovery_publication as publication
from chiplog.composition.r14_native_recovery_cut import NativeRecoveryCutSource


@pytest.mark.parametrize("replacement", ("instance", "class", "bound-other"))
def test_replaced_native_authenticator_still_denies(monkeypatch, replacement):
    source = NativeRecoveryCutSource()
    denied = []

    def deny(*args):
        denied.append(True)
        return False

    other = NativeRecoveryCutSource()
    if replacement == "bound-other":
        monkeypatch.setattr(source, "authenticate_selected_held", other.authenticate_selected_held)
    else:
        monkeypatch.setattr(
            source if replacement == "instance" else NativeRecoveryCutSource,
            "authenticate_selected_held", deny,
        )
    runtime = SimpleNamespace(
        _full_recovery_cut_source=source,
        _authority_gate=lambda: SimpleNamespace(require_held=lambda: None),
    )
    physical = object()
    monkeypatch.setattr(
        publication, "retained", lambda entry: SimpleNamespace(actor_budget_v1=None)
    )
    monkeypatch.setattr(publication, "command", lambda evidence: physical)

    def native_check(receiver, *args, **kwargs):
        if receiver is other:
            return deny()
        assert receiver is source
        return True

    monkeypatch.setattr(historical, "_authenticate_native_recovery_selected", native_check)
    with pytest.raises(ValueError, match="unauthenticated"):
        publication.validate_selected(runtime, {}, physical)
    assert denied == [True]
