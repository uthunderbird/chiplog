"""Private closed registration for the native CLI context issuer."""

from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True, slots=True)
class NativeOneCallContextSlotV1:
    slot_id: Literal["r17-native-one-call-context"] = field(
        default="r17-native-one-call-context", init=False
    )
    source_profile: Literal["chiplog.execution.r17-native-one-call-context.v1"] = field(
        default="chiplog.execution.r17-native-one-call-context.v1", init=False
    )
    mandate_profile: Literal["r17-native-context-record-one-call-v1"] = field(
        default="r17-native-context-record-one-call-v1", init=False
    )
    inventory_profile: Literal["NATIVE_CLI_NO_SCHEDULER_SOURCE_V1"] = field(
        default="NATIVE_CLI_NO_SCHEDULER_SOURCE_V1", init=False
    )
    recorded_call_count: Literal[1] = field(default=1, init=False)
    send_authorized: Literal[False] = field(default=False, init=False)
    execution_authorized: Literal[False] = field(default=False, init=False)
    disclosure_authorized: Literal[False] = field(default=False, init=False)
