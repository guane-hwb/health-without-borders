"""
A short or published SECRET_KEY is reported at startup, without the key.

Audit finding (2026-10-02): be-oct26-secret-key-sin-longitud-minima. A warning,
not a refusal to start: the deployed key cannot be checked from the repository.
"""
import pytest

from app.core.config import settings
from app.core.security import report_weak_secret_key_at_startup

EXAMPLE = "super_secret_development_key_change_me_in_production"


@pytest.mark.parametrize(("key", "expected"), [
    (EXAMPLE, "example value"),
    ("corta-24-bytes-0123456789", "shorter than 32 bytes"),
    ("a" * 64, None),
])
def test_weak_keys_are_reported_without_the_key(monkeypatch, app_log, key, expected):
    monkeypatch.setattr(settings, "SECRET_KEY", key)

    report_weak_secret_key_at_startup()

    messages = [r.getMessage() for r in app_log.records if "SECRET_KEY" in r.getMessage()]
    if expected is None:
        assert messages == []
    else:
        assert len(messages) == 1 and expected in messages[0]
        assert key not in messages[0]


def test_startup_reports_it(monkeypatch):
    from fastapi.testclient import TestClient

    from app import main

    calls = []
    monkeypatch.setattr(main, "report_weak_secret_key_at_startup", lambda: calls.append(1))
    monkeypatch.setattr(main, "run_migrations_at_startup", lambda: None)
    monkeypatch.setattr(main, "report_schema_drift_at_startup", lambda: None)
    monkeypatch.setattr(main, "prepare_nfc_keyring_at_startup", lambda: None)

    with TestClient(main.app):
        pass

    assert calls == [1]
