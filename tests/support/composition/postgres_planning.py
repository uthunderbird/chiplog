"""Shared builders extracted without changing their behavior."""

import hmac

from tests.support.deployment_gate import KEY


def verifier(payload: bytes, proof: bytes) -> bool:
    return hmac.compare_digest(hmac.digest(KEY, payload, "sha256"), proof)
