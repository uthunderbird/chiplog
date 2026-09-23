# R17 retained CLI authenticated custody, slice A

This slice extends the existing retained-file PRE_AUTH allocation and staging
path with actual Unix-peer authentication and one durable authenticated successor.
It is not completion of universal ingress, admission scheduling, quarantine,
transport follow-ups, delivery or channel parity. The remaining requirement map
is in the continuation plan. The later user instruction requires no retrospective.

## Authority and representation

`R17IngressRuntime.cli_custody(slot)` opens a private Unix socket for one already
staged token. The retained payload stays in the existing file source; the socket
does not upload a replacement payload. The adapter obtains credentials from the
accepted connection using Linux SO_PEERCRED or BSD getpeereid. Unsupported
mechanisms fail closed. The broker process UID is not an authentication input.

The server offers the exact original token, staged head, source profile, raw
digest/count and replay identity plus a random challenge bound to that scope,
connection, policy and five-second deadline. Frames use a four-byte unsigned
big-endian length: offer <=262144 bytes, response <=4096 bytes. The only response
confirms the challenge fingerprint. Canonical equality and write-side EOF reject
extra fields, changed tokens, additional frames and noncanonical encodings. A
TaskGroup owns the exchange; closing the context closes the accepted connection
and joins the exchange before removing the private endpoint.

Manifest version 9 adds the isolated deployment-trust operation
`deployment_trust.authenticate_cli_custody`. Existing manifests 4–8 retain their
original routes and targets. Input schema `chiplog.cli.custody-owner-call.v1`
contains the original broker-verified trust snapshot and the public
`chiplog.cli.custody-authentication.v1` request. The decision schema is
`chiplog.cli.custody-decision.v1`. Authentication compares the actual observed
UID with the registered credential, session, tenant/database, source and exact
retained command, and checks the fixed endpoint/policy commitments and deadline.

The authenticated command retains the complete original PublicPortCall and
PublicPortSuccess frames, including request ID, caller/callee identities and
budget. The complete encoded frames are bounded at 262144 and 65536 bytes.
Historical validation replays the owner interpretation against the original
snapshot and compares exact correlation and scope. It never uses current trust
state to rewrite a selected historical decision.

The private authority keeps the issued request object, accepted connection,
verified trust observation and original source capture. Before selection and at
the writer cut it rechecks source identity/bytes, endpoint inode/path, OS peer,
session/generation, deadline and ledger/frontier. A copied request, equal batch
or copied prepared publication does not acquire its private issuance.

## Durable closure and recovery

The unchanged token retains UNKNOWN_PRE_AUTH endpoint identity. Authentication
adds a separate observed endpoint witness. The new command/record schemas are
`chiplog.ingress.authenticated-command.v2` and
`chiplog.ingress.authenticated-record.v2`. One physical record contains the full
command, exact embedded raw inbox and ADMITTED_DURABLE custody. The inbox ID is
deterministic over tenant/database/source/token. Custody hashes the contained
inbox body; the inbox contains no custody or containing-record head, so the
commitment preimage is acyclic.

The existing successor command identity is preserved. A v1 retry cannot become
an authenticated successor, and a selected authenticated successor cannot
become a v1 retry. `read_admitted_inbox(token)` reconstructs complete mixed history
and returns the selected decision head, actual physical record head/sequence and
the full embedded body. Unknown ingress schema versions and payload aliases are
classified as ingress and rejected rather than hidden as unrelated records.

Startup validates original selected v2 output before the inherited R14 physical
recovery path may write. The existing single-pending-family handling is retained.
Selected custody can recover after the peer and retained source disappear. A
fresh exact replay still requires its own current authenticated exchange; it
returns original selected bytes. This slice issues no ACK, releases no source
reserves, normalizes no semantic command and sends no external response.

## Verification scope

Runtime evidence lives in
`tests/composition/test_ingress_authenticated_custody.py`; original PRE_AUTH,
retry, pending-family and corrupt-history regressions remain in
`test_ingress_custody_runtime.py`. Public wire consumers are independent of the
private owner implementation. The offline import guard admits only exact
`socket` and `ctypes` imports in the reviewed CLI socket leaf, with neighboring
path, SSL, dynamic-import, qualified-submodule and provider negative probes.

Fixtures cover actual local OS credentials, same-connection confirmation,
forged control frames, source/endpoint changes at writer admission, private
issuance copies, context cancellation, before/after materialization crashes,
source-free restart, exact replay and malformed selected output before recovery.
They do not claim a live Telegram/provider path, other-UID operating-system
integration on this host, admission fairness or full R17 completion.
