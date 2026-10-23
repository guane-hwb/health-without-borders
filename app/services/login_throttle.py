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

Every attempt is counted before its password is checked (``reserve_attempt``),
so a burst of simultaneous guesses gets no more evaluations than the limit
(security review 2026-10-15, F1).

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


def _paused(wait: int) -> ApiError:
    """``429`` + ``Retry-After`` for an account that may not try yet."""
    return ApiError(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail=(
            "Too many failed sign-in attempts for this account. "
            f"Try again in {wait} seconds."
        ),
        code=LOGIN_PAUSED,
        headers={"Retry-After": str(wait)},
    )


def reserve_attempt(db: Session, email: str) -> None:
    """
    Count an attempt before its password is checked, or refuse it (``429``).

    Checking the pause first and recording the failure after bcrypt let every
    guess of a simultaneous burst be evaluated before the first failure was
    saved. Here the account's row is created or locked by one
    ``INSERT … ON CONFLICT DO UPDATE`` and held until the commit, so attempts
    on one account are counted one after another, from any instance. The
    attempt that reaches the limit is still evaluated and starts the pause;
    the next ones are refused without evaluating their password. A paused
    attempt is not counted, so it does not lengthen the pause.

    A correct password then calls :func:`clear_failures`; a wrong one needs
    nothing more, it is already counted.
    """
    key = account_key(email)
    now = datetime.now(timezone.utc)
    dialect = postgresql if db.get_bind().dialect.name == "postgresql" else sqlite
    statement = (
        dialect.insert(LoginFailure)
        .values(key=key, failures=0, first_failure_at=now, last_failure_at=now)
        # A no-op update: it only takes the row lock.
        .on_conflict_do_update(index_elements=["key"], set_={"key": key})
        .returning(
            LoginFailure.failures, LoginFailure.first_failure_at, LoginFailure.blocked_until
        )
    )
    failures, first_failure_at, blocked_until = db.execute(statement).one()

    if blocked_until is not None:
        remaining = (_as_utc(blocked_until) - now).total_seconds()
        if remaining > 0:
            db.rollback()
            raise _paused(math.ceil(remaining))

    window = timedelta(seconds=settings.LOGIN_FAILURE_WINDOW_SECONDS)
    row = db.get(LoginFailure, key, populate_existing=True)
    if now - _as_utc(first_failure_at) > window:
        # The earlier failures are too old to count: a new streak starts here.
        failures = 0
        row.first_failure_at = now
    failures += 1
    row.failures, row.last_failure_at, row.blocked_until = failures, now, None
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
