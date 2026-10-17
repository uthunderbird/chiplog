"""Shared builders extracted without changing their behavior."""

import base64
import json

from chiplog.composition.r16_effects import (
    HermeticEffectProposal,
)


def _response() -> bytes:
    proposal = HermeticEffectProposal(
        schema_id="chiplog.hermetic-effect-proposal.v1",
        purpose="Record my hermetic action",
        payload_base64=base64.b64encode(b"\xffexact\x00payload").decode(),
        bundle_members=("self-action",),
    )
    return json.dumps(
        {
            "kind": "Continue",
            "tool_calls": [
                {
                    "call_id": "effect",
                    "tool": "propose_intent",
                    "text": proposal.canonical_bytes().decode(),
                }
            ],
        }
    ).encode()
