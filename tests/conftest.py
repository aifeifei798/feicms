"""Shared test environment: every test runs against a throwaway SQLite database
and a throwaway image directory, so tests never touch data/ or data/images/."""

from __future__ import annotations

import os
import tempfile

_TMP = tempfile.mkdtemp(prefix="feicms-test-")

os.environ.setdefault("FEICMS_DB", f"sqlite+aiosqlite:///{_TMP}/test.db")
os.environ.setdefault("FEICMS_IMAGE_DIR", f"{_TMP}/images")
os.environ.setdefault("FEICMS_LOCK_FILE", f"{_TMP}/collect.lock")
os.environ.setdefault("FEICMS_STATUS_FILE", f"{_TMP}/collect_status.json")
os.environ.setdefault("FEICMS_AUTO_COLLECT", "false")
os.environ.setdefault("FEICMS_IMAGE_AUTO", "false")
# Authentication is off unless a test explicitly turns it on.
os.environ.setdefault("FEICMS_ADMIN_USER", "")
os.environ.setdefault("FEICMS_ADMIN_PASSWORD", "")
