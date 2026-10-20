"""One-time authentic V1 fixture capture; run from producer checkout at 0846538."""

from typing import TypedDict


class _CapturedFile(TypedDict):
    mode: int
    sha256: str
    bytes_b64: str


class _CapturedBundle(TypedDict):
    producer_revision: str
    database_name: str
    operator_secret: str
    files: dict[str, _CapturedFile]


def main() -> None:
    import asyncio
    import base64
    import gzip
    import hashlib
    import json
    import stat
    import subprocess
    import sys
    import tempfile
    from pathlib import Path

    from chiplog.composition.r7_planning import open_r7_runtime

    revision = subprocess.check_output(["git", "rev-parse", "--verify", "HEAD"], text=True).strip()
    assert revision == "08465384aa9127909909de49d9a9eb560a8b3cd4", revision
    directory = Path(tempfile.mkdtemp(prefix="r14-legacy-genesis-producer-"))
    database = directory / "legacy.sqlite3"

    async def produce() -> None:
        async with open_r7_runtime(
            database,
            tenant_id="legacy-fixture-tenant",
            operator_secret=b"r14-public-legacy-fixture-secret",
        ) as runtime:
            await runtime.bootstrap(
                database_instance_id="legacy-fixture-database",
                principal_id="legacy-fixture-principal",
                credential_id="legacy-fixture-credential",
                session_id="legacy-fixture-session",
                token="legacy-fixture-token",
            )
            observed = runtime._trust.verify()
            assert observed is not None
            assert observed.phase == "ACTIVE"

    asyncio.run(produce())
    files: dict[str, _CapturedFile] = {}
    for path in sorted(directory.iterdir()):
        assert path.is_file() and not path.is_symlink(), path
        raw = path.read_bytes()
        assert str(directory).encode() not in raw, path
        files[path.name] = {
            "mode": stat.S_IMODE(path.stat().st_mode),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "bytes_b64": base64.b64encode(raw).decode("ascii"),
        }
    capture: _CapturedBundle = {
        "producer_revision": revision,
        "database_name": database.name,
        "operator_secret": "r14-public-legacy-fixture-secret",
        "files": files,
    }
    output = Path(sys.argv[1])
    output.write_bytes(
        gzip.compress(json.dumps(capture, sort_keys=True, separators=(",", ":")).encode(), mtime=0)
    )
    print(
        json.dumps(
            {
                "directory": str(directory),
                "output": str(output),
                "bytes": output.stat().st_size,
                "files": {n: len(base64.b64decode(f["bytes_b64"])) for n, f in files.items()},
            }
        )
    )


if __name__ == "__main__":
    main()
