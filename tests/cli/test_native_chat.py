import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from chiplog.capabilities.agent_loop.ingress_client_contracts import NativeIngressIdentityV1
from chiplog.composition.postgres_native_cli import source_identifier
from chiplog.platform.postgres import PostgresServer
from chiplog.platform.postgres_gate import canonical
from tests.support.deployment_gate import entitlement


def test_actual_native_chat_command_delivers_locally_and_replays_without_model(
    postgres_server: PostgresServer,
    tmp_path: Path,
) -> None:
    database = postgres_server.user_database("native-cli")
    tenant = database.identity
    identity = [
        "--tenant",
        tenant,
        "--principal",
        "local-user",
        "--credential",
        "c",
        "--session",
        "s",
    ]
    environment = {
        **os.environ,
        "CHIPLOG_POSTGRES_DSN": postgres_server.conninfo,
        "CHIPLOG_R6_OPERATOR_SECRET": "native-command-fixture",
    }
    prefix = [sys.executable, "-m", "chiplog.cli"]
    bootstrap = subprocess.run(
        [*prefix, "bootstrap", *identity, "--database-instance", "db", "--token", "bootstrap"],
        env=environment,
        capture_output=True,
    )
    assert bootstrap.returncode == 0, bootstrap.stderr
    private = Ed25519PrivateKey.generate()
    now = time.time_ns()
    base = entitlement()
    grant = base.model_copy(
        update={
            "generation": base.generation.model_copy(update={"tenant_id": tenant}),
            "freshness_leases": (("evidence", now + 10**12),),
            "bounds": base.bounds.model_copy(
                update={
                    "capability_id": "delivery.first_send",
                    "starts_ns": now - 1,
                    "expires_ns": now + 10**12,
                }
            ),
        }
    )
    (tmp_path / "grant.json").write_bytes(canonical(grant.model_dump(mode="json")))
    (tmp_path / "grant.sig").write_bytes(private.sign((tmp_path / "grant.json").read_bytes()))
    (tmp_path / "public.key").write_bytes(private.public_key().public_bytes_raw())
    ingress_identity = NativeIngressIdentityV1(
        tenant_id=tenant,
        channel="CLI",
        registered_source_id=source_identifier("local-user"),
        replay_id="request:1",
    )
    run_id = "ingress-run:" + hashlib.sha256(ingress_identity.canonical_bytes()).hexdigest()
    response = {
        "kind": "Complete",
        "tenant": tenant,
        "run_id": run_id,
        "turn_id": run_id + "/turn/1",
        "deliveries": [
            {"payload": [{"kind": "NonAuthoritativeText", "text": "Native local answer"}]}
        ],
    }
    (tmp_path / "response.json").write_text(json.dumps(response))
    command = [
        *prefix,
        "native-chat",
        *identity,
        "--request-id",
        "request:1",
        "--message",
        "hello",
        "--state-dir",
        str(tmp_path),
        "--entitlement",
        str(tmp_path / "grant.json"),
        "--entitlement-proof",
        str(tmp_path / "grant.sig"),
        "--verification-key",
        str(tmp_path / "public.key"),
        "--model-provider",
        "hermetic",
    ]
    for option in ("--principal", "--session"):
        forged = list(command)
        forged[forged.index(option) + 1] = "forged"
        denied = subprocess.run(forged, env=environment, capture_output=True)
        assert denied.returncode != 0 and denied.stdout == b""
        assert not (tmp_path / "inbox").exists()
    first = subprocess.run(
        [*command, "--response-file", str(tmp_path / "response.json")],
        env=environment,
        capture_output=True,
    )
    assert first.returncode == 0, first.stderr
    assert (
        first.stdout
        == b'UNVERIFIED MODEL COMMENTARY\n"Native local answer"\nEND UNVERIFIED MODEL COMMENTARY'
    )
    before = database.snapshot()
    replay = subprocess.run(command, env=environment, capture_output=True)
    assert replay.returncode == 0, replay.stderr
    assert replay.stdout == first.stdout
    assert database.snapshot() == before
    assert len(list((tmp_path / "inbox").glob("*.json"))) == 1
    with database.read() as connection:
        assert connection.execute(
            "SELECT count(*) FROM model_attempt_custody WHERE state='STARTED'"
        ).fetchone() == (1,)
    changed = list(command)
    changed[changed.index("hello")] = "changed"
    rejected = subprocess.run(changed, env=environment, capture_output=True)
    assert rejected.returncode != 0 and rejected.stdout == b""
    assert b"replay" in rejected.stderr
    assert database.snapshot() == before
    (tmp_path / "public.key").write_bytes(
        Ed25519PrivateKey.generate().public_key().public_bytes_raw()
    )
    wrong_key = subprocess.run(command, env=environment, capture_output=True)
    assert wrong_key.returncode != 0 and wrong_key.stdout == b""
    assert b"proof rejected" in wrong_key.stderr
