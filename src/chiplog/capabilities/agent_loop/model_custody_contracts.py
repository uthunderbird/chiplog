"""Private hooks at the actual model transport boundary, never model input."""

from typing import Protocol

from .contracts import ModelAttempt
from .execution_contracts import ExecutionModelAttempt
from .execution_history_contracts import ExecutionModelAttemptV3

type CustodiedAttempt = ModelAttempt | ExecutionModelAttempt | ExecutionModelAttemptV3


class ModelAttemptCustody(Protocol):
    def start(self, attempt: CustodiedAttempt) -> None: ...

    def retain(self, attempt: CustodiedAttempt, raw: bytes, receipt: str) -> None: ...
