"""
The time zone the platform reads naive local times in.

Current app builds send clinical times with their offset
(``lib/src/core/utils/clinical_time.dart``); builds before it sent local
wall-clock time without one (``DateTime.now().toIso8601String()``), and
records keep those values. A value without an offset is read in the reporting
zone, Colombia by default, so statistics and FHIR agree on what it means.
"""

import logging
from datetime import timedelta, timezone, tzinfo

from app.core.config import settings

logger = logging.getLogger(__name__)


def reporting_timezone() -> tzinfo:
    """
    Return the reporting time zone.

    Prefers the configured IANA zone. Slim container images sometimes ship
    without the tz database; rather than crash or silently report in UTC (which
    would push the last five hours of every Colombian month into the next one),
    fall back to the configured fixed offset and say so in the logs.
    """
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(settings.STATS_TIMEZONE)
    except Exception:  # noqa: BLE001 - any tz-db failure must degrade, not crash
        offset_hours = settings.STATS_TIMEZONE_FALLBACK_OFFSET_HOURS
        logger.warning(
            "Time zone %r unavailable; using fixed UTC%+d offset instead. "
            "Month boundaries will drift wherever daylight saving applies.",
            settings.STATS_TIMEZONE,
            offset_hours,
        )
        return timezone(timedelta(hours=offset_hours))
