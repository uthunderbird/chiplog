# Scheduler V3 source revision 4 review

Fresh publication capture previously read the admitted startup twice: once
inside shared-history validation and once immediately afterwards. Revision 4
returns that admitted startup alongside the validated loop snapshot. Capture
uses it within the existing held gate and SQL snapshot. Production deadlines
are unchanged.

The second startup also checked independent state after history validation.
Capture therefore retains a fresh owner-journal snapshot, including tenant,
head, decisions and materialized command IDs, followed by a fresh source
registration observation. Physical database, SQL commitment and loop checks
remain in place.

## Reviewed source bytes

The original history reader was obtained from verified commit
`97d60d17d5158f9ebea05d15dc895087cbf4a6db`. AST comparison proves its body
equals the extracted helper after normalizing the helper name, return
annotation and the added startup return value. All other module nodes are
unchanged. The public reader returns the first tuple element.

Only the history module's source hash changes in the revision 4 manifest.
This is a reviewed extraction, not generic formatting compatibility. Runtime
admission recognizes only this exact whole-file source pair:

| Source | SHA-256 |
|---|---|
| Historical history module | `cb35358fccf08a07acc22f3046e4a1d5ce5875754bd3f505c6109064db5ee80a` |
| Extracted history module | `e655c248a479b64f73d3d87ad36080b5a4216d4819ef011ad34e90da30b64c90` |
| Revision 4 manifest | `641801b242ec5f5aed7423a194a811323e66b568a1b760c372b39daca87b18b9` |

Original V3, revision 2 and revision 3 manifest bytes and registration IDs
remain unchanged, with ranks 5, 6 and 7. Revision 4 has rank 8. Tests exercise
historical selections and reject source-order regression and altered source
bytes. Neither changed runtime module belongs to the historical V2b closure.

## Evidence

The mounted exact-count test was RED before extraction: two physical cuts
and four source observations instead of the required one cut and three
observations. After extraction it passes. Negative tests change the owner
journal or final source registration after shared-history validation and
require rejection. Removing only the new owner fence makes its negative
test fail; restoring the source makes it pass.

`uv run pytest -q --tb=short -p no:timeout tests/platform/test_scheduler_reads.py tests/composition/test_scheduler_genesis_runtime.py -k 'fresh_scheduler_capture or shared_history or selected_readback or scheduler_reads'`:
91 passed, 18 deselected in 62.90 seconds.

The previously failing selected-crash mandate recovery was rerun under
mcp-debugger with a breakpoint only at the clock-expiry refusal. It passed
in 22.34 seconds without reaching that verified breakpoint. Before this
change the debugger observed preview expiry by 309.94 ms at that refusal,
with no clock regression. This supports the duplicate-work diagnosis; it
does not establish a general latency bound or whole-repository readiness.

`uv run pytest -q --tb=short -p no:timeout tests/composition/test_scheduler_mandate_issuance_runtime.py tests/composition/test_r15_lease_v2b_history.py`:
14 passed in 123.60 seconds, including all mandate issuance, rejection,
restart and selected-crash scenarios and the historical V2b/V2c controls.
After correcting test imports to their defining modules, the three new
capture cases passed again in 15.07 seconds. Targeted mypy on all six
changed Python files passes. The gate's whole-repository `uv run mypy`
retains its prior 3354 errors; this slice adds none.
