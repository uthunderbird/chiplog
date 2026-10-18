"""Inert first-path joins reject substitution; selection still belongs to the writer."""

import base64
import hashlib
import json
from collections.abc import Callable
from types import SimpleNamespace

import orjson
import pytest
from pydantic import TypeAdapter, ValidationError
from tests.support.capabilities.agent_loop.execution_first_path_completion_contracts import (
    request,
    source,
)

from chiplog.capabilities.agent_loop import _execution_completion_process as process
from chiplog.capabilities.agent_loop import execution_first_path_completion_contracts as contracts
from chiplog.capabilities.agent_loop import terminal_work_preparation
from chiplog.capabilities.agent_loop.completion_terminal_work_sources import (
    AcceptedCompletionWorkSourceV1,
)
from chiplog.capabilities.agent_loop.execution_completion_contracts import (
    ExecutionCompletionResult,
    PreparedExecutionCompletion,
    PrepareExecutionCompletion,
)
from chiplog.capabilities.agent_loop.post_terminal_contracts import (
    PreparedPostTerminalWork,
    PrepareTerminalWork,
    WorkCommandIdentity,
)
from chiplog.capabilities.agent_loop.recovery_contracts import (
    Absent,
)
from chiplog.capabilities.agent_loop.recovery_frontier_contracts import (
    FrontierMember,
    RecoveryFrontier,
)
from chiplog.capabilities.agent_loop.recovery_frontier_registry_contracts import (
    RECOVERY_FRONTIER_REGISTRY_SCHEMA,
    execution_h1_zero_call_frontier_registry_v2,
    frontier_registry_reference,
)
from chiplog.capabilities.agent_loop.rejected_completion_terminalization_contracts import (
    manifest_ref,
    run_ref,
)


async def test_roundtrip_discriminant_fingerprints_and_frozen_wire() -> None:
    value = await request()
    raw = value.canonical_bytes()
    assert contracts.decode_first_path_completion_request(raw) == value
    assert contracts.decode_completion_request(raw) == value
    assert (
        contracts.first_path_completion_request_fingerprint(value)
        == hashlib.sha256(raw).hexdigest()
    )
    assert value.exact_captured_response.endswith(b"\n")
    with pytest.raises(ValidationError):
        value.command_id = "changed"
    with pytest.raises(ValueError, match="noncanonical"):
        contracts.decode_first_path_completion_request(
            json.dumps(json.loads(raw), indent=2).encode()
        )
    wire = json.loads(raw)
    wire["kind"] = "PREPARE_EXECUTION_COMPLETION_V1"
    with pytest.raises(ValidationError):
        contracts.decode_completion_request(json.dumps(wire).encode())
    wire = json.loads(raw)
    wire["selector_generation"] = "0"
    with pytest.raises(ValidationError):
        contracts.PrepareExecutionCompletionFirstPathV2.model_validate_json(json.dumps(wire))


async def test_first_path_terminal_work_retains_accepted_source_bytes_without_obligations() -> None:
    original = await request(canonical_response=True)
    reply = process.dispatch(process.FIRST_PATH_OPERATION, original.canonical_bytes())
    payload = reply["payload"]
    assert isinstance(payload, str)
    prepared: ExecutionCompletionResult = TypeAdapter(ExecutionCompletionResult).validate_json(
        base64.b64decode(payload)
    )
    assert isinstance(prepared, PreparedExecutionCompletion)
    source = AcceptedCompletionWorkSourceV1(
        original_completion_request_bytes=original.canonical_bytes(),
        prepared_completion_bytes=prepared.canonical_bytes(),
        terminal_run=prepared.run,
        terminal_run_head=run_ref(prepared.run),
        terminal_manifest=prepared.terminal_manifest,
        terminal_manifest_head=manifest_ref(prepared.terminal_manifest),
        ordered_open_obligations=(),
    )
    terminal_work = PrepareTerminalWork(
        identity=WorkCommandIdentity(tenant_id=prepared.run.tenant, command_id=original.command_id),
        terminal_run=prepared.run,
        original_terminalization_request=source.canonical_bytes(),
        terminal_manifest=source.terminal_manifest_head,
        ordered_open_obligations=(),
    )

    result = terminal_work_preparation.prepare_h1_terminal_work(terminal_work)

    assert isinstance(result, PreparedPostTerminalWork)
    assert terminal_work.original_terminalization_request == source.canonical_bytes()
    assert result.ordered_work == ()
    assert result.complete_records == ()


