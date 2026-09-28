"""Canonical installed-H1 lifecycle for the inert live publication authority."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest

import chiplog.composition.r14_loop_history as loop_history
from chiplog.composition.common_cli_execution_runtime import (
    CommonCliExecutionRuntime,
    _H1LiveCompletionMount,
    open_common_cli_execution_runtime,
    open_installed_h1_runtime,
)
from chiplog.composition.h1_completion_preparation_session import H1CompletionSessionCut
from chiplog.composition.h1_conversation_sources import H1ConversationSources
from chiplog.composition.h1_delivery_evidence_journal import H1DeliveryEvidenceJournal
from chiplog.composition.h1_launch_enrollment import _open_installed_h1_launch
from chiplog.composition.h1_live_completion_enrollment import (
    H1LiveCompletionEnrollmentUnavailable,
    _H1LiveCompletionEnrollment,
)
from chiplog.composition.h1_live_publication_authority import H1LivePublicationAuthority
from chiplog.composition.h1_runtime_preissuance_port import _H1RuntimePreissuancePort
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources
from chiplog.platform._sqlite import EventAppender
from chiplog.platform.broker import BrokerSession, CallBudget, PublicPortCall
from tests.support.h1_installed_launch import installed_slot, prepare_installed_slot


def _resources(tmp_path: Path) -> HermeticDispatchResources:
    return HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "dispatch-custody"
    )


@pytest.mark.asyncio
async def test_installed_h1_mounts_historical_owners_before_selected_startup_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    observed: list[tuple[object, object]] = []
    original = loop_history.validate_selected_h1_completions

    def observe(runtime: CommonCliExecutionRuntime) -> None:
        port = runtime._h1_preissuance_registration_source_port
        conversation = runtime._h1_conversation_source_port
        assert type(port) is _H1RuntimePreissuancePort
        assert type(conversation) is H1ConversationSources
        assert port._runtime is conversation._runtime is runtime
        assert port._gate is conversation._gate is runtime._authority_gate()
        observed.append((port, conversation))
        original(runtime)

    monkeypatch.setattr(loop_history, "validate_selected_h1_completions", observe)
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=_resources(tmp_path)) as runtime:
            assert observed == [
                (
                    runtime._h1_preissuance_registration_source_port,
                    runtime._h1_conversation_source_port,
                )
            ]
        assert cast(H1ConversationSources, observed[0][1])._closed is True
        assert not hasattr(runtime, "_h1_preissuance_registration_source_port")
        assert not hasattr(runtime, "_h1_conversation_source_port")


@pytest.mark.asyncio
async def test_installed_h1_revokes_early_historical_owners_when_startup_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    observed: dict[str, object] = {}

    def fail(runtime: CommonCliExecutionRuntime) -> None:
        observed["runtime"] = runtime
        observed["conversation"] = runtime._h1_conversation_source_port
        raise RuntimeError("injected selected startup failure")

    monkeypatch.setattr(loop_history, "validate_selected_h1_completions", fail)
    with (
        _open_installed_h1_launch(slot) as launch,
        pytest.raises(RuntimeError, match="injected selected startup failure"),
    ):
        async with open_installed_h1_runtime(launch, resources=_resources(tmp_path)):
            raise AssertionError("selected startup failure must prevent yielding")
    runtime = observed["runtime"]
    assert cast(H1ConversationSources, observed["conversation"])._closed is True
    assert not hasattr(runtime, "_h1_preissuance_registration_source_port")
    assert not hasattr(runtime, "_h1_conversation_source_port")


@pytest.mark.asyncio
async def test_installed_h1_mounts_exact_store_resolver_before_event_appender(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    observed_resolvers: list[object | None] = []
    original_init = EventAppender.__init__

    def observe_appender(
        self: object, materializer: Any, *args: object, **kwargs: object
    ) -> None:
        observed_resolvers.append(materializer._owner_publication_resolver)
        original_init(self, materializer, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(EventAppender, "__init__", observe_appender)

    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=_resources(tmp_path)) as runtime:
            private_runtime = cast(Any, runtime)
            authority = private_runtime._h1_live_publication_authority
            journal = private_runtime._h1_delivery_evidence_journal
            coordinator = private_runtime._h1_live_publication_coordinator

            assert type(authority) is H1LivePublicationAuthority
            assert observed_resolvers == [authority]
            assert private_runtime._appender._materializer._owner_publication_resolver is authority
            assert journal._root_issuer is authority
            assert coordinator._authority is authority
            assert coordinator._appender is runtime._appender
            assert (
                type(private_runtime._h1_live_completion_enrollment)
                is _H1LiveCompletionEnrollment
            )

        assert authority._revoked is True
        assert journal._root_issuer is None
        assert not hasattr(runtime, "_h1_live_publication_authority")
        assert not hasattr(runtime, "_h1_live_publication_coordinator")
        assert not hasattr(runtime, "_h1_live_completion_mount")


@pytest.mark.asyncio
async def test_installed_h1_binds_root_only_after_the_runtime_graph_is_mounted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    original_bind = H1DeliveryEvidenceJournal._bind_private_root_issuer
    observed: dict[str, object] = {}

    def capture_bind(
        self: H1DeliveryEvidenceJournal, issuer: object, installation: object
    ) -> None:
        runtime = cast(Any, cast(_H1LiveCompletionMount, installation)._runtime)
        observed["journal"] = self
        observed["issuer"] = issuer
        observed["installation"] = installation
        assert type(installation) is _H1LiveCompletionMount
        assert runtime._h1_delivery_evidence_journal is self
        assert runtime._h1_live_completion_mount is installation
        assert runtime._h1_live_publication_authority is issuer
        assert runtime._h1_live_publication_coordinator._authority is issuer
        assert runtime._appender._materializer._owner_publication_resolver is issuer
        assert runtime._appender._materializer._writer_token is not None
        runtime._authority_gate().require_held()
        original_bind(self, issuer, installation)

    monkeypatch.setattr(H1DeliveryEvidenceJournal, "_bind_private_root_issuer", capture_bind)

    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=_resources(tmp_path)):
            pass

    assert type(observed["installation"]) is _H1LiveCompletionMount
    assert cast(H1DeliveryEvidenceJournal, observed["journal"])._root_issuer is None


@pytest.mark.asyncio
async def test_installed_h1_enrollment_opens_only_its_private_b_session_and_revokes(
    tmp_path: Path,
) -> None:
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)

    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=_resources(tmp_path)) as runtime:
            private_runtime = cast(Any, runtime)
            enrollment = private_runtime._h1_live_completion_enrollment
            session = enrollment._open_session()

            assert session._sources is private_runtime._h1_first_path_sources
            with pytest.raises(H1LiveCompletionEnrollmentUnavailable, match="not enrolled"):
                enrollment._require_live(
                    type(session)(
                        first_path_sources=private_runtime._h1_first_path_sources
                    ),
                    object(),
                )
            with pytest.raises(H1LiveCompletionEnrollmentUnavailable, match="cut is foreign"):
                enrollment._require_live(session, object())

        assert not hasattr(runtime, "_h1_live_completion_enrollment")
        with pytest.raises(H1LiveCompletionEnrollmentUnavailable, match="closed"):
            enrollment._open_session()


@pytest.mark.asyncio
async def test_terminal_reservation_uses_only_an_earlier_pinned_live_cut(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)

    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=_resources(tmp_path)) as runtime:
            private_runtime = cast(Any, runtime)
            enrollment = private_runtime._h1_live_completion_enrollment
            session = enrollment._open_session()
            cut = H1CompletionSessionCut(
                original_identity=cast(Any, object()),
                original_fingerprint="0" * 64,
                selected_seal=cast(Any, object()),
                first_path=cast(Any, object()),
                conversation=object(),
            )
            session._cut = cut
            sent = PublicPortCall(
                operation_id="agent_loop.prepare_terminal_work",
                request_id="terminal-reservation",
                caller=BrokerSession(
                    tenant_id="tenant",
                    broker_epoch=1,
                    generation_id="generation",
                    owner_id="broker",
                    session_id="broker:generation",
                ),
                callee=BrokerSession(
                    tenant_id="tenant",
                    broker_epoch=1,
                    generation_id="generation",
                    owner_id="agent_loop",
                    session_id="agent-loop:generation",
                ),
                schema_id="chiplog.agent-loop.prepare-terminal-work.v1",
                canonical_payload=b"{}",
                budget=CallBudget(
                    remaining_calls=1,
                    remaining_depth=1,
                    policy_version=1,
                    absolute_deadline_ns=1,
                ),
            )

            with pytest.raises(H1LiveCompletionEnrollmentUnavailable, match="not validated"):
                enrollment._reserve_terminal_clearance(
                    session=session,
                    cut=cut,
                    sent=sent,
                    scope_snapshot=object(),
                    preterminal_wire=object(),
                )
            assert enrollment._clearances == {}

            enrollment._records[id(session)].cut = cut

            def fail_full_replay(*_: object) -> None:
                raise AssertionError("reservation must not perform the full live replay")

            monkeypatch.setattr(enrollment, "_require_live", fail_full_replay)
            clearance = enrollment._reserve_terminal_clearance(
                session=session,
                cut=cut,
                sent=sent,
                scope_snapshot=object(),
                preterminal_wire=object(),
            )

            clearance_record = enrollment._clearances[id(clearance)]
            assert clearance_record.enrollment is enrollment._records[id(session)]


@pytest.mark.asyncio
async def test_generic_runtime_has_no_live_authority_or_resolver(tmp_path: Path) -> None:
    async with open_common_cli_execution_runtime(
        tmp_path / "generic.sqlite3", resources=_resources(tmp_path)
    ) as runtime:
        assert not hasattr(runtime, "_h1_live_publication_authority")
        assert not hasattr(runtime, "_h1_live_publication_coordinator")
        assert not hasattr(runtime, "_h1_live_completion_mount")
        assert not hasattr(runtime, "_h1_live_completion_enrollment")
        assert runtime._appender._materializer._owner_publication_resolver is None


@pytest.mark.asyncio
async def test_partial_installed_setup_does_not_bind_live_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    observed: dict[str, Any] = {}
    original_bind = H1DeliveryEvidenceJournal._bind_private_root_issuer

    def capture_bind(
        self: H1DeliveryEvidenceJournal, issuer: object, installation: object
    ) -> None:
        observed["journal"] = self
        observed["authority"] = issuer
        original_bind(self, issuer, installation)

    def fail_runtime_setup(self: CommonCliExecutionRuntime, _: Path) -> None:
        raise RuntimeError("injected runtime setup failure")

    monkeypatch.setattr(H1DeliveryEvidenceJournal, "_bind_private_root_issuer", capture_bind)
    monkeypatch.setattr(
        CommonCliExecutionRuntime, "_bind_h1_historical_custody_path", fail_runtime_setup
    )

    with (
        _open_installed_h1_launch(slot) as launch,
        pytest.raises(RuntimeError, match="injected runtime setup failure"),
    ):
        async with open_installed_h1_runtime(launch, resources=_resources(tmp_path)):
            raise AssertionError("runtime setup failure must prevent yielding")

    assert observed == {}
