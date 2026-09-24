# H1 terminal acceptance: retained RED witness

Date: 2026-09-25. These cases exercise the public `CommonCliExecutionRuntime` with a real selected CLI inbox and native Run. They remain open until the H1 terminal batch is selected and physically materialized.

| Case | Test | Expected durable observation | First observed failure |
|---|---|---|---|
| H1-T01 | `test_socket_complete_selects_native_terminal_batch` | One selected owner-journal V2 completion, terminal Run, conversation and local Commentary intent in the exact physical batch | Receipt phase `RUNNING` rather than `TERMINAL` |
| H1-T02 | `test_terminal_drive_reopens_without_second_decision_or_model_call` | Exact terminal replay after reopen with no second model call or owner decision | Initial completion receipt phase `RUNNING` rather than `TERMINAL` |
| H1-T03 | `test_prepared_complete_seal_reopens_and_publishes_once` | Existing selected seal continues to one terminal batch after reopen, without another model call | Continued receipt phase `RUNNING` rather than `TERMINAL` |

Command: `uv run pytest -q tests/composition/test_common_cli_execution_runtime.py -k 'socket_complete_selects_native_terminal_batch or terminal_drive_reopens_without_second_decision_or_model_call or prepared_complete_seal_reopens_and_publishes_once' --tb=short`

Checkpoint result: 3 xfailed, 2 deselected. Each case is marked strict with
`H1TerminalBoundaryUnavailable`, raised only when the public receipt remains
`RUNNING` at the terminal boundary. This is a behavioral RED, not evidence that
the H1 mechanism is complete.

To inspect the active RED without the marker, add `--runxfail` to the command
above. It must produce three failures headed by
`H1TerminalBoundaryUnavailable`, with `RUNNING` versus the required `TERMINAL`
in the exception text. Any other phase or a downstream owner-journal, physical
batch, replay, model, or transfer failure remains an ordinary test failure.
