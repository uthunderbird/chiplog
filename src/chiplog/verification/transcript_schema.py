"""Closed structural schema for the stateful transcript profile.

Arguments, results and world records are boundary payloads, not an alternative
production schema. They require a registered runtime binding before execution.
"""

from __future__ import annotations

from typing import Any

SCENARIO = set(
    [
        "format_version",
        "protocol_version",
        "id",
        "version",
        "status",
        "kind",
        "arrange",
        "initial_context",
        "policy",
        "fixtures",
        "variants",
        "requirements",
        "evaluation",
        "faults",
        "baselines",
        "bindings",
        "observations",
    ]
)
INPUT = set(
    [
        "kind",
        "message_ref",
        "already_in_initial_context",
        "principal",
        "peer",
        "proposal",
        "display_digest",
        "capture",
        "fault_before_adoption",
        "after",
        "act",
        "bytes",
        "envelope",
        "replace",
        "preserve",
        "fault",
    ]
)
INPUT_KINDS = set(
    [
        "message",
        "authenticated_adoption",
        "authenticated_adoption_replay",
        "owner_command_replay",
        "owner_command_fault",
        "resume",
        "observe",
    ]
)
EXPECT = set(["step", "baseline", "calls", "events", "context", "relations", "response", "state"])
CALL = set(
    [
        "id",
        "boundary",
        "operation",
        "arguments",
        "envelope",
        "count",
        "after",
        "before",
        "concurrent_with",
        "result",
    ]
)
EVENT = set(
    [
        "id",
        "operation",
        "count",
        "after",
        "before",
        "concurrent_with",
        "same_call_as",
        "fields",
        "capture",
    ]
)
RESULT = set(["fixture", "source", "fields", "kind", "capture"])
PREFIX = set(
    [
        "source",
        "variant",
        "until",
        "capture_namespace",
        "inherit_current",
        "inherit_context",
        "fresh_run",
    ]
)
FAULT = set(
    [
        "id",
        "boundary",
        "after",
        "before",
        "dependency_of",
        "change",
        "replace",
        "other_internal_families",
        "derivation_authority",
        "count",
        "require_reached",
    ]
)
PREDICATES = set(
    [
        "external_effect_before_authority",
        "planning_commit_before_exact_adoption",
        "provider_or_real_recipient_exposure",
        "claim_current_calendar_availability_from_stale_observation",
        "fixture_as_evidence_of_internal_commit",
        "inject_authoritative_proposal",
        "inject_acceptance_verdict",
        "hidden_oracle_in_model_context",
        "infer_current_purpose_from_past_journal_or_stale_calendar",
        "treat_scripted_adapter_as_evidence_of_history_understanding",
        "reuse_old_adoption_for_new_display",
        "mutate_existing_adoption",
        "treat_unreached_authority_fault_as_stale_coverage",
        "count_unreached_fault_as_tested",
        "new_command_id_for_replay",
        "overwrite_prior_committed_result",
        "infer_deduplication_from_call_count_only",
        "infer_no_write_from_unchanged_screen",
        "treat_fault_as_authorized_adoption_change",
        "replace_typed_assertion_with_unverified_commentary",
        "rollback_legal_planning_commit_on_completion_rejection",
        "accept_schema_valid_json_without_semantic_check",
        "count_schema_rejection_as_missing_evidence_coverage",
        "accept_mixed_internal_frontier",
        "emit_context_with_wrong_snapshot_revision",
        "broaden_disclosure_without_authority",
        "replace_typed_rejection_with_model_refusal",
    ]
)


class StatefulSchemaError(ValueError):
    pass


