import logging
import logging.config
import sys
from app.core.config import settings

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
                "format": log_format,
                "datefmt": "%Y-%m-%d %H:%M:%S",
            },
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