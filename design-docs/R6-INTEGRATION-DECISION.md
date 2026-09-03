# R6 integration decision: local peer credential and public composition seam

Date: 2026-09-03

## Decision

R6 extends the frozen R4/R5 seam with an immutable `peer_credential` field on
`AuthenticationRequest`, `TrustReference`, and `PlanningTrustReference`.  In the
R6 CLI contour its only valid value is the local POSIX identity `uid:<getuid()>`;
the value is obtained in composition, never from an argument, environment value,
or planning payload.

R4 persists this value with the current credential binding. Authentication and
revalidation require exact equality. Credential rotation and emergency recovery
retain the binding; changing it is not supported by R6 and fails closed.

R6 additionally exposes a public composition port/factory so a component test
can drive the CLI slice through public R4/R5 values and ports without importing
private owner implementations.

## Compatibility

This is a development-store reset boundary. A pre-decision R4 materialization
without the peer binding is rejected during replay rather than interpreted as an
empty peer binding. R6 has no migration claim for pre-decision local stores.

## Limit

`uid:<getuid()>` is a local POSIX process binding, not remote cryptographic OS
attestation. Stronger peer attestation belongs to the R7 broker/IPC boundary.
