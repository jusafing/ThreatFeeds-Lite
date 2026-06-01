"""
Logging configuration for ThreatFeeds Lite.

Sets up two output channels:
  - stdout StreamHandler  (all levels, same as before)
  - logs/app.log          (RotatingFileHandler, all levels)
  - logs/audit.log        (RotatingFileHandler, INFO+, backend.audit logger only)

Call setup_logging() once at application startup (main.py).
"""
from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

_FMT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_DATE_FMT = "%Y-%m-%dT%H:%M:%S"
_MAX_BYTES = 10 * 1024 * 1024  # 10 MB
_BACKUP_COUNT = 5


def setup_logging(log_dir: Path) -> None:
    """
    Configure root logger and the backend.audit named logger.

    Args:
        log_dir: Directory where log files are written.  Created if absent.
    """
    log_dir.mkdir(parents=True, exist_ok=True)

    formatter = logging.Formatter(_FMT, datefmt=_DATE_FMT)

    # ── Root logger ──────────────────────────────────────────────────────────
    root = logging.getLogger()
    root.setLevel(logging.INFO)

    # Remove any handlers already attached (e.g. from a previous basicConfig call)
    root.handlers.clear()

    # stdout
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    root.addHandler(stream_handler)

    # app.log — all INFO+ messages
    app_handler = logging.handlers.RotatingFileHandler(
        log_dir / "app.log",
        maxBytes=_MAX_BYTES,
        backupCount=_BACKUP_COUNT,
        encoding="utf-8",
    )
    app_handler.setFormatter(formatter)
    root.addHandler(app_handler)

    # ── Audit logger ─────────────────────────────────────────────────────────
    audit_logger = logging.getLogger("backend.audit")
    audit_logger.setLevel(logging.DEBUG)  # DEBUG emitted only if root allows it
    audit_logger.propagate = True         # flows through root handlers above

    # audit.log — dedicated file for structured ingestion events (INFO+)
    audit_handler = logging.handlers.RotatingFileHandler(
        log_dir / "audit.log",
        maxBytes=_MAX_BYTES,
        backupCount=_BACKUP_COUNT,
        encoding="utf-8",
    )
    audit_handler.setLevel(logging.INFO)
    audit_handler.setFormatter(formatter)
    audit_logger.addHandler(audit_handler)
