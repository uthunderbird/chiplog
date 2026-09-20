"""Compile and dispatch every transcript profile; missing runtime is NOT_RUNNABLE."""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from chiplog.capabilities.agent_loop.contracts import LoopRejected

from .identity import canonical_json_bytes
from .loop_scenario import run_scenario
from .transcripts import CompiledTranscript, TranscriptCompileError, compile_transcript


def discover_transcripts(source: Path) -> list[Path]:
    if source.is_file():
        return [source]
    if not source.is_dir():
        raise ValueError(f"transcript source does not exist: {source}")
    return [
        path
        for path in sorted(source.rglob("*.md"))
        if re.search(
            r"(?m)^\s*(?:`{3,}|~{3,})[^\n]*\btranscript\b|<!--\s*scenario:",
            path.read_text(),
        )
    ]


def _stateful_requirements(compiled: dict[str, Any], plan: dict[str, Any]) -> list[dict[str, str]]:
    reasons: list[dict[str, str]] = []
    reasons.append(
        {
            "kind": "runner",
            "name": "stateful-v2",
            "reason": "no registered production execution adapter for this profile",
        }
    )
    for name, declaration in compiled["bindings"].items():
        reasons.append(
            {
                "kind": "binding",
                "name": name,
                "reason": "production_schema is unset"
                if declaration.get("production_schema") is None
                else "production schema has no registered execution adapter",
            }
        )
    for expect in plan["expect"]:
        for name in expect.get("state", {}):
            reasons.append(
                {
                    "kind": "observation",
                    "name": name,
                    "reason": "no registered public-state observer",
                }
            )
    for fault in plan["faults"]:
        reasons.append(
            {
                "kind": "fault",
                "name": fault["id"],
                "reason": "no registered injection at " + fault["boundary"],
            }
        )
    for requirement in plan["requires"]:
        reasons.append(
            {
                "kind": "requirement",
                "name": requirement,
                "reason": "capability is not registered in current runtime",
            }
        )
    if plan["prefix"]:
        reasons.extend(_stateful_requirements(compiled, plan["prefix"]["contract"]))
    # Preserve deterministic order without repeating inherited requirements.
    return list({(r["kind"], r["name"], r["reason"]): r for r in reasons}.values())


async def _run_document(path: Path, compiled: CompiledTranscript) -> dict[str, Any]:
    contract: Any = compiled.compiled
    scenario = contract["scenario"]
    result: dict[str, Any] = {
        "source": str(path),
        "scenario_id": compiled.scenario_id,
        "scenario_version": scenario["version"],
        "schema_version": contract["schema_version"],
        "source_digest": compiled.source_digest,
        "compiled_bundle_digest": compiled.compiled_bundle_digest,
        "compilation": "PASS",
    }
    if scenario.get("kind") == "library":
        return {
            **result,
            "status": "COMPILED",
            "kind": "library",
            "dependencies": contract["dependencies"],
        }
    if contract["schema_version"] == 3:
        return {
            **result,
            "status": "NOT_RUNNABLE",
            "dependencies": contract["dependencies"],
            "variants": [
                {
                    "id": plan["id"],
                    "status": "NOT_RUNNABLE",
                    "reasons": _stateful_requirements(contract, plan),
                }
                for plan in contract["variants"]
            ],
        }
    if compiled.scenario_id != "local-planning-receipt":
        return {
            **result,
            "status": "NOT_RUNNABLE",
            "reasons": [
                {
                    "kind": "runner",
                    "name": compiled.scenario_id,
                    "reason": "no registered production scenario adapter",
                }
            ],
        }
    if scenario.get("status", "executable") != "executable":
        return {
            **result,
            "status": "NOT_RUNNABLE",
            "reasons": [
                {"kind": "status", "name": "design", "reason": "scenario is not marked executable"}
            ],
        }
    with TemporaryDirectory(prefix="chiplog-transcript-run-") as directory:
        root = Path(directory)
        try:
            observed = await run_scenario(path, root / "run.sqlite", root / "observed.json")
        except (LoopRejected, TranscriptCompileError) as error:
            return {**result, "status": "FAIL", "phase": "execution", "error": str(error)}
        if (
            observed["compiled_bundle_digest"] != compiled.compiled_bundle_digest
            or observed["source_digest"] != compiled.source_digest
        ):
            return {
                **result,
                "status": "FAIL",
                "phase": "execution",
                "error": "source changed between compilation and execution",
            }
        return {**result, "status": "PASS", "observed": observed}


async def run_transcripts(source: Path, artifact: Path) -> dict[str, Any]:
    """Retain one immutable report, including every compile failure and blocked variant."""
    if await asyncio.to_thread(artifact.exists):
        raise ValueError("existing transcript result cannot be overwritten")
    paths = discover_transcripts(source)
    if not paths:
        raise ValueError("no authored transcripts found")
    results: list[dict[str, Any]] = []
    compiled: list[tuple[Path, CompiledTranscript]] = []
    identities: set[str] = set()
    for path in paths:
        try:
            item = compile_transcript(path)
            if item.scenario_id in identities:
                raise TranscriptCompileError(f"duplicate scenario identity: {item.scenario_id}")
            identities.add(item.scenario_id)
            compiled.append((path, item))
        except (TranscriptCompileError, OSError, UnicodeError) as error:
            results.append(
                {
                    "source": str(path),
                    "status": "FAIL",
                    "compilation": "FAIL",
                    "phase": "compilation",
                    "error": str(error),
                }
            )
    for path, item in compiled:
        results.append(await _run_document(path, item))
    statuses = {item["status"] for item in results}
    status = (
        "FAIL"
        if "FAIL" in statuses
        else "NOT_RUNNABLE"
        if "NOT_RUNNABLE" in statuses
        else "PASS"
        if "PASS" in statuses
        else "COMPILED"
    )
    report = {
        "schema_version": 1,
        "status": status,
        "compilation": "PASS" if all(r["compilation"] == "PASS" for r in results) else "FAIL",
        "results": sorted(results, key=lambda r: r["source"]),
    }
    artifact.parent.mkdir(parents=True, exist_ok=True)
    with artifact.open("xb") as stream:
        stream.write(canonical_json_bytes(report))
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "source", type=Path, help="one transcript or a recursively scanned directory"
    )
    parser.add_argument("--artifact", required=True, type=Path, help="new immutable JSON result")
    args = parser.parse_args()
    report = asyncio.run(run_transcripts(args.source, args.artifact))
    print(
        json.dumps(
            {
                "status": report["status"],
                "compilation": report["compilation"],
                "artifact": str(args.artifact),
            }
        )
    )
    return {"PASS": 0, "COMPILED": 0, "FAIL": 1, "NOT_RUNNABLE": 2}[report["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
