"""Bad source inputs reach the independent canonical dependency verifier."""

from dataclasses import replace
from pathlib import Path

import pytest

from chiplog.verification.scheduler_schema import (
    SchemaAuditError,
    derive_schema_manifest,
    require_schema_manifest,
)


def sources() -> tuple[bytes, bytes]:
    owner = Path(__file__).resolve().parents[2] / "src/chiplog/capabilities/agent_loop"
    return (
        (owner / "scheduler_materialization.py").read_bytes(),
        (owner / "scheduler_rollover.py").read_bytes(),
    )


def changed(source: bytes, old: bytes, new: bytes) -> bytes:
    assert source.count(old) == 1
    return source.replace(old, new)


def test_actual_sources_generate_field_sensitive_dependencies_without_declared_graph() -> None:
    materialization, rollover = sources()
    manifest = derive_schema_manifest(materialization, rollover)
    require_schema_manifest(manifest, materialization, rollover)
    graph = dict(manifest.dependencies)
    assert graph["materialization:_hash:execution-root-lease-genesis-v1"] == (
        "primitive:ScheduledBatchPrimitiveDomainV1",
    )
    assert (
        "materialization:_hash:scheduled-run-v1"
        in graph["primitive:ScheduledBatchPrimitiveDomainV1"]
    )
    assert (
        "materialization:_hash:execution-root-lease-genesis-v1"
        not in graph["primitive:ScheduledBatchPrimitiveDomainV1"]
    )
    assert (
        "materialization:_member:interval-result"
        not in graph["materialization:_hash:scheduler-interval-envelope-v1"]
    )
    assert "rollover:_record:rollover-decision" in graph["rollover:_record:rollover-edge"]
    assert graph["primitive:Rollover"] == ()
    # Handwritten registration data is not the generator's dependency oracle.
    altered = changed(
        materialization,
        b'("parent_primitive", ()),',
        b'("parent_primitive", ("invented-hand-edge",)),',
    )
    assert derive_schema_manifest(altered, rollover).dependencies == manifest.dependencies
    with pytest.raises(SchemaAuditError, match="actual canonical source"):
        require_schema_manifest(manifest, altered, rollover)


@pytest.mark.parametrize(
    "old,new",
    [
        (b'"physical-root-head-v1", epoch_body', b'"unregistered-epoch-domain", epoch_body'),
        (
            b'_hash("physical-root-selector-v1", root_id)',
            b'_hash("physical-root-selector-v1", "constant")',
        ),
    ],
)
def test_added_aliased_or_removed_preimage_edges_change_actual_generated_graph(
    old: bytes, new: bytes
) -> None:
    materialization, rollover = sources()
    original = derive_schema_manifest(materialization, rollover)
    altered = changed(materialization, old, new)
    regenerated = derive_schema_manifest(altered, rollover)
    assert regenerated.dependencies != original.dependencies
    # Keeping registered source fingerprints cannot conceal a changed graph.
    forged = replace(regenerated, source_fingerprints=original.source_fingerprints)
    with pytest.raises(SchemaAuditError):
        require_schema_manifest(forged, materialization, rollover)


@pytest.mark.parametrize(
    "injection",
    [
        b'    primitive = primitive.model_copy(update={"root_id": genesis.lease_head})\n',
        b'    primitive = primitive.model_copy(update={"root_id": '
        b'"a" if genesis.lease_head else "b"})\n',
        b'    if genesis.lease_head:\n        selected = "a"\n'
        b'    else:\n        selected = "b"\n'
        b'    primitive = primitive.model_copy(update={"root_id": selected})\n',
        b'    primitive = primitive.model_copy(update={"root_id": '
        b'str(tuple("x" for item in genesis.lease_head))})\n',
        b'    primitive = primitive.model_copy(update={"root_id": '
        b'str(tuple("x" for outer in ("a",) for item in genesis.lease_head))})\n',
        b'    primitive = primitive.model_copy(update={"root_id": '
        b'str(tuple("x" for item in genesis.lease_head if item))})\n',
    ],
)
def test_derived_lease_preimage_cannot_reenter_primitive_through_copy_or_branch(
    injection: bytes,
) -> None:
    materialization, rollover = sources()
    marker = b'    epoch_id = "physical-root-genesis-v1:"'
    altered = changed(
        materialization,
        marker,
        injection + b'    primitive_ref = _reference("scheduled-batch-primitive-v1", '
        b'primitive.model_dump(mode="json"))\n' + marker,
    )
    with pytest.raises(SchemaAuditError, match=r"forbidden|cyclic"):
        derive_schema_manifest(altered, rollover)


