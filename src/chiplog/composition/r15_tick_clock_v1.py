"""Frozen v1 coordinate-clock leaf; publisher changes do not rewrite its identity.

Changes to this module require a new admitted clock version/migration for existing
selected ticks. Keep later interval/resolution/lease orchestration outside it.
"""

import hashlib
import time
from pathlib import Path

from chiplog.capabilities.agent_loop.contracts import LoopRejected
from chiplog.composition.r15_tick_contracts import TickClockPort, TickClockReading, TickClockSource


def _clock_source(identity: str) -> TickClockSource:
    return TickClockSource(
        source_id=identity,
        contract_version="chiplog.scheduler.coordinate-clock.v1",
        coordinate_codec="chiplog.scheduler.unix-ns.v1",
        deployment_profile="hermetic-offline",
        implementation_fingerprint=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    )


class SchedulerClock:
    @property
    def source(self) -> TickClockSource:
        return _clock_source("r15-unix-clock-v1")

    def observe(self) -> TickClockReading:
        return TickClockReading(unix_ns=time.time_ns(), monotonic_ns=time.monotonic_ns())


class EvaluationSchedulerClock:
    """Registered offline clock, injected at assembly; not accepted in tick payloads."""

    def __init__(self, unix_ns: int) -> None:
        self.unix_ns = unix_ns

    @property
    def source(self) -> TickClockSource:
        return _clock_source("r15-evaluation-clock-v1")

    def observe(self) -> TickClockReading:
        return TickClockReading(unix_ns=self.unix_ns, monotonic_ns=time.monotonic_ns())


# Registration captures original descriptors, not a later mutable class lookup.
# These constants and the interpreter are trusted; this is not process attestation.
_REGISTERED_IMPLEMENTATIONS = tuple(
    (kind, kind.__dict__["observe"], kind.__dict__["source"])
    for kind in (SchedulerClock, EvaluationSchedulerClock)
)


def registered_clock_source(clock: TickClockPort) -> TickClockSource:
    for kind, observe, source in _REGISTERED_IMPLEMENTATIONS:
        if type(clock) is not kind:
            continue
        namespace = object.__getattribute__(clock, "__dict__")
        if (
            "observe" in namespace
            or kind.__dict__.get("observe") is not observe
            or kind.__dict__.get("source") is not source
        ):
            raise LoopRejected("registered scheduler clock implementation changed")
        return clock.source
    raise LoopRejected("unregistered scheduler clock implementation")
