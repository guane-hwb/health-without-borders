"""
Alembic environment: the app's own engine and models.

Run from the command line (``alembic upgrade head``) or by the service at
startup (``app.db.migrations.upgrade_to_head``), which passes its connection
in ``config.attributes["connection"]`` and keeps the app's logging setup.
"""
from logging.config import fileConfig

from alembic import context

import app.db.models  # noqa: F401 - registers every table on Base.metadata
from app.db.base import Base
from app.db.session import engine

config = context.config

if config.config_file_name and not config.attributes.get("skip_logging_config"):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _run(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        # SQLite (tests, local dev) cannot ALTER most things in place.
        render_as_batch=connection.dialect.name == "sqlite",
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connection = config.attributes.get("connection")
    if connection is not None:
        _run(connection)
        return
    with engine.connect() as connection:
        _run(connection)


if context.is_offline_mode():
    raise SystemExit("Offline (SQL script) mode is not supported; run against a database.")
run_migrations_online()
