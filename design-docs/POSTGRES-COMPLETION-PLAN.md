# Native completion and delivery implementation plan

## Plan

1. Build a native `postgres_completion` owner using the existing pure
   `prepare_completion` and actual retained `DeliveryObservation` inputs. Do not
   accept an externally constructed observation as authority. Read the selected
   captured Complete bytes from the current sealed Run; authenticate the peer and
   inspect every selected seal's current closure inside the writer transaction.
2. Reconstruct historical disclosure envelopes from all retained actual attempt
   manifest members. Register only the actual configured hermetic self recipient;
   preserve the original authenticated origin tuple. Model-selected substitutions
   still require the exact currently registered tuple and disclosure permission.
3. Expose current provider queries from native original effect closure/current
   reductions, including exact evidence and current source frontier. Unsupported
   planning/fact/candidate query authorities HOLD rather than inventing records.
   Add these actual query DTOs to the next model context, so a Complete assertion
   can cite them. The query frontier is the last native original-effect semantic-reduction publication;
   model preparation/capture/sealing and delivery publications do not invalidate
   their own input cut. Receipt changes atomically publish a new reduction.
   Retain the immutable sealed source Run head and the actual acceptance-time
   worker reference. Reconstruct the same acceptance primitive from that historical
   source, while current actor/worker authorization is checked independently.
4. Atomically publish native CompleteAcceptance Run, actual DeliveryManifest,
   acceptance proposal, rendered conversation entries, and prepared delivery
   intents. Identities are actual completion/delivery subjects, never fake original
   consequential call IDs. Preserve original obligations. Replay authenticates but
   does not publish duplicate history or delivery intents.
5. Delivery has a distinct immutable first-send ticket and a durable claim before
   the existing HermeticEffectsProvider.emit_issued call. Use actual registered
   key, persistent custody journals, explicit exact signed gate/default HOLD,
   fresh actor/worker and live gate at claim. Inside that same locked claim
   transaction, recheck every selected seal and reconstruct current assertion
   queries against the retained sealed source; reproduce the accepted proposal.
   A late rival after acceptance must prevent SEND. Using the later accepted Run
   head or a fresh worker in the historical primitive would self-invalidate it.
   Delivery preparation is not provider
   receipt. Lost response/reopen never automatically resend; reconciliation only
   consumes authenticated original receipt. Store an exact terminal delivery
   result separately from CompleteAcceptance.
6. Focused real PostgreSQL tests: acceptance atomic batch/history/reopen; actual
   rendered payload + exact recipient SEND; gate default HOLD and claim-time
   expiry/revocation; lost response/reopen no repeat SEND; altered recipient or
   restricted disclosure rejection; actual current provider assertion; older seal
   late rival blocks CompleteAcceptance before publication. Completion remains
   accepted if later evidence changes; new SEND must revalidate the current cut.

## Assumptions

- The current production transport available in this repository is the registered
  durable hermetic self recipient, not a generic external provider network.
- Native model Complete already has a sealed zero-call response. Prior original
  obligations remain independent and must currently close at acceptance and SEND.
- `prepare_completion` owns rendering/disclosure semantics; its inert references
  bind actual persisted bytes, not newly invented proof authority.
- No existing native planning/fact/candidate current-query owner exists; accepting
  arbitrary caller DTOs would create authority. Only actual native effect queries
  are mounted in this slice, with unsupported assertion classes explicitly HOLD.
- Root owns model custody replacement files concurrently; completion owns new
  contracts/module/tests and coordinates only thin execution context/wrapper seams.
- Existing SQLite sources and historical record bytes are not changed or deleted.
