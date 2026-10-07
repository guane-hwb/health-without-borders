"""
Per-account limit on failed sign-ins, kept in PostgreSQL.

Audit finding (2026-10-02): be-oct26-rate-limit-por-instancia-y-solo-por-ip. The
login limit is counted per IP and in each Cloud Run instance's memory, so
guessing one account's password from many addresses, or across many instances,
met no limit at all. This counts failures per account in the database, which
every instance shares, without adding a service (no Redis).

After ``LOGIN_FAILURES_BEFORE_DELAY`` failures within
``LOGIN_FAILURE_WINDOW_SECONDS``, the account is paused for
``LOGIN_DELAY_BASE_SECONDS``, doubling with each further failure up to
``LOGIN_DELAY_MAX_SECONDS``. A pause rather than a lockout: someone who knows
an email can delay that user's sign-in, but never lock them out for good. A
successful sign-in, or an administrator's password reset, clears the count.

The account is identified by an HMAC of the normalized email, so the table
holds no email addresses (including the ones of accounts that do not exist),
and a paused account looks the same whether it exists or not.
"""

import hashlib
import hmac
import logging
import math
from datetime import datetime, timedelta, timezone

from fastapi import status
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import LOGIN_PAUSED, ApiError
from app.db.models import LoginFailure
from app.schemas.user import normalize_email

logger = logging.getLogger(__name__)


def account_key(email: str) -> str:
    return hmac.new(
        settings.SECRET_KEY.encode(), normalize_email(email).encode(), hashlib.sha256
    ).hexdigest()


def _as_utc(value: datetime) -> datetime:
    """SQLite hands timestamps back without their zone; they are UTC."""
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def seconds_paused(db: Session, email: str) -> int:
    """Seconds until the account may try again; 0 when it is not paused."""
    row = db.get(LoginFailure, account_key(email))
    if row is None or row.blocked_until is None:
        return 0
    remaining = (_as_utc(row.blocked_until) - datetime.now(timezone.utc)).total_seconds()
    return max(0, math.ceil(remaining))


def ensure_login_not_paused(db: Session, email: str) -> None:
    """Refuse an attempt while the account is paused (``429`` + ``Retry-After``)."""
    wait = seconds_paused(db, email)
    if wait:
        raise ApiError(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                "Too many failed sign-in attempts for this account. "
                f"Try again in {wait} seconds."
            ),
            code=LOGIN_PAUSED,
            headers={"Retry-After": str(wait)},
        )


def record_failure(db: Session, email: str) -> None:
    """
    Count a failed attempt and pause the account once there are too many.

    The count is incremented by one ``INSERT … ON CONFLICT DO UPDATE``, so
    simultaneous failures from several instances are all counted.
    """
    key = account_key(email)
    now = datetime.now(timezone.utc)
    dialect = postgresql if db.get_bind().dialect.name == "postgresql" else sqlite
    statement = (
        dialect.insert(LoginFailure)
        .values(key=key, failures=1, first_failure_at=now, last_failure_at=now)
        .on_conflict_do_update(
            index_elements=["key"],
            set_={"failures": LoginFailure.failures + 1, "last_failure_at": now},
        )
        .returning(LoginFailure.failures, LoginFailure.first_failure_at)
    )
    failures, first_failure_at = db.execute(statement).one()

    window = timedelta(seconds=settings.LOGIN_FAILURE_WINDOW_SECONDS)
    row = db.get(LoginFailure, key, populate_existing=True)
    if now - _as_utc(first_failure_at) > window:
        # The earlier failures are too old to count: a new streak starts here.
        failures = 1
        row.failures, row.first_failure_at, row.blocked_until = 1, now, None
    over = failures - settings.LOGIN_FAILURES_BEFORE_DELAY
    if over >= 0:
        pause = min(
            settings.LOGIN_DELAY_BASE_SECONDS * 2 ** over, settings.LOGIN_DELAY_MAX_SECONDS
        )
        row.blocked_until = now + timedelta(seconds=pause)
        logger.warning(
            "Sign-in paused for %ds after %d failed attempts account=%s",
            pause,
            failures,
            key[:8],
        )

    # Forget streaks that ended long ago, so the table stays small.
    db.query(LoginFailure).filter(
        LoginFailure.last_failure_at < now - window,
        (LoginFailure.blocked_until.is_(None)) | (LoginFailure.blocked_until < now),
    ).delete(synchronize_session=False)
    db.commit()


def clear_failures(db: Session, email: str) -> None:
    """The right password was given (or an administrator reset it)."""
    deleted = (
        db.query(LoginFailure)
        .filter(LoginFailure.key == account_key(email))
        .delete(synchronize_session=False)
    )
    if deleted:
        db.commit()
