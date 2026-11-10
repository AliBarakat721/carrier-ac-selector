import pytest

from updater import APP_VERSION, UpdateError, is_newer_version


def test_application_version_is_semver():
    assert APP_VERSION == "2.0.0"


@pytest.mark.parametrize(
    ("candidate", "current", "expected"),
    [
        ("2.0.1", "2.0.0", True),
        ("2.1.0", "2.0.9", True),
        ("3.0.0", "2.99.99", True),
        ("2.0.0", "2.0.0", False),
        ("1.99.99", "2.0.0", False),
        ("v2.0.1", "2.0.0", True),
    ],
)
def test_version_comparison(candidate, current, expected):
    assert is_newer_version(candidate, current) is expected


@pytest.mark.parametrize("value", ["latest", "2.0", "v2", "2.0.0-beta"])
def test_rejects_unsupported_version_format(value):
    with pytest.raises(UpdateError):
        is_newer_version(value, "2.0.0")
