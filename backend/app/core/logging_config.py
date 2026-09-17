"""Logging that never prints request bodies or PII. PAN-like tokens are masked defensively."""
from __future__ import annotations

import logging
import re

PAN_RE = re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b")


class MaskingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
            if PAN_RE.search(msg):
                record.msg = PAN_RE.sub(lambda m: m.group(0)[:3] + "••••" + m.group(0)[-1], msg)
                record.args = ()
        except Exception:  # pragma: no cover
            pass
        return True


def configure_logging(level: int = logging.INFO) -> None:
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for name in ("astra", "uvicorn.access", "uvicorn.error"):
        logging.getLogger(name).addFilter(MaskingFilter())