@pytest.mark.parametrize(
    "mutation",
    [
        "capture",
        "seal",
        "source",
        "registry",
        "frontier",
        "frontier_run",
        "inventory",
        "admitted",
        "lineage",
    ],
)
async def test_cut_rejects_substituted_sources_even_with_rehashed_inventory(mutation: str) -> None:
    value = await request()
    cut = value.source
    if mutation == "capture":
        cut = cut.model_copy(update={"selected_capture": cut.current_run})
    elif mutation == "seal":
        cut = cut.model_copy(
            update={"seal": cut.seal.model_copy(update={"original_run_id": "foreign"})}
        )
    elif mutation == "source":
        changed = cut.complete_sources[0].model_copy(update={"canonical_record_bytes": b"fake"})
        cut = cut.model_copy(update={"complete_sources": (changed, *cut.complete_sources[1:])})
    elif mutation == "registry":
        frontier = cut.frontier.model_copy(
            update={"registry": cut.frontier.registry.model_copy(update={"fingerprint": "0" * 64})}
        )
        cut = cut.model_copy(update={"frontier": frontier})
    elif mutation == "frontier":
        cut = cut.model_copy(
            update={"frontier": cut.frontier.model_copy(update={"fingerprint": "0" * 64})}
        )
    elif mutation == "frontier_run":
        cut = cut.model_copy(
            update={"frontier": cut.frontier.model_copy(update={"run_id": "foreign"})}
        )
    elif mutation == "admitted":
        cut = cut.model_copy(
            update={
                "selected_admitted_input": cut.selected_admitted_input.model_copy(
                    update={"source_class": "TELEGRAM_PUSH"}
                )
            }
        )
    elif mutation == "lineage":
        cut = cut.model_copy(
            update={
                "complete_ordered_run_lineage": tuple(reversed(cut.complete_ordered_run_lineage))
            }
        )
    cut = cut.model_copy(
        update={
            "complete_inventory_fingerprint": "0" * 64
            if mutation == "inventory"
            else contracts.first_path_inventory_fingerprint(cut)
        }
    )
    with pytest.raises(ValueError):
        contracts.FirstPathCompletionCutV2.model_validate_json(cut.canonical_bytes())


@pytest.mark.parametrize(
    "mutation", ["response", "attempt", "generation", "visibility", "delivery", "fence"]
)
async def test_request_rejects_cross_source_joins_and_unvalidated_copies(mutation: str) -> None:
    value = await request()
    changes: dict[str, dict[str, object]] = {
        "response": {"exact_captured_response": b"fake"},
        "attempt": {"selected_attempt": value.source.current_run},
        "generation": {"selector_generation": 1},
        "visibility": {"visibility_manifest": value.source.current_run},
        "delivery": {"delivery": value.delivery.model_copy(update={"turn_id": "foreign"})},
        "fence": {"fence": value.fence.model_copy(update={"run_head": "stale"})},
    }
    changed = value.model_copy(update=changes[mutation])
    with pytest.raises(ValueError):
        contracts.first_path_completion_request_fingerprint(changed)


@pytest.mark.parametrize("family", ["TURN", "CALL", "OBLIGATION", "EXECUTION_LINEAGE"])
async def test_rehashed_frontier_cannot_hide_required_or_forbidden_heads(family: str) -> None:
    value = await request()
    members = []
    for member in value.source.frontier.ordered_members:
        if member.family == family:
            member = member.model_copy(
                update={
                    "ordered_heads": (Absent(),)
                    if family == "TURN"
                    else (value.source.current_run.revision,),
                }
            )
            member = member.model_copy(
                update={
                    "fingerprint": contracts.first_path_frontier_member_fingerprint(member),
                }
            )
        members.append(member)
    frontier = value.source.frontier.model_copy(update={"ordered_members": tuple(members)})
    frontier = frontier.model_copy(
        update={
            "fingerprint": contracts.first_path_frontier_fingerprint(frontier),
        }
    )
    cut = value.source.model_copy(update={"frontier": frontier})
    cut = cut.model_copy(
        update={
            "complete_inventory_fingerprint": contracts.first_path_inventory_fingerprint(cut),
        }
    )
    with pytest.raises(ValueError):
        contracts.FirstPathCompletionCutV2.model_validate_json(cut.canonical_bytes())


