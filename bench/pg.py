"""A PostgreSQL server for tests and benchmarks.

Uses TEST_DATABASE_URL when set (CI). Otherwise starts an embedded PostgreSQL 16
with pgserver, so no Docker is needed on a laptop.
"""

from __future__ import annotations

import os
import tempfile


class NoServerError(RuntimeError):
    """No TEST_DATABASE_URL and pgserver is not installed for this Python."""


def server_url() -> tuple[str, str]:
    url = os.environ.get("TEST_DATABASE_URL")
    if url:
        return url, "provided PostgreSQL"
    try:
        import pgserver  # noqa: PLC0415 - dev dependency, not on every Python
    except ImportError as exc:
        raise NoServerError("set TEST_DATABASE_URL or install pgserver") from exc
    server = pgserver.get_server(tempfile.mkdtemp(prefix="sqllab-pg-"), cleanup_mode="stop")
    return server.get_uri(), "embedded PostgreSQL 16 (pgserver), default settings"
