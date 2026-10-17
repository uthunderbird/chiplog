"""Shared builders extracted without changing their behavior."""

import json


def effect_response(*labels: str) -> bytes:
    return json.dumps(
        {
            "kind": "Continue",
            "tool_calls": [
                {
                    "call_id": label,
                    "tool": "request_self_effect",
                    "arguments": {"payload": "aGVsbG8=", "bundle_members": [label]},
                }
                for label in labels
            ],
        }
    ).encode()
