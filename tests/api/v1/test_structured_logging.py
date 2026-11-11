"""
Logs on Cloud Run: severity, one entry per record, and no exception message.

Backend audit 2026-11-04:
- be-nov26-traceback-de-arranque-sin-redactar (PoC-5): a failed startup reached
  the logs as traceback *text* (Starlette's format_exc, logged by uvicorn),
  with the database driver's message whole.
- be-nov26-logs-de-aplicacion-sin-severidad (G17): plain-text lines arrive in
  Cloud Logging as DEFAULT, and a traceback as one entry per line.
"""
import json
import logging
import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.errors import StartupCheckError
from app.core.logging import (
    JsonPhiSafeFormatter,
    PhiSafeFormatter,
    TracebackTextFilter,
    log_format_name,
    redact_traceback_text,
    setup_logging,
)
from app.main import StartupError, app

# Built at runtime, so no line of this file holds the marker the logs must not.
MARKER = "sint" + "-marker-0042"
REPO = Path(__file__).resolve().parents[3]

TRACEBACK = f"""Traceback (most recent call last):
  File "/app/app/db/migrations.py", line 61, in run_migrations_at_startup
    upgrade_to_head(engine)
sqlalchemy.exc.IntegrityError: (psycopg2.errors.UniqueViolation) could not create unique index
DETAIL:  Key (document_type, document_number)=(TI, {MARKER}) is duplicated.
[SQL: CREATE UNIQUE INDEX ix ON patients (document_type, document_number)]

The above exception was the direct cause of the following exception:

Traceback (most recent call last):
  File "/usr/local/lib/python3.11/site-packages/starlette/routing.py", line 694, in lifespan
    async with self.lifespan_context(app) as maybe_state:
RuntimeError: {MARKER}
"""


def _record(msg="hello %s", args=("world",), level=logging.WARNING, exc_info=None):
    return logging.LogRecord("app.test", level, __file__, 1, msg, args, exc_info)


# ---------------------------------------------------------------------------
# Traceback text
# ---------------------------------------------------------------------------


def test_a_traceback_as_text_keeps_frames_and_types_but_no_message():
    redacted = redact_traceback_text(TRACEBACK)

    assert MARKER not in redacted
    assert "DETAIL" not in redacted and "[SQL:" not in redacted
    assert "sqlalchemy.exc.IntegrityError: <message redacted>" in redacted
    assert "RuntimeError: <message redacted>" in redacted
    assert 'File "/app/app/db/migrations.py", line 61' in redacted
    assert "\n\nThe above exception was the direct cause" in redacted


def test_text_before_the_traceback_stays_and_text_without_one_is_untouched():
    assert redact_traceback_text("Exception in ASGI application\n" + TRACEBACK).startswith(
        "Exception in ASGI application\nTraceback"
    )
    assert redact_traceback_text("Started server process") == "Started server process"


def test_a_message_line_that_is_not_an_exception_type_is_still_redacted():
    text = "Traceback (most recent call last):\n  File \"x\", line 1\n(TI, SINT) is duplicated"

    assert redact_traceback_text(text).endswith("Exception: <message redacted>")


def test_the_filter_redacts_a_traceback_logged_as_the_message():
    record = _record("%s", (TRACEBACK,), level=logging.ERROR)

    assert TracebackTextFilter().filter(record) is True

    assert MARKER not in record.getMessage()
    assert record.args == ()


def test_the_filter_leaves_other_messages_and_their_args_alone():
    record = _record()

    TracebackTextFilter().filter(record)

    assert record.args == ("world",)


# ---------------------------------------------------------------------------
# JSON lines with severity
# ---------------------------------------------------------------------------


def test_cloud_run_log_lines_are_json_with_severity():
    line = JsonPhiSafeFormatter().format(_record(level=logging.ERROR))

    entry = json.loads(line)
    assert entry["severity"] == "ERROR"
    assert entry["message"] == "hello world"
    assert entry["logger"] == "app.test"
    assert entry["time"].endswith("+00:00")
    assert "exception" not in entry


def test_an_exception_is_one_entry_with_its_stack_redacted():
    try:
        raise ValueError(f"DETAIL: Key (doc)=({MARKER})")
    except ValueError:
        line = JsonPhiSafeFormatter().format(_record(level=logging.ERROR, exc_info=sys.exc_info()))

    assert "\n" not in line  # one Cloud Logging entry, not one per traceback line
    entry = json.loads(line)
    assert MARKER not in line
    assert entry["exception"].endswith("builtins.ValueError: <message redacted>")


