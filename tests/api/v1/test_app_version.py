"""
X-App-Version parsing (security review N5).

The header is client-controlled, so anything outside the format is reduced to
``invalid`` before it reaches a log line.
"""
import pytest
from starlette.requests import Request

from app.core.app_version import app_version


def _request(value=None) -> Request:
    headers = [] if value is None else [(b"x-app-version", value.encode("latin-1"))]
    return Request({"type": "http", "headers": headers})


@pytest.mark.parametrize(
    "value",
    ["1.0.0", "1.0.0+1", "1.2.3+45 (android)", "10.20.30+123456789 (ios)", "0.1.0 (web)"],
)
def test_well_formed_versions_are_kept(value):
    assert app_version(_request(value)) == value


def test_surrounding_spaces_are_trimmed():
    assert app_version(_request("  1.2.3+4 (web) ")) == "1.2.3+4 (web)"


def test_a_missing_header_is_none():
    assert app_version(_request()) == "none"


@pytest.mark.parametrize(
    "value",
    [
        "",
        "1.2",
        "v1.2.3",
        "1.2.3+",
        "1.2.3 (windows)",
        "1.2.3 android",
        "1.2.3\nLogin user_id=forged",
        "1.2.3+45 (android) extra",
        "1" * 5 + ".0.0",
    ],
)
def test_anything_else_is_invalid(value):
    assert app_version(_request(value)) == "invalid"