def closed(value: Any, allowed: set[str], required: set[str], where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise StatefulSchemaError(f"{where} must be a map")
    if set(value) - allowed:
        raise StatefulSchemaError(f"unknown {where} fields: {sorted(set(value) - allowed)}")
    if required - set(value):
        raise StatefulSchemaError(f"missing {where} fields: {sorted(required - set(value))}")
    return value


def mapping(value: Any, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise StatefulSchemaError(f"{where} must be a map")
    return value


def sequence(value: Any, where: str) -> list[Any]:
    if not isinstance(value, list):
        raise StatefulSchemaError(f"{where} must be a list")
    return value


def string(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value:
        raise StatefulSchemaError(f"{where} must be nonempty text")
    return value


def strings(value: Any, where: str) -> list[str]:
    return [string(item, where) for item in sequence(value, where)]


def captures(value: Any) -> None:
    for key, path in mapping(value, "capture").items():
        if not key.isidentifier():
            raise StatefulSchemaError(f"invalid capture name: {key}")
        string(path, "capture path")


def count(value: Any) -> None:
    if type(value) is int and value >= 0:
        return
    bounds = closed(value, {"min", "max"}, {"min", "max"}, "count")
    if not all(type(v) is int for v in bounds.values()) or not 0 <= bounds["min"] <= bounds["max"]:
        raise StatefulSchemaError("invalid count bounds")


def validate_scenario(value: Any) -> dict[str, Any]:
    obj = closed(
        value,
        SCENARIO,
        {"format_version", "protocol_version", "id", "version", "status"},
        "scenario",
    )
    if (
        type(obj["format_version"]) is not int
        or obj["format_version"] != 2
        or obj["protocol_version"] != 2
    ):
        raise StatefulSchemaError("unsupported format/protocol version")
    string(obj["id"], "scenario.id")
    if type(obj["version"]) is not int or obj["version"] < 1:
        raise StatefulSchemaError("scenario.version must be a positive integer")
    if obj["status"] not in ("design", "executable") or obj.get("kind") not in (None, "library"):
        raise StatefulSchemaError("invalid scenario status/kind")
    for name in (
        "arrange",
        "initial_context",
        "policy",
        "requirements",
        "baselines",
        "bindings",
        "observations",
    ):
        if name in obj:
            mapping(obj[name], name)
    for binding in obj.get("bindings", {}).values():
        closed(
            binding,
            {"kind", "input", "result", "captures", "production_schema"},
            {"kind", "production_schema"},
            "binding",
        )
        string(binding["kind"], "binding.kind")
        if binding["production_schema"] is not None:
            string(binding["production_schema"], "binding.production_schema")
        if "captures" in binding:
            strings(binding["captures"], "binding.captures")
    for observation in obj.get("observations", {}).values():
        string(observation, "observation description")
    for variant in mapping(obj.get("variants", {}), "variants").values():
        closed(
            variant,
            {"parameters", "steps", "allowed_outcome", "prefix", "requires"},
            {"steps", "allowed_outcome"},
            "variant",
        )
        ids = strings(variant["steps"], "variant.steps")
        if not ids or len(ids) != len(set(ids)):
            raise StatefulSchemaError("variant steps must be nonempty and unique")
        mapping(variant.get("parameters", {}), "parameters")
        string(variant["allowed_outcome"], "allowed_outcome")
        strings(variant.get("requires", []), "requires")
        if "prefix" in variant:
            prefix = closed(variant["prefix"], PREFIX, PREFIX, "prefix")
            for field in ("source", "variant", "until", "capture_namespace"):
                string(prefix[field], "prefix." + field)
            if not all(
                prefix[field] is True
                for field in ("fresh_run", "inherit_current", "inherit_context")
            ):
                raise StatefulSchemaError(
                    "prefix must execute fresh and inherit actual state/context"
                )
    for fault in sequence(obj.get("faults", []), "faults"):
        closed(fault, FAULT, {"id", "boundary", "count", "require_reached"}, "fault")
        string(fault["id"], "fault.id")
        string(fault["boundary"], "fault.boundary")
        for field in ("after", "before", "dependency_of"):
            if field in fault:
                string(fault[field], "fault." + field)
        if (
            type(fault["count"]) is not int
            or fault["count"] != 1
            or fault["require_reached"] is not True
        ):
            raise StatefulSchemaError("fault must require exactly one reached injection")
    return obj


def validate_step(value: Any) -> dict[str, Any]:
    obj = closed(value, {"id", "input", "end"}, {"id", "input", "end"}, "step")
    string(obj["id"], "step.id")
    string(obj["end"], "step.end")
    stimulus = closed(obj["input"], INPUT, {"kind"}, "step.input")
    if not isinstance(stimulus["kind"], str) or stimulus["kind"] not in INPUT_KINDS:
        raise StatefulSchemaError("unknown input kind")
    if stimulus["kind"] == "message":
        string(stimulus.get("message_ref"), "input.message_ref")
    if "capture" in stimulus:
        captures(stimulus["capture"])
    for field in ("after", "message_ref", "fault", "fault_before_adoption", "principal", "peer"):
        if field in stimulus:
            string(stimulus[field], "input." + field)
    if (
        "already_in_initial_context" in stimulus
        and type(stimulus["already_in_initial_context"]) is not bool
    ):
        raise StatefulSchemaError("already_in_initial_context requires boolean")
    return obj


def validate_expect(value: Any) -> dict[str, Any]:
    obj = closed(value, EXPECT, {"step"}, "expect")
    string(obj["step"], "expect.step")
    for category, allowed in (("calls", CALL), ("events", EVENT)):
        for item in sequence(obj.get(category, []), category):
            closed(
                item,
                allowed,
                {"id", "operation", "count"} | ({"boundary"} if category == "calls" else set()),
                category,
            )
            string(item["id"], category + ".id")
            string(item["operation"], category + ".operation")
            count(item["count"])
            if category == "calls":
                string(item["boundary"], "call.boundary")
                if "arguments" in item and "envelope" in item:
                    raise StatefulSchemaError("call arguments and envelope are mutually exclusive")
                if "arguments" in item:
                    mapping(item["arguments"], "call.arguments")
                if "envelope" in item:
                    envelope = closed(item["envelope"], {"eq", "eq_input"}, set(), "envelope")
                    if len(envelope) != 1:
                        raise StatefulSchemaError("envelope requires one operator")
                    if "eq_input" in envelope and envelope["eq_input"] is not True:
                        raise StatefulSchemaError("eq_input requires true")
            if "fields" in item:
                mapping(item["fields"], "event.fields")
            if "same_call_as" in item:
                string(item["same_call_as"], "same_call_as")
            for relation in ("before", "after", "concurrent_with"):
                if relation in item:
                    strings(item[relation], relation)
            if "capture" in item:
                captures(item["capture"])
            if "result" in item:
                result = closed(item["result"], RESULT, set(), "call.result")
                if ("fixture" in result) == ("source" in result):
                    raise StatefulSchemaError("result requires exactly one fixture or source")
                if "source" in result and result["source"] != "real_boundary":
                    raise StatefulSchemaError("internal results must come from real_boundary")
                for field in ("fixture", "kind"):
                    if field in result:
                        string(result[field], "result." + field)
                if "fields" in result:
                    mapping(result["fields"], "result.fields")
                if "capture" in result:
                    captures(result["capture"])
    for predicate in mapping(obj.get("state", {}), "state").values():
        closed(predicate, {"eq", "delta", "unchanged"}, set(), "state predicate")
        if len(predicate) != 1:
            raise StatefulSchemaError("state requires exactly one operator")
        if "delta" in predicate and type(predicate["delta"]) is not int:
            raise StatefulSchemaError("delta requires integer")
        if "unchanged" in predicate and type(predicate["unchanged"]) is not bool:
            raise StatefulSchemaError("unchanged requires boolean")
    if "response" in obj:
        response = closed(obj["response"], {"exact", "semantics"}, set(), "response")
        if len(response) != 1:
            raise StatefulSchemaError("response requires exactly one modality")
        if "semantics" in response:
            strings(response["semantics"], "semantics")
    for relation in sequence(obj.get("relations", []), "relations"):
        closed(relation, {"left", "op", "right"}, {"left", "op", "right"}, "relation")
        if relation["op"] not in ("eq", "not_eq", "unchanged"):
            raise StatefulSchemaError("unknown relation operator")
    if "context" in obj:
        context = closed(
            obj["context"],
            set(
                [
                    "updates",
                    "carry",
                    "before_next_model_call",
                    "emission_with_injected_context",
                    "expected_current",
                    "new_render_revision_allowed",
                    "planning_identity",
                ]
            ),
            set(),
            "context",
        )
        for update in sequence(context.get("updates", []), "context.updates"):
            closed(
                update,
                {
                    "screen",
                    "from_revision",
                    "after_event",
                    "from_observed_result",
                    "injected_snapshot",
                },
                {"screen", "from_revision", "after_event"},
                "context update",
            )
            if ("from_observed_result" in update) == ("injected_snapshot" in update):
                raise StatefulSchemaError("context update requires exactly one source")
    return obj


def validate_forbid(value: Any) -> dict[str, Any]:
    obj = closed(value, {"use", "scope", "operations", "predicates", "steps"}, set(), "forbid")
    strings(obj.get("operations", []), "forbid.operations")
    predicates = strings(obj.get("predicates", []), "forbid.predicates")
    if set(predicates) - PREDICATES:
        raise StatefulSchemaError(
            f"unknown forbid predicates: {sorted(set(predicates) - PREDICATES)}"
        )
    for local in mapping(obj.get("steps", {}), "forbid.steps").values():
        validate_forbid(local)
    return obj