def test_a_stack_info_goes_in_its_own_field():
    record = _record()
    record.stack_info = "Stack (most recent call last):\n  File \"x\", line 1"

    assert json.loads(JsonPhiSafeFormatter().format(record))["stack"].startswith("Stack")


def test_json_only_on_cloud_run(monkeypatch):
    monkeypatch.delenv("K_SERVICE", raising=False)
    assert log_format_name() == "standard"
    monkeypatch.setenv("K_SERVICE", "hwb-backend-dev")
    assert log_format_name() == "json"


def test_setup_logging_wires_json_and_the_traceback_filter_on_cloud_run(monkeypatch):
    monkeypatch.setenv("K_SERVICE", "hwb-backend-dev")
    try:
        setup_logging()
        for name in ("app", "uvicorn", "uvicorn.access", ""):
            handler = logging.getLogger(name).handlers[0]
            assert isinstance(handler.formatter, JsonPhiSafeFormatter), name
            assert any(isinstance(f, TracebackTextFilter) for f in handler.filters), name
    finally:
        monkeypatch.delenv("K_SERVICE")
        setup_logging()
    handler = logging.getLogger("app").handlers[0]
    assert type(handler.formatter) is PhiSafeFormatter


# ---------------------------------------------------------------------------
# A failed startup step
# ---------------------------------------------------------------------------


@pytest.fixture
def startup_steps(monkeypatch):
    for step in ("report_weak_secret_key_at_startup", "run_migrations_at_startup",
                 "report_schema_drift_at_startup", "prepare_nfc_keyring_at_startup"):
        monkeypatch.setattr(f"app.main.{step}", lambda: None)
    return monkeypatch


def test_a_failed_step_logs_its_name_and_type_and_stops_without_the_message(
    startup_steps, app_log
):
    def fail():
        raise RuntimeError(f"secret {MARKER}")

    startup_steps.setattr("app.main.run_migrations_at_startup", fail)

    with pytest.raises(StartupError) as raised:
        with TestClient(app):
            pass

    assert str(raised.value) == "Startup failed at migrations."
    assert raised.value.__suppress_context__  # Starlette's text cannot show it
    logged = [r for r in app_log.records if r.getMessage().startswith("Startup failed")]
    assert [r.getMessage() for r in logged] == ["Startup failed at migrations: RuntimeError"]
    assert MARKER not in PhiSafeFormatter().format(logged[0])


def test_a_startup_check_keeps_its_message_for_the_operator(startup_steps, app_log):
    def fail():
        raise StartupCheckError("The NFC keyring table is empty. Restore nfc_keys.")

    startup_steps.setattr("app.main.prepare_nfc_keyring_at_startup", fail)

    with pytest.raises(StartupError):
        with TestClient(app):
            pass

    assert "Startup failed at NFC keyring: The NFC keyring table is empty. Restore nfc_keys." in [
        r.getMessage() for r in app_log.records
    ]


@pytest.mark.parametrize("k_service", [None, "hwb-backend-dev"])
def test_uvicorn_logs_no_exception_message_when_startup_fails(k_service):
    """PoC-5 with the real server: the database host carries the marker, so
    the driver's message would name it."""
    env = {
        **os.environ,
        "DATABASE_URL": f"postgresql://hwb:x@{MARKER}.invalid:5432/hwb_test",
        "RUN_MIGRATIONS_ON_STARTUP": "true",
        "SECRET_KEY": "startup_failure_test_secret_key_0123456789abcdef",
        "FHIR_BACKEND": "noop",
        "LLM_BACKEND": "noop",
        "NFC_KEK": "",
        "NFC_MASTER_KEY": "",
        "NFC_CURRENT_KEY_VERSION": "0",
    }
    env.pop("K_SERVICE", None)
    if k_service:
        env["K_SERVICE"] = k_service

    result = subprocess.run(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "0"],
        cwd=REPO, env=env, capture_output=True, text=True, timeout=120,
    )

    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert MARKER not in output
    assert "Startup failed at migrations: OperationalError" in output
    if k_service:
        entries = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
        failed = [e for e in entries if e["message"].startswith("Startup failed at")]
        assert failed and failed[0]["severity"] == "ERROR"
        assert failed[0]["exception"].endswith("<message redacted>")
