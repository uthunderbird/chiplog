import chiplog


def test_package_exposes_entrypoint() -> None:
    assert callable(chiplog.main)
