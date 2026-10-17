"""The pre-model fence accepts only its own exact emission suffix."""

from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from chiplog.capabilities.agent_loop.execution_contracts import ExecutionRunRecord
from chiplog.composition import h1_runtime_preissuance_port as port_module
from chiplog.composition.h1_preissuance_registration import (
    H1PreissuanceSelection,
    H1PreissuanceSourceViolation,
)
from chiplog.composition.h1_runtime_preissuance_port import _H1RuntimePreissuancePort
from chiplog.platform.authority_gate import AuthorityGate
from tests.support.execution_fan_out import fixture


@pytest.mark.parametrize("change", ["none", "suffix", "worker", "custody", "source", "trust"])
async def test_pre_model_selection_rechecks_suffix_and_original_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    base = (await fixture()).captured_run
    suffix: list[ExecutionRunRecord] = []
    for index, event in enumerate(
        ("TurnStarted", "TurnVisibilityAccumulated", "ModelAttemptPrepared", "ModelAttemptEmitted")
    ):
        suffix.append(
            base.model_copy(
                update={
                    "event": event,
                    "head": f"head-{index}",
                    "predecessor": "previous" if index == 0 else suffix[-1].head,
                }
            )
        )
    trust = object()
    cut = SimpleNamespace(
        prepared=SimpleNamespace(run=suffix[0], custody_digest="original", custody_generation=1),
        append_receipt=SimpleNamespace(after=trust),
    )
    runtime = SimpleNamespace(
        current_worker=lambda: "other" if change == "worker" else base.worker_session,
        _trust=SimpleNamespace(
            capture_verified_observation=lambda: object() if change == "trust" else trust
        ),
    )
    port = object.__new__(_H1RuntimePreissuancePort)
    object.__setattr__(port, "_gate", AuthorityGate.for_database(tmp_path / "runtime.sqlite"))
    object.__setattr__(port, "_runtime", runtime)
    monkeypatch.setattr(_H1RuntimePreissuancePort, "_cut", lambda *_: cut)
    monkeypatch.setattr(_H1RuntimePreissuancePort, "_assert_launch_and_trust", lambda _: None)
    monkeypatch.setattr(port_module, "_selection_state", lambda *_: SimpleNamespace(consumed=False))
    lineage = list(suffix)
    if change == "suffix":
        lineage.append(suffix[-1].model_copy(update={"head": "intervening"}))
    monkeypatch.setattr(
        port_module, "read_execution_history", lambda _: SimpleNamespace(records=lineage)
    )

    def source_join(*_: object) -> tuple[None, None, str, int]:
        if change == "source":
            raise H1PreissuanceSourceViolation("selected source changed")
        return None, None, "changed" if change == "custody" else "original", 1

    monkeypatch.setattr(_H1RuntimePreissuancePort, "_selected_scope_join", source_join)
    # Inert records isolate this predicate; the mounted extractor test proves
    # the real issuer and original source reads, without these substitutions.
    selection = cast(H1PreissuanceSelection, object())
    assert port.validate_pre_model_selection(selection, suffix[1], suffix[2], suffix[3]) is (
        change == "none"
    )