async def test_v1_request_bytes_remain_exact_through_versioned_decoder() -> None:
    from tests.support.completion_assembly import accepted_completion_fixture

    original = (
        await accepted_completion_fixture("v3", "empty")
    ).assembly.original_completion_request
    raw = original.canonical_bytes()
    assert contracts.decode_completion_request(raw).canonical_bytes() == raw
    with pytest.raises(ValueError):
        contracts.decode_first_path_completion_request(raw)


async def test_v1_cut_cannot_be_relabelled_as_v2_profile_without_workspace_carrier() -> None:
    value = await request()
    registry = execution_h1_zero_call_frontier_registry_v2()
    frontier = value.source.frontier.model_copy(update={"registry": registry})
    frontier = frontier.model_copy(
        update={"fingerprint": contracts.first_path_frontier_fingerprint(frontier)}
    )
    registry_source = source(
        RECOVERY_FRONTIER_REGISTRY_SCHEMA,
        frontier_registry_reference(registry),
        registry.canonical_bytes(),
    )
    cut = value.source.model_copy(
        update={
            "frontier": frontier,
            "complete_sources": (*value.source.complete_sources[:-1], registry_source),
        }
    )
    cut = cut.model_copy(
        update={"complete_inventory_fingerprint": contracts.first_path_inventory_fingerprint(cut)}
    )

    with pytest.raises(ValueError, match="V2 workspace carrier differs"):
        contracts.FirstPathCompletionCutV2.model_validate_json(cut.canonical_bytes())


async def test_first_path_request_reaches_its_mounted_owner_and_prepares_acceptance() -> None:
    value = await request(canonical_response=True)

    assert (
        process.FIRST_PATH_OPERATION,
        "broker",
        "agent_loop",
        contracts.FIRST_PATH_COMPLETION_SCHEMA,
        process.RESULT_SCHEMA,
    ) in process.ROUTES
    reply = process.dispatch(process.FIRST_PATH_OPERATION, value.canonical_bytes())

    assert reply["schema_id"] == process.RESULT_SCHEMA
    payload = reply["payload"]
    assert isinstance(payload, str)
    raw = base64.b64decode(payload)
    decoded: ExecutionCompletionResult = TypeAdapter(ExecutionCompletionResult).validate_json(raw)
    assert isinstance(decoded, PreparedExecutionCompletion)
    assert decoded.canonical_bytes() == raw
    assert decoded.source_request_fingerprint == hashlib.sha256(value.canonical_bytes()).hexdigest()
    assert decoded.complete_earlier_continuations == ()
    assert decoded.run.state == "SUCCEEDED"


async def test_legacy_completion_owner_route_keeps_its_existing_result_bytes() -> None:
    from tests.support.completion_assembly import accepted_completion_fixture

    from chiplog.capabilities.agent_loop.execution_completion_preparation import (
        prepare_execution_completion,
    )

    original = (
        await accepted_completion_fixture("v3", "empty")
    ).assembly.original_completion_request
    assert isinstance(original, PrepareExecutionCompletion)
    expected = prepare_execution_completion(original)

    reply = process.dispatch(process.OPERATION, original.canonical_bytes())

    assert reply == {
        "payload": base64.b64encode(expected.canonical_bytes()).decode(),
        "schema_id": process.RESULT_SCHEMA,
    }


@pytest.mark.parametrize("mutation", ["noncanonical", "rehash_splice"])
async def test_first_path_owner_rejects_noncanonical_and_rehashed_splice(mutation: str) -> None:
    value = await request()
    if mutation == "noncanonical":
        payload = json.dumps(json.loads(value.canonical_bytes()), indent=2).encode()
    else:
        cut = value.source.model_copy(update={"selected_capture": value.source.current_run})
        cut = cut.model_copy(
            update={
                "complete_inventory_fingerprint": contracts.first_path_inventory_fingerprint(cut)
            }
        )
        payload = value.model_copy(update={"source": cut}).canonical_bytes()

    reply = process.dispatch(process.FIRST_PATH_OPERATION, payload)

    assert reply["failure"] == "PROTOCOL_REJECTED"


