import logging
import logging.config
import re
import sys
import traceback

from app.core.config import settings

# Path segment after /patients/scan/ is a bracelet UID, which the project
# classifies as PHI (docs/infrastructure/security.md). Access logs print the
# raw request path, so it is masked there.
_SCAN_UID_IN_PATH = re.compile(r"(/patients/scan/)[^\s/?\"]+")


class PhiSafeFormatter(logging.Formatter):
    """
    Formatter that never writes exception messages, only their types and frames.

    Database driver errors carry PHI in their text: SQLAlchemy appends the
    statement parameters and psycopg2 appends ``DETAIL: Key (col)=(value)``.
    Any ``logger.exception`` in the app — and the traceback uvicorn prints for
    an unhandled error — goes through this formatter, so a leak cannot depend
    on every call site remembering to scrub its own exception.
    """

    def format(self, record: logging.LogRecord) -> str:
        # A handler formatted earlier with the stock formatter may have cached
        # the full traceback text on the record; always rebuild it here.
        record.exc_text = None
        return super().format(record)

    def formatException(self, ei) -> str:  # noqa: N802 - logging API name
        _, exc, _ = ei
        blocks = []
        seen = set()
        while exc is not None and id(exc) not in seen:
            seen.add(id(exc))
            frames = "".join(traceback.format_tb(exc.__traceback__))
            blocks.append(
                f"Traceback (most recent call last):\n{frames}"
                f"{type(exc).__module__}.{type(exc).__qualname__}: <message redacted>"
            )
            exc = exc.__cause__ or exc.__context__
        # Innermost cause first, as Python prints chained exceptions.
        return "\n\nDuring handling, another exception occurred:\n\n".join(
            reversed(blocks)
        )


class ScanUidAccessFilter(logging.Filter):
    """Mask the bracelet UID that /patients/scan/{uid} puts in access logs."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(
                _SCAN_UID_IN_PATH.sub(r"\1***", a) if isinstance(a, str) else a
                for a in record.args
            )
        if isinstance(record.msg, str):
            record.msg = _SCAN_UID_IN_PATH.sub(r"\1***", record.msg)
        return True


def setup_logging():
    """
    Configures the logging system for the application.
    
    Strategies:
    - Uses a Dictionary Configuration schema (standard in Python).
    - Outputs to STDOUT (Console), which is best practice for Docker/Cloud Run.
    - Sets specific levels for third-party libraries (uvicorn, sqlalchemy) to reduce noise.
    """
    
    # Define the log format
    # Example output: 2026-01-29 10:00:00 - app.services.patient - INFO - Patient created
    log_format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    
    # Determine Log Level from Settings (default to INFO)
    # You can add LOG_LEVEL="DEBUG" in your .env later to see more details
    log_level = "DEBUG" if settings.DEBUG else "INFO"

    logging_config = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "standard": {
                "()": PhiSafeFormatter,
                "fmt": log_format,
                "datefmt": "%Y-%m-%d %H:%M:%S",
            },
        },
        "filters": {
            "scan_uid": {"()": ScanUidAccessFilter},
        },
        "handlers": {
            "console": {
                "level": log_level,
                "class": "logging.StreamHandler",
                "formatter": "standard",
                "stream": sys.stdout, # Important for Docker/GCP
            },
        },
        "loggers": {
            # Root Logger (Default)
            "": {
                "handlers": ["console"],
                "level": log_level,
                "propagate": True,
            },
            # App specific logger (Everything inside app/...)
            "app": {
                "handlers": ["console"],
                "level": log_level,
                "propagate": False,
            },
            # Uvicorn (The server) - Keep it at INFO to avoid noise
            "uvicorn": {
                "handlers": ["console"],
                "level": "INFO",
                "propagate": False,
            },
            "uvicorn.access": {
                "handlers": ["console"],
                "filters": ["scan_uid"],
                "level": "INFO",
                "propagate": False,
            },
            # SQLAlchemy (Database) - Set to WARN to hide SQL queries in production
            # Change to INFO or DEBUG if you want to see raw SQL queries
            "sqlalchemy.engine": {
                "handlers": ["console"],
                "level": "WARNING",
                "propagate": False,
            },
        }
    }

    logging.config.dictConfig(logging_config)