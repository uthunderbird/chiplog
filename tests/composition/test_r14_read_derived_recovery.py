"""One held read reuses pure derivation, while authenticating each source horizon."""

import hashlib
import json
import sqlite3
from dataclasses import replace

import pytest

from chiplog.architecture.r7_runtime import R14_EXECUTION_NEXT_TURN_V27_PRODUCTION_MANIFEST
from chiplog.composition import r14_native_recovery_historical as historical
from chiplog.composition import r14_recovery_publication as publication
from chiplog.composition.common_cli_execution_runtime import open_common_cli_execution_runtime
from chiplog.composition.r14_loop_history import _strict_json
from chiplog.composition.r14_native_recovery_cut import NativeRecoveryCutSource
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources
from tests.support.native_cancelled import native_response, selected_native_cancelled


@pytest.mark.asyncio
async def test_same_read_derives_once_but_authenticates_both_actual_horizons(tmp_path, monkeypatch):
    resources = HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "custody"
    )
    async with open_common_cli_execution_runtime(
        tmp_path / "read-derived.sqlite",
        resources=resources,
        responses=(native_response(),),
        manifest=R14_EXECUTION_NEXT_TURN_V27_PRODUCTION_MANIFEST,
        recovery_cut_source=NativeRecoveryCutSource(),
    ) as runtime:
        await runtime.publish_native_recovery_profile()
        await selected_native_cancelled(runtime, bound=True)
        await runtime.publish_native_one_call_cancelled_accounting()
        await runtime.publish_execution_accounting_readiness()
        with runtime._authority_gate().hold():
            rows = []
            for identity, predecessor, raw in runtime._loop_decisions().entries():
                entry = _strict_json(raw)
                command = (
                    runtime._publication(entry)
                    if entry.get("kind") == "DECIDED"
                    and str(entry.get("operation_kind", "")).startswith("agent_loop")
                    else None
                )
                rows.append((identity, predecessor, raw, entry, command))
            cut = historical._make_authenticated_loop_decision_cut(runtime, rows)
            ((_, _, _, entry, physical),) = (
                row
                for row in rows
                if row[3].get("kind") == "DECIDED"
                and row[3].get("operation_kind") == publication.OPERATION
            )
            derived = []
            derive_sites = []
            cache_reads = []
            horizons = []
            original_command = publication.command
            original_authenticate = historical._authenticate_native_recovery_selected
            original_read_derived = historical._read_derived_recovery

            def counted(evidence):
                derived.append(evidence)
                frame = __import__("sys")._getframe(1)
                derive_sites.append((frame.f_code.co_name, frame.f_lineno))
                return original_command(evidence)

            def authenticate(*args, **kwargs):
                horizons.append(kwargs.get("through_sequence"))
                return original_authenticate(*args, **kwargs)

            def observe_read(runtime, selected_entry, selected_cut):
                row = historical._recovery_derivation_row(runtime, selected_entry, selected_cut)
                key = row.decision_id, row.raw
                cache_reads.append(
                    (
                        id(selected_cut),
                        key in selected_cut._recovery_derivations,
                        len(selected_cut._recovery_derivations),
                        hashlib.sha256(row.raw).hexdigest()[:12],
                    )
                )
                return original_read_derived(runtime, selected_entry, selected_cut)

            monkeypatch.setattr(publication, "command", counted)
            monkeypatch.setattr(historical, "_authenticate_native_recovery_selected", authenticate)
            monkeypatch.setattr(historical, "_read_derived_recovery", observe_read)
            first = publication.validate_selected(
                runtime, entry, physical, authenticated_loop_decisions=cut
            )
            bounded = physical.expected_head + 1
            second = publication.validate_selected(
                runtime,
                entry,
                physical,
                through_sequence=bounded,
                authenticated_loop_decisions=cut,
            )
            assert horizons == [None, bounded]
            assert len(derived) == 1, (
                "one read must derive identical retained bytes only once; "
                f"call_sites={derive_sites}, cache_reads={cache_reads}"
            )
            assert first is second
            assert original_command(first) == physical
            assert resources.require_original_provider().transfers == ()
            assert historical._read_cached_recovery_command(runtime, entry, first, cut) == physical
            assert (
                historical._read_cached_recovery_command(runtime, entry, first.model_copy(), cut)
                is None
            )
            with pytest.raises(ValueError):
                publication.validate_selected(
                    runtime,
                    entry,
                    replace(physical, request_fingerprint="0" * 64),
                    authenticated_loop_decisions=cut,
                )
            assert len(derived) == 1
            assert first.actor_budget_v1 is not None
            damaged_values = (
                first.model_copy(
                    update={
                        "accounting_result": first.accounting_result.model_copy(
                            update={"source_request_fingerprint": "0" * 64}
                        )
                    }
                ),
                first.model_copy(
                    update={
                        "accounting_reply": first.accounting_reply.model_copy(
                            update={"canonical_payload": b"{}"}
                        )
                    }
                ),
                first.model_copy(
                    update={
                        "actor_budget_v1": first.actor_budget_v1.model_copy(
                            update={
                                "issued_monotonic_ns": first.actor_budget_v1.issued_monotonic_ns + 1
                            }
                        )
                    }
                ),
            )
            for damaged in damaged_values:
                bad_entry = dict(
                    entry, execution_recovery_evidence=damaged.canonical_bytes().decode()
                )
                with pytest.raises(ValueError):
                    publication.validate_selected(
                        runtime, bad_entry, physical, authenticated_loop_decisions=cut
                    )
            assert len(derived) == 1
            foreign = historical._make_authenticated_loop_decision_cut(object(), rows)
            with pytest.raises(ValueError):
                publication.validate_selected(
                    runtime, entry, physical, authenticated_loop_decisions=foreign
                )
            assert len(derived) == 1
            # A factory-issued cut still carries decoded physical data, never a
            # substitute for the command regenerated from retained owner replies.
            poisoned_physical = replace(physical, request_fingerprint="f" * 64)
            poisoned_rows = [
                (*row[:4], poisoned_physical) if row[3] is entry else row for row in rows
            ]
            poisoned_cut = historical._make_authenticated_loop_decision_cut(runtime, poisoned_rows)
            with pytest.raises(ValueError, match="unauthenticated"):
                publication.validate_selected(
                    runtime, entry, poisoned_physical, authenticated_loop_decisions=poisoned_cut
                )
            assert len(derived) == 2
            _, regenerated = historical._read_derived_recovery(runtime, entry, poisoned_cut)
            assert regenerated == physical and regenerated != poisoned_physical
            denied = []

            class DenyingSource(NativeRecoveryCutSource):
                def authenticate_selected_held(self, runtime, evidence, selected_entry, **kwargs):
                    denied.append((evidence, selected_entry))
                    return False

            with monkeypatch.context() as scoped:
                scoped.setattr(runtime, "_full_recovery_cut_source", DenyingSource())
                with pytest.raises(ValueError, match="unauthenticated"):
                    publication.validate_selected(
                        runtime, entry, physical, authenticated_loop_decisions=cut
                    )
            assert denied == [(first, entry)]
            assert len(derived) == 3  # Custom source kept its original uncached path.
            # Supported subclasses receive the authenticated cut and may
            # inspect it, but cannot replace evidence used by native validation.
            callback_observations = []
            native_source = runtime._full_recovery_cut_source

            class CachePoisoningSource(NativeRecoveryCutSource):
                def authenticate_selected_held(self, runtime, evidence, selected_entry, **kwargs):
                    received_cut = kwargs["authenticated_loop_decisions"]
                    damaged = evidence.model_copy(
                        update={
                            "accounting_reply": evidence.accounting_reply.model_copy(
                                update={"canonical_payload": b"{}"}
                            )
                        }
                    )
                    row = historical._recovery_derivation_row(runtime, selected_entry, received_cut)
                    try:
                        received_cut._recovery_derivations[(row.decision_id, row.raw)] = (
                            damaged,
                            physical,
                        )
                        callback_observations.append("cache-mutated")
                    except TypeError:
                        callback_observations.append("cache-read-only")
                    runtime._full_recovery_cut_source = native_source
                    return True

            runtime._full_recovery_cut_source = CachePoisoningSource()
            callback_evidence = publication.validate_selected(
                runtime, entry, physical, authenticated_loop_decisions=cut
            )
            assert callback_evidence.accounting_reply.canonical_payload == (
                first.accounting_result.canonical_bytes()
            )
            assert callback_observations == ["cache-read-only"]
            bounded_evidence = publication.validate_selected(
                runtime,
                entry,
                physical,
                through_sequence=bounded,
                authenticated_loop_decisions=cut,
            )
            assert bounded_evidence is first
            assert bounded_evidence.accounting_reply.canonical_payload == (
                first.accounting_result.canonical_bytes()
            )
            assert callback_observations == ["cache-read-only"]
            runtime._full_recovery_cut_source = native_source
            post_poison_derivations = len(derived)
            # Changing a source after successful validation must still fail the
            # bounded authentication, even though pure derivation remains reusable.
            owner = runtime._owner_decisions()._raw
            owner_entries = owner.entries()
            marker = next(
                json.loads(raw)
                for _, _, raw in reversed(owner_entries)
                if json.loads(raw).get("kind") == "MATERIALIZED"
            )
            marker["decision_id"] = "missing-selected-decision"
            owner.append(
                json.dumps(marker, sort_keys=True, separators=(",", ":")).encode(),
                owner_entries[-1][0],
            )
            with pytest.raises(ValueError, match="unauthenticated"):
                publication.validate_selected(
                    runtime,
                    entry,
                    physical,
                    through_sequence=bounded,
                    authenticated_loop_decisions=cut,
                )
            assert horizons[-1] == bounded
            assert len(derived) == post_poison_derivations
            assert runtime._loop_decisions().entries() == tuple(
                (identity, predecessor, raw) for identity, predecessor, raw, _, _ in rows
            )
            loop = runtime._loop_decisions()
            loop_entries = loop.entries()
            loop.append(
                json.dumps(entry, sort_keys=True, separators=(",", ":")).encode(),
                loop_entries[-1][0],
            )
            with pytest.raises(ValueError, match="unchanged authenticated loop cut"):
                publication.validate_selected(
                    runtime, entry, physical, authenticated_loop_decisions=cut
                )
            assert len(derived) == post_poison_derivations
            with sqlite3.connect(runtime._database) as connection:
                assert connection.execute("SELECT head FROM tenant_heads").fetchone() == (
                    physical.expected_head + 1,
                )
            assert resources.require_original_provider().transfers == ()
