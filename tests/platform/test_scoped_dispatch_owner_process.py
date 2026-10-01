"""The v21 scoped-dispatch route crosses the spawned effects-owner boundary."""

from __future__ import annotations

import hashlib
import os
import time
from typing import cast

import pytest
from pydantic import TypeAdapter

from chiplog.adapters.driven.effects_hermetic import HermeticEffectsProvider
from chiplog.adapters.driven.loop_hermetic import HermeticModel
from chiplog.architecture.r7_runtime import (
    R14_R17_H1_SCOPED_EFFECTS_J7_PRODUCTION_MANIFEST,
    R14_R17_H1_SCOPED_EFFECTS_V3_J7_PRODUCTION_MANIFEST,
)
from chiplog.capabilities.effects.contracts import CommandIdentity, ExactHead
from chiplog.capabilities.effects.dispatch_v2 import reference
from chiplog.capabilities.effects.dispatch_v2_contracts import CurrentDispatchInputsV2
from chiplog.capabilities.effects.fences import Absent
from chiplog.capabilities.effects.lifecycle_transition_contracts import SelectedEffectsSource
from chiplog.capabilities.effects.scoped_authorization_record_contracts import (
    REVISION_SCHEMA,
    make_scoped_authorization_owner_records,
    scoped_dispatch_authorization_head,
    scoped_pre_send_revision_head,
)
from chiplog.capabilities.effects.scoped_dispatch_contracts import (
    PreparedScopedAuthorization,
    PrepareScopedAuthorization,
    PrepareScopedFirstSend,
    ScopedDispatchResult,
    ScopedPreSendCut,
    SelectedScopedAuthorization,
)
from chiplog.capabilities.effects.scoped_dispatch_preparation import prepare_scoped_dispatch
from chiplog.capabilities.effects.scoped_intent_contracts import ScopedAuthorityRecord
from chiplog.capabilities.effects.scoped_intent_record_contracts import make_scoped_intent_member
from chiplog.platform.broker import (
    BrokerSession,
    CallBudget,
    PublicPortCall,
    PublicPortRejected,
    PublicPortSuccess,
)
from chiplog.platform.r7_leaves import ProductionClock, ProductionPlanningStore
from chiplog.platform.r7_runtime import AuthorityBrokerRuntime
from tests.support.scoped_intent_records import delivery_publication_fixture


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _head(name: str, raw: bytes | None = None) -> ExactHead:
    value = name.encode() if raw is None else raw
    return ExactHead(subject_id=name, head=name + "/" + _sha(value), fingerprint=_sha(value))


def _source(name: str) -> ScopedAuthorityRecord:
    raw = ("authority:" + name).encode()
    return ScopedAuthorityRecord(
        owner="effects",
        head=_head(name, raw),
        schema_id="chiplog.effects.authority.v3",
        canonical_record_bytes=raw,
        selected_decision=_head("decision:" + name),
    )


def _selected(
    name: str,
    raw: bytes,
    *,
    schema_id: str,
    head: ExactHead | None = None,
    physical_head: ExactHead | None = None,
) -> SelectedEffectsSource:
    selected_head = head or _head(name, raw)
    return SelectedEffectsSource(
        owner="effects",
        subject=selected_head,
        schema_id=schema_id,
        canonical_record_bytes=raw,
        selected_decision=_head("decision:" + name),
        physical_record=physical_head or selected_head,
    )


async def _authorization_request() -> PrepareScopedAuthorization:
    fixture = await delivery_publication_fixture()
    intent = fixture.intent
    identity = CommandIdentity(
        command_id="authorize-v3", fingerprint="a" * 64, expected_tenant_head=0
    )
    current = cast(CurrentDispatchInputsV2, fixture.request.current).model_copy(
        update={
            "command_fingerprint": identity.fingerprint,
            "immutable_mandate": reference(
                intent.mandate.mandate_id, intent.mandate.canonical_bytes()
            ),
            "observation": fixture.request.current.observation.model_copy(
                update={"query_fingerprint": identity.fingerprint}
            ),
        }
    )
    member = make_scoped_intent_member(intent)
    selected_intent = _selected(
        intent.intent_id,
        member.canonical_record_bytes,
        schema_id="chiplog.effects.external-action-intent.v3",
        head=ExactHead(
            subject_id=member.record_id,
            head=member.record_id,
            fingerprint=member.fingerprint,
        ),
    )
    return PrepareScopedAuthorization(
        identity=identity,
        observed=ScopedPreSendCut(
            original_intent=intent,
            selected_intent=selected_intent,
            current_parent=selected_intent,
            state="INTENT_RECORDED",
            authorization=Absent(),
        ),
        current=current,
        complete_current_origin_sources=(_source("authorize-current"),),
        fence=fixture.request.fence,
    )


