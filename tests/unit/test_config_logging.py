import json
import logging

from apex_telemetry.config import Settings
from apex_telemetry.core.logging import JSONLogFormatter, correlation_id_ctx


def test_settings_load_defaults():
    settings = Settings(
        DATABASE_URL="postgresql+asyncpg://test:test@localhost:5432/test_db",
        SYNC_DATABASE_URL="postgresql+psycopg://test:test@localhost:5432/test_db",
    )
    assert settings.ENVIRONMENT == "development"
    assert settings.DEBUG is True
    assert settings.DB_POOL_SIZE == 10
    assert "test_db" in settings.DATABASE_URL


def test_json_log_formatter():
    formatter = JSONLogFormatter()
    logger = logging.getLogger("test_logger")
    record = logger.makeRecord(
        name="test_logger",
        level=logging.INFO,
        fn="test_file.py",
        lno=10,
        msg="Test telemetry message",
        args=(),
        exc_info=None,
    )
    correlation_id_ctx.set("corr-uuid-12345")
    formatted = formatter.format(record)
    parsed = json.loads(formatted)

    assert parsed["level"] == "INFO"
    assert parsed["logger"] == "test_logger"
    assert parsed["message"] == "Test telemetry message"
    assert parsed["correlation_id"] == "corr-uuid-12345"
    assert "timestamp" in parsed
