# PLAN-FACT-JOURNAL split manifest

This is one-time migration evidence, not a contract registry or a source of product semantics.

- Source path: `design-docs/PLAN-FACT-JOURNAL.md`
- Source Git revision: `0be77291f5c21f45dc6094932188f1e5bfdc6a37`
- Source Git blob: `f3357c964d563450827410d952b326d78e0b5047`
- Source byte length: `188659`
- Source SHA-256: `76b9526f00de8373c6658948ecac47d661191e10ed54c06dd0387e9312667afd`
- Encoding and line endings: UTF-8, LF

Ranges are half-open byte intervals in the source blob. Source ordinals partition
`[0, 188659)` exactly; destination ordinals define copied-payload order inside each file.

| Source ordinal | Source lines | Byte range | Bytes | Destination | Payload ordinal | Payload SHA-256 |
|---|---:|---:|---:|---|---:|---|
| R01 | 1–120 | `[0, 7049)` | 7049 | `README.md` | 1 | `69d1d399f46204d79f6fa1d77e0d8602b2be286c9ba5812580f3d04b6beb0a91` |
| R02 | 121–957 | `[7049, 46140)` | 39091 | `SCHEMA.md` | 1 | `c1a1fe19f334db702d54678539a1d373ef694161adca9527101738e2a3d72609` |
| R03 | 958–1172 | `[46140, 60500)` | 14360 | `SEMANTICS.md` | 1 | `1304025599fb5d700597276ff3f34490b9032efdfeb57062d28c406e6d28a05a` |
| R04 | 1173–1265 | `[60500, 66699)` | 6199 | `EFFECTS.md` | 1 | `da94741c562052a5360481f3b9ff315d0e9eb2dc0ed1d8962124c8bc90c418eb` |
| R05 | 1266–1530 | `[66699, 85090)` | 18391 | `SEMANTICS.md` | 2 | `4021f5302693f0096c9150eab2d1d6d877873e4233fb7d1a5fafd1dcd9b593cd` |
| R06 | 1531–1780 | `[85090, 103149)` | 18059 | `ASSURANCE-SECURITY.md` | 1 | `3647a2ebcf8952427287a1035629d9cf8ba4840fe220df1fc720990faeff8b6c` |
| R07 | 1781–1833 | `[103149, 106364)` | 3215 | `SEMANTICS.md` | 3 | `e1d49745dc7ad72dd7c6e0927225d8c8cecd5a36ce9c88341949a68b87d05583` |
| R08 | 1834–2047 | `[106364, 121204)` | 14840 | `ASSURANCE-DATA.md` | 1 | `34302421f8dd53e78fd5e8c17e91d395ad34bd5f31930b289d0704512198dd29` |
| R09 | 2048–2150 | `[121204, 126625)` | 5421 | `EXAMPLES.md` | 1 | `40cb158493d97589914acb8d7bf53ea806dcdf6850f917f87ed9d639df64d68d` |
| R10 | 2151–2171 | `[126625, 128432)` | 1807 | `SEMANTICS.md` | 4 | `67b956c59a2a073c02d4706574d698b46ed5345af26983ba2855c2d914ac5f59` |
| R11 | 2172–2195 | `[128432, 130211)` | 1779 | `SEMANTICS.md` | 5 | `9c299ef7ebd195f05e40affa2c610968b33c102f0e93ffdd246c84c06e1b32c8` |
| R12 | 2196–2394 | `[130211, 142801)` | 12590 | `ADOPTION.md` | 1 | `2a1884b250e5284f7960c433705f9f93bfa7ec0b212d10e421536e562a44efab` |
| R13 | 2395–2405 | `[142801, 143457)` | 656 | `README.md` | 2 | `cbb0f5cb4a709ccba6516581d4b108e7ee2b18ecbc076df92a2a2987071c6f8a` |
| R14 | 2406–2547 | `[143457, 174818)` | 31361 | `CONFORMANCE.md` | 1 | `cdb96c4d25dccd001d184e42e07df8ddea0f0c00edf5056f2ce52e5e808848cb` |
| R15 | 2548–2571 | `[174818, 182027)` | 7209 | `CONFORMANCE.md` | 2 | `cf9cacc435d403b06ed40c625b1b252f53995fd2940d8fa6680258db1f6d15df` |
| R16 | 2572–2597 | `[182027, 185197)` | 3170 | `CONFORMANCE.md` | 3 | `f2b579b5350e377776963d739261158248b58d1d0b7f5c82f994d3f8cfc5eba1` |
| R17 | 2598–2608 | `[185197, 185832)` | 635 | `CONFORMANCE.md` | 4 | `ca2c1faf01fda6ae7df82070317f07810b8352a07d617dc1d140a051d81cc0f8` |
| R18 | 2609–2623 | `[185832, 188659)` | 2827 | `README.md` | 3 | `2930e610e0de7212ec998e156ae77b5ad82688a17e83a1160c59e76c5e79548d` |

## Enumerated migration wrappers

- Every content leaf except `README.md` adds one H1 title and one short status/provenance callout before copied payload.
- Leaves whose copied range ends with a structural blank separator add a non-semantic HTML migration trailer after that exact payload, preventing whitespace-only EOF diagnostics without altering copied bytes.
- `README.md` adds the document-set migration callout and reader-task map after copied source lines 1–2 (the title and its blank separator); the remainder of R01 follows unchanged.
- The old `../PLAN-FACT-JOURNAL.md` contains only compatibility headings and links; it contains no copied canonical payload.
- This manifest is new migration evidence and contains no copied canonical payload.

The parity check must independently verify every row digest, destination payload order,
source-range coverage, absence of duplicate payload slices, the original reconstructed
SHA-256, the old-anchor map, and the before/after occurrence multiset of stable IDs.
