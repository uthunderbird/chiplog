import chiplog  # type: ignore[import-untyped]


def test_package_exposes_entrypoint() -> None:
    assert callable(chiplog.main)
