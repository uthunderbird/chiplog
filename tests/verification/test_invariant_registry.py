from __future__ import annotations

from pathlib import Path

import pytest

from chiplog.verification.invariants import InvariantManifestError, extract_invariant_manifest

ROOT = Path(__file__).resolve().parents[2]

SOURCE = ROOT / "design-docs/project-architecture/NORMATIVE.md"


def test_canonical_invariant_manifest_is_source_derived_and_complete() -> None:
    source_digest, entries = extract_invariant_manifest(SOURCE)

    assert len(source_digest) == 64
    assert [entry.number for entry in entries] == list(range(1, 109))
    assert [entry.invariant_id for entry in entries] == [
        f"A{number:02d}" for number in range(1, 109)
    ]
    assert len({entry.text_digest for entry in entries}) == 108


@pytest.mark.parametrize("mutation", ["omit", "duplicate", "renumber", "unknown"])
def test_invariant_manifest_rejects_source_mutations(tmp_path: Path, mutation: str) -> None:
    text = SOURCE.read_text()
    if mutation == "omit":
        text = text.replace("1. `VISION.md`", "101. `VISION.md`", 1)
    elif mutation == "duplicate":
        text = text.replace("2. Tenant and principal", "1. Tenant and principal", 1)
    elif mutation == "renumber":
        text = text.replace("108. `ModelCallAttempt`", "109. `ModelCallAttempt`", 1)
    else:
        text = text.replace(
            "## Operation-level deployment gate",
            "109. Unknown invariant.\n\n## Operation-level deployment gate",
            1,
        )
    path = tmp_path / "document.md"
    path.write_text(text)

    with pytest.raises(InvariantManifestError):
        extract_invariant_manifest(path)


def test_invariant_text_change_changes_entry_and_source_identity(tmp_path: Path) -> None:
    original_source, original_entries = extract_invariant_manifest(SOURCE)
    path = tmp_path / "document.md"
    path.write_text(
        SOURCE.read_text().replace("sole product authority", "only product authority", 1)
    )

    changed_source, changed_entries = extract_invariant_manifest(path)

    assert changed_source != original_source
    assert changed_entries[0].text_digest != original_entries[0].text_digest
