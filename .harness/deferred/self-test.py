#!/usr/bin/env python3
"""Black-box and state-machine tests for the deferred-defect ledger."""

from __future__ import annotations

import hashlib
import importlib.util
import pathlib
import shutil
import subprocess
import tempfile

BACKEND = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "deferred.py"
SPEC = importlib.util.spec_from_file_location("deferred_backend", BACKEND)
assert SPEC and SPEC.loader
backend = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(backend)


def run(root: pathlib.Path, *args: str, ok: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["python3", str(BACKEND), str(root), *args], text=True, capture_output=True, check=False
    )
    if ok and result.returncode != 0:
        raise AssertionError(result.stderr)
    if not ok and result.returncode == 0:
        raise AssertionError(f"unexpected success: {result.stdout}")
    return result


def git(root: pathlib.Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=root, text=True, capture_output=True, check=True
    )
    return result.stdout.strip()


def wrapper(root: pathlib.Path, name: str, *args: str, ok: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [str(root / ".harness" / "scripts" / f"{name}.sh"), *args],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )
    if ok and result.returncode != 0:
        raise AssertionError(result.stderr)
    if not ok and result.returncode == 0:
        raise AssertionError(f"unexpected wrapper success: {result.stdout}")
    return result


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def admission_text(revision: str, disposition: str = "non-blocking", target_hash: str = "") -> str:
    target = b"target\n"
    finding = b"finding_id: RT-7\n"
    evidence = b"reproducer fails\n"
    ratification = b"finding_id: RT-7\ndisposition: non-blocking\nratified_by: primary-agent\n"
    return f"""---
finding_id: RT-7
finding_locator: review.md#RT-7
finding_sha256: {digest(finding)}
original_severity: P1
ratified_disposition: {disposition}
ratified_by: primary-agent
ratified_at: 2026-09-03T12:00:00Z
ratification_locator: ratification.md
ratification_sha256: {digest(ratification)}
observed_revision: {revision}
target: target.txt
target_sha256: {target_hash or digest(target)}
evidence_locator: evidence.txt
evidence_sha256: {digest(evidence)}
expected_verdict: reproducer exits non-zero
---

## Finding

Concrete defect.

## Why non-blocking now

The current acceptance criterion still passes.

## Why deferred

Repair would expand the current scope.

## Promotion condition

- predicate: current acceptance test begins to fail
- owner: task owner
- next_action: take the defect into scope
- unknown: stop

## Invalidation condition

- predicate: reproducer no longer reaches the target
- owner: task owner
- next_action: retire after verification
- unknown: stop
"""


