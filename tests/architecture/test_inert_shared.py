from __future__ import annotations

import hashlib
import inspect
import json
from dataclasses import fields, is_dataclass
from typing import Any, cast, get_type_hints

import chiplog.domain_primitives as primitives
from chiplog.verification.inert_shared import verify_inert_shared_source
from tests.support.inert_shared import PACKAGE

EXPECTED = {
    "TenantId": (("value", str),),
    "PrincipalId": (("value", str),),
    "PermissionScope": (("value", str),),
    "RecordId": (("tenant_id", primitives.TenantId), ("value", str)),
    "RecordTypeId": (("namespace", str), ("name", str)),
    "SchemaId": (("namespace", str), ("name", str), ("version", int)),
    "CodecVersion": (("value", int),),
    "CanonicalizationVersion": (("value", int),),
    "OwnerTag": (("value", str),),
    "Fingerprint": (("algorithm", str), ("digest", bytes)),
    "ProducingVersions": (
        ("schema_id", primitives.SchemaId),
        ("codec", primitives.CodecVersion),
        ("canonicalization", primitives.CanonicalizationVersion),
    ),
    "CanonicalBytes": (
        ("payload", bytes),
        ("producing_versions", primitives.ProducingVersions),
    ),
    "PreservedBytes": (
        ("original", bytes),
        ("producing_versions", primitives.ProducingVersions),
    ),
}

EXPECTED_SIGNATURE_DIGESTS = {
    "CanonicalBytes": "1459574602979b9b5db93303511f5a3d32695681db77991e2c25a4760b9540ab",
    "CanonicalizationVersion": "1a7f5c3dc11e5620c30ff31bf7ffe25f9473232befde1426ea640683d04f4e0f",
    "CodecVersion": "903aea15fc8239dd53a98aa9684466e8326ccbc028b75feb8325ac1ea65b3d21",
    "Fingerprint": "b02c25ce3b12fa1a80196d2ede6ee102490ac2bf7851544f396b19dba64c0241",
    "OwnerTag": "5bb2d0bdb7b617ce22dcc567a8b53cf65fd14e6969c324a8bb16992e012b908e",
    "PermissionScope": "cf2ed746ed68053d4d4f65e940f9461f9d058115d80a0930092d64069f77f4be",
    "PreservedBytes": "2714dbfb479cde63940be384c303921653bb800833226b49d6b4e3805dafb4c1",
    "PrincipalId": "48997894ae1949e99ec66735c7ba70e66360fd1b4109892ad7275c7cbfbb0939",
    "ProducingVersions": "7d5cbbb9cd23e8af6e9f5fb742b1c4efc48b5000981a270889a139a10f1188e0",
    "RecordId": "d1f431ca1ea4fd61dbe4f6bd2c851be925cd87bbde2ca6b5df8e14c1d31bd2df",
    "RecordTypeId": "54bc5285559a0a4f97b545e1b352ba530dc656c0cfd2e930713a64c3cf199f13",
    "SchemaId": "90257a4ba569edd67fb2aec2cb9b4e647dcb1358c1b1d6f8bb2670e01c4830c9",
    "TenantId": "859d063d97d9c379e78daa9d0794803deef7f42e9a1225f7b85981b9ac3fdfd5",
}


def test_v2_public_exports_and_signatures_match_frozen_contract_exactly() -> None:
    assert set(primitives.__all__) == set(EXPECTED)
    assert len(primitives.__all__) == len(EXPECTED)
    for name, expected_fields in EXPECTED.items():
        value_type = cast(type[Any], getattr(primitives, name))
        assert is_dataclass(value_type)
        assert cast(Any, value_type).__dataclass_params__.frozen
        hints = get_type_hints(value_type)
        observed_fields = tuple((field.name, hints[field.name]) for field in fields(value_type))
        assert observed_fields == expected_fields
        parameters = tuple(inspect.signature(value_type).parameters.values())
        assert all(parameter.default is inspect.Parameter.empty for parameter in parameters)
        assert all(
            parameter.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD for parameter in parameters
        )


def test_v2_canonical_signature_digests_match_parallel_freeze() -> None:
    observed: dict[str, str] = {}
    for export, expected_fields in EXPECTED.items():
        signature = {
            "export": export,
            "fields": [[name, annotation.__name__] for name, annotation in expected_fields],
        }
        encoded = json.dumps(signature, sort_keys=True, separators=(",", ":")).encode()
        observed[export] = hashlib.sha256(encoded).hexdigest()

    assert observed == EXPECTED_SIGNATURE_DIGESTS


def test_v2_real_shared_package_is_closed_and_policy_free() -> None:
    verify_inert_shared_source(PACKAGE)
