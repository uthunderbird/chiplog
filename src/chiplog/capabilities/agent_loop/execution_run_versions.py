"""Discriminated, representation-only executable Run and creation unions."""

from typing import Annotated

from pydantic import Field

from .execution_contracts import ExecutionRunRecord
from .execution_history_contracts import ExecutionRunRecordV3
from .execution_history_transition_contracts import CreateExecutionRunV3
from .execution_transition_contracts import CreateExecutionRun

ExecutionRun = Annotated[
    ExecutionRunRecord | ExecutionRunRecordV3,
    Field(discriminator="schema_id"),
]

CreateExecutionRunVersion = Annotated[
    CreateExecutionRun | CreateExecutionRunV3,
    Field(discriminator="kind"),
]