def main() -> None:
    with tempfile.TemporaryDirectory() as raw:
        root = pathlib.Path(raw)
        scripts = root / ".harness" / "scripts"
        scripts.mkdir(parents=True)
        for name in ("deferred.py", "defer-defect.sh", "take-defect.sh", "close-defect.sh"):
            shutil.copy2(BACKEND.parent / name, scripts / name)
        git(root, "init", "-q")
        git(root, "config", "user.email", "test@example.invalid")
        git(root, "config", "user.name", "Deferred Test")
        (root / "target.txt").write_bytes(b"target\n")
        (root / "review.md").write_bytes(b"finding_id: RT-7\n")
        (root / "evidence.txt").write_bytes(b"reproducer fails\n")
        (root / "ratification.md").write_bytes(
            b"finding_id: RT-7\ndisposition: non-blocking\nratified_by: primary-agent\n"
        )
        git(root, "add", ".")
        git(root, "commit", "-qm", "fixture")
        revision = git(root, "rev-parse", "--verify", "HEAD^{commit}")

        good = root / "good.md"
        good.write_text(admission_text(revision), encoding="utf-8")
        created = wrapper(root, "defer-defect", str(good)).stdout.strip()
        entry = root / created
        assert entry.is_file()
        original = entry.read_text(encoding="utf-8")

        old_now = backend.now_utc
        try:
            backend.now_utc = lambda: "2026-09-03T12:00:00Z"
            collision_source = root / "collision.md"
            collision_source.write_text(admission_text(revision), encoding="utf-8")
            first = backend.admission(root, collision_source)
            try:
                backend.admission(root, collision_source)
            except backend.DeferredError:
                pass
            else:
                raise AssertionError("ID collision was not rejected")
            assert first.is_file()
        finally:
            backend.now_utc = old_now

        blocking = root / "blocking.md"
        blocking.write_text(admission_text(revision, "blocking"), encoding="utf-8")
        run(root, "defer", str(blocking), ok=False)

        stale = root / "stale.md"
        stale.write_text(admission_text(revision, target_hash="0" * 64), encoding="utf-8")
        run(root, "defer", str(stale), ok=False)

        missing = root / "missing.md"
        missing.write_text(admission_text("0" * 40), encoding="utf-8")
        run(root, "defer", str(missing), ok=False)

        unknown = root / "unknown.md"
        unknown.write_text(
            admission_text(revision).replace("finding_id: RT-7\n", "finding_id: RT-7\nextra: no\n", 1),
            encoding="utf-8",
        )
        run(root, "defer", str(unknown), ok=False)

        permissive = root / "permissive.md"
        permissive.write_text(
            admission_text(revision).replace("- unknown: stop", "- unknown: ignore", 1),
            encoding="utf-8",
        )
        run(root, "defer", str(permissive), ok=False)

        unknown_section = root / "unknown-section.md"
        unknown_section.write_text(
            admission_text(revision) + "\n## Unbound note\n\nORIGINAL\n",
            encoding="utf-8",
        )
        run(root, "defer", str(unknown_section), ok=False)

        bad_fragment = root / "bad-fragment.md"
        bad_fragment.write_text(
            admission_text(revision).replace("review.md#RT-7", "review.md#DOES-NOT-EXIST"),
            encoding="utf-8",
        )
        run(root, "defer", str(bad_fragment), ok=False)

        injected_ratifier_bytes = (
            b"finding_id: RT-7\ndisposition: non-blocking\nratified_by: primary | agent\n"
        )
        (root / "ratification.md").write_bytes(injected_ratifier_bytes)
        git(root, "add", "ratification.md")
        git(root, "commit", "-qm", "injected ratifier fixture")
        injected_revision = git(root, "rev-parse", "--verify", "HEAD^{commit}")
        injected_ratifier = root / "injected-ratifier.md"
        injected_ratifier.write_text(
            admission_text(injected_revision)
            .replace("ratified_by: primary-agent", "ratified_by: primary | agent")
            .replace(
                "ratification_sha256: "
                + digest(b"finding_id: RT-7\ndisposition: non-blocking\nratified_by: primary-agent\n"),
                "ratification_sha256: " + digest(injected_ratifier_bytes),
            ),
            encoding="utf-8",
        )
        run(root, "defer", str(injected_ratifier), ok=False)

        injected_evidence = root / "injected-evidence.md"
        (root / "ev | bad.txt").write_bytes(b"bad locator evidence\n")
        (root / "ratification.md").write_bytes(
            b"finding_id: RT-7\ndisposition: non-blocking\nratified_by: primary-agent\n"
        )
        git(root, "add", "ev | bad.txt", "ratification.md")
        git(root, "commit", "-qm", "injected evidence locator fixture")
        evidence_revision = git(root, "rev-parse", "--verify", "HEAD^{commit}")
        injected_evidence.write_text(
            admission_text(evidence_revision)
            .replace("evidence_locator: evidence.txt", "evidence_locator: ev | bad.txt")
            .replace(
                "evidence_sha256: " + digest(b"reproducer fails\n"),
                "evidence_sha256: " + digest(b"bad locator evidence\n"),
            ),
            encoding="utf-8",
        )
        run(root, "defer", str(injected_evidence), ok=False)

        (root / "ratification.md").write_bytes(
            b"finding_id: RT-7\ndisposition: non-blocking\nratified_by: primary-agent\n"
        )

        contradictory_bytes = (
            b"finding_id: RT-7\ndisposition: non-blocking\nratified_by: primary-agent\n"
            b"disposition: blocking\n"
        )
        (root / "ratification.md").write_bytes(contradictory_bytes)
        git(root, "add", "ratification.md")
        git(root, "commit", "-qm", "contradictory ratification")
        contradictory_revision = git(root, "rev-parse", "--verify", "HEAD^{commit}")
        stale_revision = root / "stale-revision.md"
        stale_revision.write_text(admission_text(revision), encoding="utf-8")
        run(root, "defer", str(stale_revision), ok=False)
        contradictory = root / "contradictory.md"
        contradictory.write_text(
            admission_text(contradictory_revision).replace(
                "ratification_sha256: "
                + digest(b"finding_id: RT-7\ndisposition: non-blocking\nratified_by: primary-agent\n"),
                "ratification_sha256: " + digest(contradictory_bytes),
            ),
            encoding="utf-8",
        )
        run(root, "defer", str(contradictory), ok=False)

        entry_id = entry.stem
        wrapper(root, "close-defect", entry_id, "active", "owner", revision, "", ok=False)
        assert entry.read_text(encoding="utf-8") == original
        tampered = original.replace("finding_id: RT-7", "finding_id: forged", 1)
        entry.write_text(tampered, encoding="utf-8")
        run(root, "take", entry_id, "owner", revision, ok=False)
        entry.write_text(original, encoding="utf-8")

        severity_tampered = original.replace("original_severity: P1", "original_severity: forged", 1)
        entry.write_text(severity_tampered, encoding="utf-8")
        run(root, "take", entry_id, "owner", revision, ok=False)
        entry.write_text(original, encoding="utf-8")

        entry.write_text(original + "\n## Unbound note\n\nMUTATED\n", encoding="utf-8")
        run(root, "take", entry_id, "owner", revision, ok=False)
        entry.write_text(original, encoding="utf-8")

        create_line = next(line for line in original.splitlines() if " | create | " in line)
        entry.write_text(original.replace(create_line + "\n", ""), encoding="utf-8")
        run(root, "take", entry_id, "owner", revision, ok=False)
        entry.write_text(original, encoding="utf-8")

        run(root, "take", entry_id, "owner\n- injected", revision, ok=False)
        assert entry.read_text(encoding="utf-8") == original

        wrapper(root, "take-defect", entry_id, "owner", revision)
        taken = entry.read_text(encoding="utf-8")
        assert "status: active" in taken
        forged = taken.replace(" | take:active | owner | ", " | take:active | forged-owner | ")
        entry.write_text(forged, encoding="utf-8")
        run(root, "close", entry_id, "resolved", "owner", revision, "evidence.txt", ok=False)
        entry.write_text(taken, encoding="utf-8")
        run(root, "take", entry_id, "owner", revision)
        assert entry.read_text(encoding="utf-8") == taken
        run(root, "take", entry_id, "other-owner", revision, ok=False)

        missing_event = backend.transition_id(
            entry_id, "resolved", "owner", revision, "missing.txt"
        )
        forged_terminal = taken.replace("status: active", "status: resolved").replace(
            "\n## History\n",
            f"\n## History\n",
        ).rstrip() + (
            f"\n- 2026-09-03T12:00:01Z | {missing_event} | close:resolved | owner | "
            f"{revision} | missing.txt\n"
        )
        entry.write_text(forged_terminal, encoding="utf-8")
        run(root, "close", entry_id, "resolved", "owner", revision, "missing.txt", ok=False)
        entry.write_text(taken, encoding="utf-8")

        wrapper(
            root, "close-defect", entry_id, "resolved", "owner", revision, "evidence.txt"
        )
        resolved = entry.read_text(encoding="utf-8")
        assert "status: resolved" in resolved
        run(root, "close", entry_id, "resolved", "owner", revision, "evidence.txt")
        assert entry.read_text(encoding="utf-8") == resolved
        run(root, "close", entry_id, "retired", "owner", revision, "evidence.txt", ok=False)

        semantic = run(
            root, "close", entry_id, "bogus", "owner", revision, "evidence.txt", ok=False
        )
        assert "→ сделай:" in semantic.stderr and "✓ " in semantic.stderr
        run(root, "close", first.stem, "moved", "owner", revision, "tracker:", ok=False)
        run(
            root,
            "close",
            first.stem,
            "moved",
            "owner",
            revision,
            "tracker:this is not an id or url",
            ok=False,
        )

        assert original != resolved
        print("deferred ledger self-test: ok")


if __name__ == "__main__":
    main()