@pytest.mark.parametrize(
    "injection",
    [
        b'    alias = root_fields\n    alias["hidden"] = lineage_head\n',
        b'    root_fields.update({"hidden": lineage_head})\n',
        b"    unknown_callback(root_fields)\n",
        b"    _hash = external_hash\n",
        b"    while root_fields:\n        root_fields = {}\n",
    ],
)
def test_unsupported_mutation_alias_callback_or_control_flow_fails_closed(injection: bytes) -> None:
    materialization, rollover = sources()
    marker = b"    lineage = ExecutionLineageBinding("
    with pytest.raises(SchemaAuditError):
        derive_schema_manifest(changed(materialization, marker, injection + marker), rollover)


def test_unknown_dynamic_domain_and_hidden_codec_transform_fail_closed() -> None:
    materialization, rollover = sources()
    dynamic = changed(
        materialization, b'_hash("physical-root-selector-v1", root_id)', b"_hash(root_id, root_id)"
    )
    hidden = changed(
        materialization,
        b"return hashlib.sha256(_bytes([domain, value])).hexdigest()",
        b'return hashlib.sha256(_bytes([domain, value, "hidden"])).hexdigest()',
    )
    for altered in (dynamic, hidden):
        with pytest.raises(SchemaAuditError):
            derive_schema_manifest(altered, rollover)


def test_source_domain_alias_cycle_and_import_substitution_reject() -> None:
    materialization, rollover = sources()
    cycle = changed(
        materialization,
        b'_hash("execution-lineage-v1", root_fields)',
        b'_hash("scheduled-run-v1", root_fields)',
    )
    wrong_import = changed(rollover, b"import hashlib", b"import hashlib as base64")
    with pytest.raises(SchemaAuditError, match="cyclic"):
        derive_schema_manifest(cycle, rollover)
    with pytest.raises(SchemaAuditError, match="import"):
        derive_schema_manifest(materialization, wrong_import)


def test_local_helper_summary_preserves_real_preimage_dependency() -> None:
    materialization, rollover = sources()
    original = dict(derive_schema_manifest(materialization, rollover).dependencies)
    altered = changed(
        materialization,
        b'_hash("physical-root-selector-v1", root_id)',
        b'_hash("physical-root-selector-v1", _bridge(root_id))',
    )
    altered += b"\n\ndef _bridge(value):\n    return value\n"
    graph = dict(derive_schema_manifest(altered, rollover).dependencies)
    key = "materialization:_hash:physical-root-selector-v1"
    assert graph[key] == original[key]
    altered = changed(
        altered, b"    return value\n", b'    return _hash("hidden-helper-domain", value)\n'
    )
    graph = dict(derive_schema_manifest(altered, rollover).dependencies)
    assert graph[key] == ("materialization:_hash:hidden-helper-domain",)


@pytest.mark.parametrize(
    "suffix",
    [
        b'\nglobals().update({"_hash": lambda *args: "f" * 64})\n',
        b"\n@replace_builder\ndef unused():\n    return None\n",
        b"\ndef unused(value=unknown_callback()):\n    return value\n",
        b"\nclass Hidden(RecoveryDTO):\n    unknown_callback()\n",
    ],
)
def test_module_class_and_default_execution_cannot_hide_canonical_edges(suffix: bytes) -> None:
    materialization, rollover = sources()
    with pytest.raises(SchemaAuditError):
        derive_schema_manifest(materialization + suffix, rollover)
