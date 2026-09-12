from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def main() -> int:
    root = Path.cwd()
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "tests/domain_primitives/test_canonicalization.py",
            "tests/architecture/test_inert_shared.py",
            "tests/verification/test_inert_shared.py",
        ],
        cwd=root,
        check=False,
    )
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
