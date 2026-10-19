import json
import logging
import sys
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

# ContextVar for tracing request correlation IDs across async task boundaries
correlation_id_ctx: ContextVar[str | None] = ContextVar("correlation_id", default=None)


class JSONLogFormatter(logging.Formatter):
    """Formatter that outputs structured JSON log entries."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "correlation_id": correlation_id_ctx.get(),
        }

        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)

        # Include custom extra fields if passed
        extra_keys = set(record.__dict__.keys()) - {
            "name",
            "msg",
            "args",
            "levelname",
            "levelno",
            "pathname",
            "filename",
            "module",
            "exc_info",
            "exc_text",
            "stack_info",
            "lineno",
            "funcName",
            "created",
            "msecs",
            "relativeCreated",
            "thread",
            "threadName",
            "processName",
            "process",
            "message",
        }
        for key in extra_keys:
            log_entry[key] = getattr(record, key)

        return json.dumps(log_entry)


def setup_logging(log_level: str = "INFO", json_format: bool = True) -> None:
    """Configure the root logger with either JSON or console formatting."""
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level.upper())

    # Remove existing handlers to avoid duplicate log emissions
    for handler in list(root_logger.handlers):
        root_logger.removeHandler(handler)

    handler = logging.StreamHandler(sys.stdout)
    if json_format:
        handler.setFormatter(JSONLogFormatter())
    else:
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s [%(levelname)s] [%(name)s] [cid=%(correlation_id)s] %(message)s",
                defaults={"correlation_id": "-"},
            )
        )

    root_logger.addHandler(handler)

    # Silence overly chatty third-party loggers
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