async def _first_send_request() -> PrepareScopedFirstSend:
    authorization_request = await _authorization_request()
    prepared = prepare_scoped_dispatch(authorization_request)
    assert isinstance(prepared, PreparedScopedAuthorization)
    identity = CommandIdentity(command_id="send-v3", fingerprint="b" * 64, expected_tenant_head=0)
    current = authorization_request.current.model_copy(
        update={
            "command_fingerprint": identity.fingerprint,
            "observation": authorization_request.current.observation.model_copy(
                update={"query_fingerprint": identity.fingerprint}
            ),
        }
    )
    record_head = reference(
        prepared.authorization.authorization_id, prepared.authorization.canonical_bytes()
    )
    authorization_row, parent_row = make_scoped_authorization_owner_records(
        prepared.authorization, prepared.parent
    )
    authorization_semantic = scoped_dispatch_authorization_head(prepared.authorization)
    assert authorization_semantic == record_head
    authorization_physical = ExactHead(
        subject_id=authorization_row.record_id,
        head=authorization_row.record_id,
        fingerprint=authorization_row.fingerprint,
    )
    parent_semantic = scoped_pre_send_revision_head(prepared.parent)
    parent_physical = ExactHead(
        subject_id=parent_row.record_id,
        head=parent_row.record_id,
        fingerprint=parent_row.fingerprint,
    )
    selected_authorization = SelectedScopedAuthorization(
        source=_selected(
            prepared.authorization.authorization_id,
            prepared.authorization.canonical_bytes(),
            schema_id="chiplog.effects.scoped-authorization.v3",
            head=authorization_semantic,
            physical_head=authorization_physical,
        ),
        record=prepared.authorization,
    )
    return PrepareScopedFirstSend(
        identity=identity,
        observed=ScopedPreSendCut(
            original_intent=authorization_request.observed.original_intent,
            selected_intent=authorization_request.observed.selected_intent,
            current_parent=_selected(
                "authorized-parent",
                prepared.parent.canonical_bytes(),
                schema_id=REVISION_SCHEMA,
                head=parent_semantic,
                physical_head=parent_physical,
            ),
            state="DISPATCH_AUTHORIZED",
            authorization=selected_authorization,
        ),
        current=current,
        complete_current_origin_sources=(_source("send-current"),),
        fence=authorization_request.fence,
        selected_authorization=record_head,
        ordinal=0,
    )


@pytest.mark.asyncio
async def test_spawned_v21_owner_returns_canonical_success_and_typed_denial() -> None:
    manifest = R14_R17_H1_SCOPED_EFFECTS_V3_J7_PRODUCTION_MANIFEST
    leaves = {
        "clock": ProductionClock(),
        "planning_store": ProductionPlanningStore(),
        "model": HermeticModel(),
        "effects_transport": HermeticEffectsProvider(receipt_key=b"fixture", scenarios=()),
    }
    with AuthorityBrokerRuntime(
        "tenant", 1, "generation", manifest, b"secret", realized_leaves=leaves
    ) as runtime:
        effects = next(item for item in runtime.attest() if item.identity.owner_id == "effects")
        assert effects.process_id != os.getpid()
        callee = runtime.session("effects")
        caller = BrokerSession(
            tenant_id=callee.tenant_id,
            broker_epoch=callee.broker_epoch,
            generation_id=callee.generation_id,
            owner_id="broker",
            session_id="broker",
        )

        async def send(
            request: PrepareScopedAuthorization | PrepareScopedFirstSend,
            *,
            schema: str = "chiplog.effects.scoped-dispatch-request.v3",
            payload: bytes | None = None,
        ) -> object:
            return await runtime.call(
                PublicPortCall(
                    operation_id="effects.prepare_scoped_dispatch_v3",
                    request_id=request.identity.command_id,
                    caller=caller,
                    callee=callee,
                    schema_id=schema,
                    canonical_payload=request.canonical_bytes() if payload is None else payload,
                    budget=CallBudget(
                        remaining_calls=1,
                        remaining_depth=1,
                        policy_version=1,
                        absolute_deadline_ns=time.monotonic_ns() + 5_000_000_000,
                    ),
                )
            )

        adapter: TypeAdapter[ScopedDispatchResult] = TypeAdapter(ScopedDispatchResult)
        for request in (await _authorization_request(), await _first_send_request()):
            reply = await send(request)
            assert isinstance(reply, PublicPortSuccess)
            assert reply.schema_id == "chiplog.effects.scoped-dispatch-result.v3"
            result = adapter.validate_json(reply.canonical_payload, strict=True)
            assert result.canonical_bytes() == reply.canonical_payload
            assert reply.canonical_payload == prepare_scoped_dispatch(request).canonical_bytes()

        authorization = await _authorization_request()
        denied_request = authorization.model_copy(
            update={
                "observed": authorization.observed.model_copy(update={"state": "HELD_BEFORE_SEND"})
            }
        )
        denied = await send(denied_request)
        assert isinstance(denied, PublicPortSuccess)
        assert denied.schema_id == "chiplog.effects.scoped-dispatch-result.v3"
        assert adapter.validate_json(denied.canonical_payload, strict=True).kind == "DENIED"

        rejected = await send(await _authorization_request(), payload=b"{")
        assert isinstance(rejected, PublicPortRejected)
        assert rejected.failure.kind == "PROTOCOL_REJECTED"
        noncanonical_request = await _authorization_request()
        noncanonical = await send(
            noncanonical_request,
            payload=noncanonical_request.canonical_bytes() + b" ",
        )
        assert isinstance(noncanonical, PublicPortRejected)
        assert noncanonical.failure.kind == "PROTOCOL_REJECTED"
        wrong_schema = await send(
            await _authorization_request(),
            schema="chiplog.effects.h1-scoped-delivery-owner-call.v1",
        )
        assert isinstance(wrong_schema, PublicPortRejected)
        assert wrong_schema.failure.kind == "PROTOCOL_REJECTED"


def test_v20_process_target_and_route_set_remain_historical() -> None:
    manifest = R14_R17_H1_SCOPED_EFFECTS_J7_PRODUCTION_MANIFEST
    effects = next(owner for owner in manifest.owners if owner.owner_id == "effects")
    assert effects.target_ids == ("chiplog.capabilities.effects._h1_scoped_process:dispatch",)
    assert "effects.prepare_scoped_dispatch_v3" not in effects.capability_ids
    assert "effects.prepare_scoped_dispatch_v3" not in {
        route.operation_id for route in manifest.routes
    }


