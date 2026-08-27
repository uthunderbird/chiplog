from __future__ import annotations

import argparse
from pathlib import Path

from .runner import CLOSED_PROFILES, run_profile


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a closed Chiplog verification profile")
    parser.add_argument("profile", choices=CLOSED_PROFILES)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    result, artifact = run_profile(args.root.resolve(), args.profile)
    print(f"{result['status']}: {args.profile}; artifact={artifact}")
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
