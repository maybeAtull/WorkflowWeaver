"""One place for "now", so timestamps are always timezone-aware UTC."""

from datetime import datetime, timezone


def utc_now() -> datetime:
    # timezone.utc rather than datetime.UTC: the project supports Python 3.10.
    return datetime.now(timezone.utc)  # noqa: UP017
