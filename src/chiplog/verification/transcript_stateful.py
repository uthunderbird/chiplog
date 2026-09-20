"""Compile stateful authored documents without fabricating runtime observations."""

from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Any

from .identity import canonical_json_bytes, sha256_bytes
from .transcript_schema import (
    StatefulSchemaError,
    closed,
    mapping,
    sequence,
    string,
    strings,
    validate_expect,
    validate_forbid,
    validate_scenario,
    validate_step,
)
from .transcript_yaml import parse_contract_yaml

REFERENCE = re.compile(r"^\$([a-zA-Z_][\w-]*)(?:\.(.+))?$")


def _walk(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for item in value.values() for text in _walk(item)]
    if isinstance(value, list):
        return [text for item in value for text in _walk(item)]
    return []


def _merge(base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in extra.items():
        if key not in result:
            result[key] = value
        elif isinstance(value, list) and isinstance(result[key], list):
            result[key] += [item for item in value if item not in result[key]]
        elif isinstance(value, dict) and isinstance(result[key], dict):
            result[key] = _merge(result[key], value)
        elif result[key] != value:
            raise StatefulSchemaError(f"conflicting imported field: {key}")
    return result


class StatefulCompiler:
    def __init__(self, source: Path) -> None:
        self.source = source.resolve()
        self.root = next(
            (p for p in self.source.parents if p.name == "transcripts"), self.source.parent
        )
        self.documents: dict[Path, dict[str, Any]] = {}
        self.dependencies: dict[str, str] = {}

    def _path(self, source: Path, relative: str) -> Path:
        path = (source.parent / relative).resolve()
        if not path.is_relative_to(self.root) or path.suffix != ".md":
            raise StatefulSchemaError(f"dependency escapes transcript root: {relative}")
        if not path.is_file():
            raise StatefulSchemaError(f"missing transcript dependency: {relative}")
        return path

    def _document(self, path: Path) -> dict[str, Any]:
        if path in self.documents:
            return self.documents[path]
        # Shared Markdown extraction keeps one interpretation of fences/messages.
        from .transcripts import (
            _extract_contract_comments,
            _extract_contract_fences,
            _extract_messages,
        )

        text = path.read_text(encoding="utf-8")
        if _extract_contract_comments(text):
            raise StatefulSchemaError("stateful dependencies cannot mix hidden contracts")
        blocks = [parse_contract_yaml(body) for body in _extract_contract_fences(text)]
        scenarios = [b["scenario"] for b in blocks if "scenario" in b]
        if len(scenarios) != 1:
            raise StatefulSchemaError("one scenario block is required")
        scenario = validate_scenario(scenarios[0])
        steps: list[dict[str, Any]] = []
        expectations: dict[str, dict[str, Any]] = {}
        forbids: list[dict[str, Any]] = []
        for block in blocks:
            name, body = next(iter(block.items()))
            if name == "scenario":
                continue
            if name == "step":
                steps.append(validate_step(body))
            elif name == "expect":
                item = validate_expect(body)
                target = expectations.setdefault(item["step"], {"step": item["step"]})
                repeated = (set(target) & set(item)) - {"step"}
                if repeated:
                    raise StatefulSchemaError(f"repeated expect fields: {sorted(repeated)}")
                target.update(item)
            elif name == "forbid":
                forbids.append(validate_forbid(body))
            elif name == "design":
                closed(
                    body,
                    {"step", "tool_call", "tool_result", "model_result", "rationale"},
                    set(),
                    "design",
                )
            else:
                raise StatefulSchemaError(f"unknown discriminator: {name}")
        ids = [s["id"] for s in steps]
        if len(ids) != len(set(ids)):
            raise StatefulSchemaError("duplicate step id")
        if set(expectations) != set(ids):
            raise StatefulSchemaError(
                "every step must have expectations and no unknown expect.step"
            )
        if scenario.get("kind") == "library":
            if steps or scenario.get("variants"):
                raise StatefulSchemaError("library cannot define executable steps/variants")
        elif not steps or not scenario.get("variants"):
            raise StatefulSchemaError("stateful scenario requires steps and variants")
        used = {
            sid for variant in scenario.get("variants", {}).values() for sid in variant["steps"]
        }
        if used != set(ids):
            raise StatefulSchemaError("variant steps must cover exactly declared steps")
        messages = _extract_messages(text)
        document = {
            "scenario": scenario,
            "steps": steps,
            "expect": expectations,
            "forbid": forbids,
            "messages": messages,
        }
        self.documents[path] = document
        semantic = {**document, "messages": [m for m in messages if m["mode"] != "пример"]}
        self.dependencies[path.relative_to(self.root).as_posix()] = sha256_bytes(
            canonical_json_bytes(semantic)
        )
        return document

    def _expand(
        self, value: Any, source: Path, parameters: dict[str, Any], stack: tuple[str, ...] = ()
    ) -> Any:
        if isinstance(value, str) and value.startswith("$"):
            match = REFERENCE.fullmatch(value)
            if match and match[1] in parameters and match[2] is None:
                return copy.deepcopy(parameters[match[1]])
            return value
        if isinstance(value, list):
            return [self._expand(item, source, parameters, stack) for item in value]
        if not isinstance(value, dict):
            return value
        local = {
            k: self._expand(v, source, parameters, stack) for k, v in value.items() if k != "use"
        }
        if "use" not in value:
            return local
        reference = self._expand(value["use"], source, parameters, stack)
        if not isinstance(reference, str) or "#/" not in reference:
            raise StatefulSchemaError(f"invalid import reference: {reference!r}")
        relative, pointer = reference.split("#", 1)
        target = self._path(source, relative)
        identity = str(target) + "#" + pointer
        if identity in stack:
            raise StatefulSchemaError("cyclic transcript import")
        selected: Any = self._document(target)
        for part in pointer[1:].split("/"):
            if re.search(r"~(?![01])", part):
                raise StatefulSchemaError("invalid JSON pointer escape")
            part = part.replace("~1", "/").replace("~0", "~")
            if isinstance(selected, dict) and part in selected:
                selected = selected[part]
            elif isinstance(selected, list) and part.isdecimal() and int(part) < len(selected):
                selected = selected[int(part)]
            else:
                raise StatefulSchemaError(f"unresolved import pointer: {reference}")
        # Root forbid is stored as authored blocks; a library's single block is its contract.
        if pointer == "/forbid":
            selected = self._combined_forbid(selected, target, parameters, (*stack, identity))
        else:
            selected = self._expand(selected, target, parameters, (*stack, identity))
        if local:
            return _merge(mapping(selected, "imported value with additions"), local)
        return selected

    def _combined_forbid(
        self,
        blocks: list[dict[str, Any]],
        source: Path,
        parameters: dict[str, Any],
        stack: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for item in blocks:
            result = _merge(result, self._expand(item, source, parameters, stack))
        validate_forbid(result)
        return result

    def _variant(
        self, source: Path, name: str, stack: tuple[tuple[Path, str], ...] = ()
    ) -> dict[str, Any]:
        identity = (source, name)
        if identity in stack:
            raise StatefulSchemaError("cyclic prefix dependency")
        document = self._document(source)
        scenario = document["scenario"]
        if name not in scenario.get("variants", {}):
            raise StatefulSchemaError(f"unknown prefix variant: {name}")
        variant = scenario["variants"][name]
        parameters = variant.get("parameters", {})
        inherited: dict[str, Any] | None = None
        if "prefix" in variant:
            spec = variant["prefix"]
            parent = self._variant(
                self._path(source, spec["source"]), spec["variant"], (*stack, identity)
            )
            until = spec["until"]
            if until not in parent["graph"]:
                raise StatefulSchemaError(f"unreachable prefix cutoff: {until}")
            reached = _ancestors(parent["graph"], until) | {until}
            inherited = {
                "contract": parent,
                "until": until,
                "namespace": spec["capture_namespace"],
                "reached": sorted(reached),
                "captures": {k: v for k, v in parent["captures"].items() if v in reached},
            }
        steps_by_id = {step["id"]: step for step in document["steps"]}
        steps = [self._expand(steps_by_id[sid], source, parameters) for sid in variant["steps"]]
        expectations = [
            self._expand(document["expect"][sid], source, parameters) for sid in variant["steps"]
        ]
        messages = {m["id"]: m for m in document["messages"] if m["mode"] != "пример"}
        if inherited:
            messages = {**inherited["contract"]["messages"], **messages}
        plan = {
            "id": name,
            "scenario_id": scenario["id"],
            "parameters": parameters,
            "arrange": self._expand(scenario.get("arrange", {}), source, parameters),
            "initial_context": self._expand(
                scenario.get("initial_context", {}), source, parameters
            ),
            "policy": self._expand(scenario.get("policy", {}), source, parameters),
            "steps": steps,
            "expect": expectations,
            "messages": messages,
            "forbid": self._combined_forbid(document["forbid"], source, parameters),
            "faults": self._expand(scenario.get("faults", []), source, parameters),
            "requirements": self._expand(scenario.get("requirements", {}), source, parameters),
            "requires": variant.get("requires", []),
            "evaluation": scenario.get("evaluation", {}),
            "baselines": scenario.get("baselines", {}),
            "allowed_outcome": variant["allowed_outcome"],
            "prefix": inherited,
        }
        if set(plan["forbid"].get("steps", {})) - set(steps_by_id):
            raise StatefulSchemaError("forbid scope references unknown step")
        fault_ids = {
            step["input"][key]
            for step in steps
            for key in ("fault", "fault_before_adoption")
            if key in step["input"]
        }
        declared_faults = {fault["id"]: fault for fault in plan["faults"]}
        if len(declared_faults) != len(plan["faults"]) or fault_ids - set(declared_faults):
            raise StatefulSchemaError("duplicate or unregistered input fault")
        plan["faults"] = [fault for fault in plan["faults"] if fault["id"] in fault_ids]
        fixtures = []
        for fixture in sequence(scenario.get("fixtures", []), "fixtures"):
            resolved = self._expand(fixture, source, parameters)
            closed(
                resolved,
                {"id", "boundary", "match", "result", "precondition", "steps"},
                {"boundary", "match", "result"},
                "fixture",
            )
            string(resolved["boundary"], "fixture.boundary")
            mapping(resolved["match"], "fixture.match")
            mapping(resolved["result"], "fixture.result")
            if "steps" in resolved:
                strings(resolved["steps"], "fixture.steps")
            if "steps" in resolved and not set(resolved["steps"]) & set(variant["steps"]):
                continue
            fixture_id = resolved.get("id", self._expand(fixture.get("use"), source, parameters))
            if not isinstance(fixture_id, str):
                raise StatefulSchemaError("fixture requires id or import identity")
            fixtures.append({"id": fixture_id, "definition": resolved})
        if len({f["id"] for f in fixtures}) != len(fixtures):
            raise StatefulSchemaError("duplicate fixture identity")
        plan["fixtures"] = fixtures
        for stimulus in (step["input"] for step in steps):
            if "message_ref" in stimulus and stimulus["message_ref"] not in messages:
                raise StatefulSchemaError(f"dangling input message_ref: {stimulus['message_ref']}")
        graph, captures = _graph(plan)
        plan.update(graph=graph, captures=captures)
        _validate_references(plan)
        return plan

    def compile(self) -> dict[str, Any]:
        document = self._document(self.source)
        scenario = document["scenario"]
        plans = [self._variant(self.source, name) for name in scenario.get("variants", {})]
        # Libraries compile their structured content, but never become fake runs.
        library = (
            self._expand(document, self.source, {}) if scenario.get("kind") == "library" else None
        )
        bindings: dict[str, Any] = {}
        observations: dict[str, Any] = {}
        for dependency in self.documents.values():
            bindings = _merge(bindings, dependency["scenario"].get("bindings", {}))
            observations = _merge(observations, dependency["scenario"].get("observations", {}))
        for plan in plans:
            for expectation in plan["expect"]:
                unknown = set(expectation.get("state", {})) - set(observations)
                if unknown:
                    raise StatefulSchemaError(f"unknown observation paths: {sorted(unknown)}")
        return {
            "schema_version": 3,
            "profile": "stateful-v2",
            "scenario": scenario,
            "variants": plans,
            "library": library,
            "bindings": bindings,
            "observations": observations,
            "dependencies": [
                {"path": p, "contract_digest": d} for p, d in sorted(self.dependencies.items())
            ],
        }


def _ancestors(
    graph: dict[str, list[str]], node: str, active: frozenset[str] = frozenset()
) -> set[str]:
    if node in active:
        raise StatefulSchemaError(f"cyclic event dependency at {node}")
    if node not in graph:
        raise StatefulSchemaError(f"unknown event reference: {node}")
    result: set[str] = set(graph[node])
    for parent in graph[node]:
        result |= _ancestors(graph, parent, active | {node})
    return result


def _graph(plan: dict[str, Any]) -> tuple[dict[str, list[str]], dict[str, str]]:
    graph: dict[str, list[str]] = {}
    captured: dict[str, str] = {}
    inherited = plan["prefix"]
    if inherited:
        ns = inherited["namespace"]
        graph.update(
            {
                ns + "." + name: [
                    ns + "." + parent for parent in inherited["contract"]["graph"][name]
                ]
                for name in inherited["reached"]
            }
        )
        captured.update(
            {ns + "." + key: ns + "." + origin for key, origin in inherited["captures"].items()}
        )

    def node(name: str, predecessors: list[str], capture: dict[str, Any] | None = None) -> None:
        if name in graph:
            raise StatefulSchemaError(f"duplicate event/call identity: {name}")
        graph[name] = list(dict.fromkeys(predecessors))
        for key in capture or {}:
            if key in captured:
                raise StatefulSchemaError(f"duplicate capture: {key}")
            captured[key] = name

    previous: str | None = inherited["namespace"] + "." + inherited["until"] if inherited else None
    deferred: list[tuple[str, str]] = []
    for step, expect in zip(plan["steps"], plan["expect"], strict=True):
        start = step["id"] + ".input"
        predecessors = [previous] if previous else []
        if "after" in step["input"]:
            predecessors.append(step["input"]["after"])
        node(start, predecessors, step["input"].get("capture"))
        call_start = start
        if step["input"]["kind"] == "authenticated_adoption":
            call_start = step["id"] + ".adoption"
            node(call_start, [start])
        for call in expect.get("calls", []):
            name = call["id"]
            node(name + ".call", [call_start, *call.get("after", [])])
            node(name + ".result", [name + ".call"], call.get("result", {}).get("capture"))
            deferred.extend((target, name + ".result") for target in call.get("before", []))
        for event in expect.get("events", []):
            parents = [start, *event.get("after", [])]
            if "same_call_as" in event:
                parents.append(event["same_call_as"] + ".result")
            node(event["id"], parents, event.get("capture"))
            deferred.extend((target, event["id"]) for target in event.get("before", []))
        if step["end"] not in graph:
            raise StatefulSchemaError(f"step end has no observation: {step['end']}")
        previous = step["end"]
    for fault in plan["faults"]:
        witnesses = [
            event["id"]
            for expected in plan["expect"]
            for event in expected.get("events", [])
            if event["operation"] == "fault.reached"
            and event.get("fields", {}).get("fault") == fault["id"]
        ]
        if len(witnesses) != 1:
            raise StatefulSchemaError(f"fault requires one reachability observation: {fault['id']}")
        if "after" in fault:
            graph[witnesses[0]].append(fault["after"])
        if "before" in fault:
            deferred.append((fault["before"], witnesses[0]))
    for target, predecessor in deferred:
        if target not in graph:
            raise StatefulSchemaError(f"unknown before event: {target}")
        graph[target].append(predecessor)
    for name in graph:
        _ancestors(graph, name)
    return graph, captured


def _validate_references(plan: dict[str, Any]) -> None:
    graph = plan["graph"]
    captured = plan["captures"]
    params = plan["parameters"]

    def check(value: Any, at: str | None) -> None:
        for text in _walk(value):
            ref = REFERENCE.fullmatch(text)
            if ref is None:
                continue
            key = ref[1]
            if key in params:
                continue
            if key == "message":
                if ref[2] not in plan["messages"]:
                    raise StatefulSchemaError(f"unknown message reference: {text}")
                continue
            if plan["prefix"] and key == plan["prefix"]["namespace"]:
                key += "." + (ref[2] or "").split(".")[0]
            if key not in captured:
                raise StatefulSchemaError(f"unknown capture reference: {text}")
            if at and captured[key] not in _ancestors(graph, at) | {at}:
                raise StatefulSchemaError(f"forward capture reference: {text} at {at}")

    for step, expect in zip(plan["steps"], plan["expect"], strict=True):
        check(step["input"], step["id"] + ".input")
        for call in expect.get("calls", []):
            check(call.get("arguments", call.get("envelope", {})), call["id"] + ".call")
            check(call.get("result", {}), call["id"] + ".result")
            fixture = call.get("result", {}).get("fixture")
            if fixture and fixture not in {f["id"] for f in plan["fixtures"]}:
                raise StatefulSchemaError(f"unregistered fixture reference: {fixture}")
        for event in expect.get("events", []):
            check(event.get("fields", {}), event["id"])
        for item in [*expect.get("calls", []), *expect.get("events", [])]:
            if any(name not in graph for name in item.get("concurrent_with", [])):
                raise StatefulSchemaError("unknown concurrent event")
        for update in expect.get("context", {}).get("updates", []):
            if update["after_event"] not in _ancestors(graph, step["end"]) | {step["end"]}:
                raise StatefulSchemaError("context update requires a reached predecessor")
        for name in ("state", "context", "relations"):
            check(expect.get(name, {}), step["end"])
    # Deferred payloads are validated for declared references; runtime is responsible
    # for checking that their captures exist when the actual boundary is reached.
    check(plan["fixtures"], None)
    check(plan["faults"], None)
    check(
        {k: v for k, v in plan.items() if k not in {"prefix", "graph", "captures", "messages"}},
        None,
    )
    for setup in ("arrange", "initial_context"):
        check(plan[setup], plan["steps"][0]["id"] + ".input")
