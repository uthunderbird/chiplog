from __future__ import annotations

import os
from dataclasses import replace

import pytest

from chiplog.architecture.r7_runtime import R7_PRODUCTION_MANIFEST
from chiplog.composition.r7_supervisor import _verify_realized_graph_exact
from chiplog.platform.r7_runtime import (
    AuthorityBrokerRuntime,
)


def test_broker_starts_one_distinct_dishka_graph_process_per_owner() -> None:
    with AuthorityBrokerRuntime(
        "tenant-1",
        1,
        "generation-1",
        R7_PRODUCTION_MANIFEST,
        b"test-session-secret",
    ) as runtime:
        attestations = runtime.attest()
        assert tuple(item.identity.owner_id for item in attestations) == (
            "deployment_trust",
            "planning",
            "projections",
        )
        assert len({item.process_id for item in attestations}) == 3
        assert all(item.process_id != os.getpid() for item in attestations)
        assert all(item.parent_process_id == os.getpid() for item in attestations)
        assert all(item.dishka_scope == "APP" for item in attestations)
        assert all(item.identity.broker_epoch == 1 for item in attestations)
        assert all(len(item.loaded_policy_modules) == 1 for item in attestations)
        assert len({item.loaded_policy_modules[0] for item in attestations}) == 3
        assert all(not owner.raw_capabilities for owner in R7_PRODUCTION_MANIFEST.owners)
        graph = runtime.graph_generation()
        assert graph.manifest_digest == R7_PRODUCTION_MANIFEST.fingerprint()
        assert tuple(owner.owner_id for owner in graph.owners) == tuple(
            owner.owner_id for owner in R7_PRODUCTION_MANIFEST.owners
        )
        assert all(
            owner.provider_ids and owner.target_ids and owner.factory_ids for owner in graph.owners
        )
        assert graph.routes == tuple(
            (
                route.operation_id,
                route.caller_owner_id,
                route.callee_owner_id,
                route.request_schema_id,
                route.result_schema_id,
            )
            for route in R7_PRODUCTION_MANIFEST.routes
        )


def test_generation_teardown_closes_all_owner_processes() -> None:
    runtime = AuthorityBrokerRuntime(
        "tenant-1",
        2,
        "generation-2",
        R7_PRODUCTION_MANIFEST,
        b"test-session-secret",
    )
    runtime.start()
    pids = {item.process_id for item in runtime.attest()}
    runtime.close()
    assert all(not _process_exists(process_id) for process_id in pids)
    assert runtime.teardown_trace == tuple(
        owner.owner_id for owner in reversed(R7_PRODUCTION_MANIFEST.owners)
    )


def test_exact_realized_graph_rejects_every_owner_and_leaf_identity_mutant() -> None:
    generation = "generation-exact"
    with AuthorityBrokerRuntime(
        "tenant-1", 1, generation, R7_PRODUCTION_MANIFEST, b"test-session-secret"
    ) as runtime:
        graph = runtime.graph_generation()
    first = graph.owners[0]
    mutants = (
        replace(graph, manifest_digest="0" * 64),
        replace(graph, owners=(replace(first, owner_id="foreign"), *graph.owners[1:])),
        replace(graph, owners=(graph.owners[1], graph.owners[0], *graph.owners[2:])),
        replace(graph, owners=(replace(first, capability_ids=()), *graph.owners[1:])),
        replace(
            graph,
            owners=(
                replace(first, capability_ids=("foreign", *first.capability_ids[1:])),
                *graph.owners[1:],
            ),
        ),
        replace(
            graph,
            owners=(
                replace(first, capability_ids=(*first.capability_ids, "foreign")),
                *graph.owners[1:],
            ),
        ),
        replace(graph, owners=(replace(first, provider_ids=("foreign",)), *graph.owners[1:])),
        replace(graph, owners=(replace(first, provider_ids=()), *graph.owners[1:])),
        replace(
            graph,
            owners=(
                replace(first, provider_ids=(*first.provider_ids, "foreign")),
                *graph.owners[1:],
            ),
        ),
        replace(graph, owners=(replace(first, target_ids=("foreign",)), *graph.owners[1:])),
        replace(graph, owners=(replace(first, target_ids=()), *graph.owners[1:])),
        replace(
            graph,
            owners=(replace(first, target_ids=(*first.target_ids, "foreign")), *graph.owners[1:]),
        ),
        replace(graph, owners=(replace(first, factory_ids=("foreign",)), *graph.owners[1:])),
        replace(graph, owners=(replace(first, factory_ids=()), *graph.owners[1:])),
        replace(
            graph,
            owners=(replace(first, factory_ids=(*first.factory_ids, "foreign")), *graph.owners[1:]),
        ),
        replace(graph, owners=(replace(first, scopes=("REQUEST",)), *graph.owners[1:])),
        replace(graph, owners=(replace(first, scopes=()), *graph.owners[1:])),
        replace(
            graph, owners=(replace(first, scopes=(*first.scopes, "REQUEST")), *graph.owners[1:])
        ),
        replace(
            graph,
            owners=(
                replace(first, process_identity=graph.owners[1].process_identity),
                *graph.owners[1:],
            ),
        ),
        replace(graph, owners=(replace(first, session_id="foreign"), *graph.owners[1:])),
        replace(graph, owners=(replace(first, generation_id="foreign"), *graph.owners[1:])),
        replace(graph, leaves=(("clock", "broker", "foreign"), *graph.leaves[1:])),
        replace(graph, owners=graph.owners[:-1]),
        replace(graph, owners=(*graph.owners, first)),
        replace(graph, leaves=graph.leaves[:-1]),
        replace(graph, leaves=(*graph.leaves, graph.leaves[0])),
        replace(graph, routes=(("foreign", *graph.routes[0][1:]), *graph.routes[1:])),
        replace(graph, routes=graph.routes[:-1]),
        replace(graph, routes=(*graph.routes, graph.routes[0])),
        replace(graph, broker_capabilities=("foreign",)),
        replace(graph, broker_capabilities=graph.broker_capabilities[:-1]),
        replace(graph, broker_capabilities=(*graph.broker_capabilities, "foreign")),
        replace(graph, application_loop_id="foreign"),
        replace(graph, tenant_id="foreign"),
        replace(graph, generation_id="foreign"),
        replace(graph, broker_epoch=2),
        replace(graph, owners=(replace(first, process_identity="٠"), *graph.owners[1:])),
        replace(graph, owners=(replace(first, process_identity="0"), *graph.owners[1:])),
    )
    for mutant in mutants:
        with pytest.raises(RuntimeError, match="realized runtime graph differs"):
            _verify_realized_graph_exact(R7_PRODUCTION_MANIFEST, mutant, generation, "tenant-1", 1)


def _process_exists(process_id: int) -> bool:
    try:
        os.kill(process_id, 0)
    except ProcessLookupError:
        return False
    return True