async def test_first_path_fingerprints_encode_each_real_dto_once_natively(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Real producer DTOs exercise consistency only, not selected-history authority.
    prepared = await request()
    cut = prepared.source
    frontier = cut.frontier
    member = frontier.ordered_members[0]
    cases: tuple[
        tuple[
            str,
            str,
            FrontierMember | RecoveryFrontier | contracts.FirstPathCompletionCutV2,
            Callable[[], str],
        ],
        ...,
    ] = (
        (
            "chiplog.execution.first-path-frontier-member.v2",
            "fingerprint",
            member,
            lambda: contracts.first_path_frontier_member_fingerprint(member),
        ),
        (
            "chiplog.execution.first-path-frontier.v2",
            "fingerprint",
            frontier,
            lambda: contracts.first_path_frontier_fingerprint(frontier),
        ),
        (
            contracts.FIRST_PATH_CUT_SCHEMA,
            "complete_inventory_fingerprint",
            cut,
            lambda: contracts.first_path_inventory_fingerprint(cut),
        ),
    )
    original_std_dumps = json.dumps
    original_native_dumps = orjson.dumps
    for domain, excluded, value, fingerprint in cases:
        content = value.model_dump(mode="json")
        del content[excluded]
        expected = original_std_dumps(
            {"domain": domain, "content": content},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
        std_calls = 0
        native_calls = 0
        preimages: list[bytes] = []

        def std_dumps(
            value: object,
            *,
            sort_keys: bool,
            separators: tuple[str, str],
            ensure_ascii: bool,
        ) -> str:
            nonlocal std_calls
            std_calls += 1
            return original_std_dumps(
                value, sort_keys=sort_keys, separators=separators, ensure_ascii=ensure_ascii
            )

        def native_dumps(value: object, *, option: int) -> bytes:
            nonlocal native_calls
            native_calls += 1
            return original_native_dumps(value, option=option)

        def observed_sha(raw: bytes, observed: list[bytes] = preimages) -> str:
            observed.append(raw)
            return hashlib.sha256(raw).hexdigest()

        with monkeypatch.context() as patch:
            patch.setattr(contracts, "json", SimpleNamespace(dumps=std_dumps))
            patch.setattr(
                contracts,
                "orjson",
                SimpleNamespace(
                    dumps=native_dumps,
                    OPT_SORT_KEYS=orjson.OPT_SORT_KEYS,
                    JSONEncodeError=orjson.JSONEncodeError,
                ),
                raising=False,
            )
            patch.setattr(contracts, "_sha", observed_sha)
            assert fingerprint() == hashlib.sha256(expected).hexdigest()
        assert preimages == [expected]
        assert std_calls == 0
        assert native_calls == 1


@pytest.mark.parametrize("sequence", [0, 2**63, 2**64 - 1])
async def test_first_path_native_fingerprint_preserves_unicode_and_uint64_bytes(
    sequence: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = (await request()).source.frontier
    fields = original.model_dump(mode="python")
    fields.update(tenant_id="тенант\n\t\x00é😀", tenant_commit_sequence=sequence)
    frontier = RecoveryFrontier.model_validate(fields)
    content = frontier.model_dump(mode="json")
    del content["fingerprint"]
    expected = json.dumps(
        {"domain": "chiplog.execution.first-path-frontier.v2", "content": content},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode()
    observed: list[bytes] = []

    def sha(raw: bytes) -> str:
        observed.append(raw)
        return hashlib.sha256(raw).hexdigest()

    monkeypatch.setattr(contracts, "_sha", sha)
    assert (
        contracts.first_path_frontier_fingerprint(frontier) == hashlib.sha256(expected).hexdigest()
    )
    assert observed == [expected]


async def test_first_path_native_encoding_error_retains_legacy_preimage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    member = (await request()).source.frontier.ordered_members[0]
    content = member.model_dump(mode="json")
    del content["fingerprint"]
    expected = json.dumps(
        {"domain": "chiplog.execution.first-path-frontier-member.v2", "content": content},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode()
    calls = 0
    observed: list[bytes] = []

    def unavailable(value: object, *, option: int) -> bytes:
        nonlocal calls
        calls += 1
        raise orjson.JSONEncodeError("native encoding refused")

    def sha(raw: bytes) -> str:
        observed.append(raw)
        return hashlib.sha256(raw).hexdigest()

    monkeypatch.setattr(
        contracts,
        "orjson",
        SimpleNamespace(
            dumps=unavailable,
            OPT_SORT_KEYS=orjson.OPT_SORT_KEYS,
            JSONEncodeError=orjson.JSONEncodeError,
        ),
    )
    monkeypatch.setattr(contracts, "_sha", sha)
    assert (
        contracts.first_path_frontier_member_fingerprint(member)
        == hashlib.sha256(expected).hexdigest()
    )
    assert calls == 1
    assert observed == [expected]
