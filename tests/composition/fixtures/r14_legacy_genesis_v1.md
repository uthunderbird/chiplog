# Authentic legacy V1 startup bundle

`r14_legacy_genesis_v1.json.gz` was produced by the real `open_r7_runtime` and
`runtime.bootstrap` at source revision
`08465384aa9127909909de49d9a9eb560a8b3cd4`. There were no owner, journal,
materialization or clock substitutions. Only bootstrap ran; no application work.
All credentials and the journal key belong exclusively to this public test bundle.

The closed runtime yielded ten files, 150712 original bytes, compressed into
11234 bytes. SHA256 of the gzip artifact:
`187a44494e4d41286ebd5207e0811bfe5bb516fb769d0699a0ad95d3a83f5543`.
The fixture retains each file's exact bytes, relative name, mode and SHA256,
including independent journal, protected head/key, SQLite materialization,
authority commitment, broker/read stores and lock files. Capture rejected any
embedded original directory path; restoration does not rewrite authority.

To produce another genuine instance, use a separate clean checkout of the exact
producer revision, with its dependencies installed. From that checkout run:

```sh
PYTHONPATH=src /absolute/path/to/chiplog/.venv/bin/python \
  /absolute/path/to/chiplog/tests/support/r14_legacy_genesis_capture.py \
  /private/tmp/another-r14-legacy-genesis-v1.json.gz
```

The script checks producer HEAD before starting and keeps imports inside `main`
so spawned owners do not acquire diagnostic-runner imports. Random journal keys
make independently produced instances differ. Deterministic gzip serialization
does not promise identical outputs from independent bootstraps. Do not regenerate
the committed historical fixture from a later initializer or during tests.

The consumer test authenticates the complete historical journal/materialization
and V1 genesis binding through existing readers, then compares every restored
file byte before and after. This establishes preserved legacy authority; ordinary
startup refusal and early no-write version admission remain separate requirements.
