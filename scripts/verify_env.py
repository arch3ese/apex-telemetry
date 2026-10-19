import asyncio
import logging

from apex_telemetry.config import get_settings
from apex_telemetry.core.logging import setup_logging
from apex_telemetry.db.session import ping_database

logger = logging.getLogger("apex_telemetry.verify_env")


async def main() -> None:
    settings = get_settings()
    setup_logging(log_level=settings.LOG_LEVEL, json_format=True)

    logger.info("Verifying environment configuration and database connectivity...")
    logger.info(
        "Loaded settings",
        extra={
            "project": settings.PROJECT_NAME,
            "environment": settings.ENVIRONMENT,
            "db_url": settings.DATABASE_URL.split("@")[-1],  # mask credentials
        },
    )

    try:
        health = await ping_database()
        logger.info("Database connection verification successful!", extra=health)
        print("\n--- ENVIRONMENT VERIFICATION SUCCESSFUL ---")
        print(f"PostgreSQL Status: {health['status']}")
        print(f"Database Engine:   {health['version']}")
        print("-------------------------------------------\n")
    except Exception as exc:
        logger.error("Database connection verification failed!", exc_info=exc)
        print("\n--- ENVIRONMENT VERIFICATION FAILED ---")
        print(f"Error: {exc}")
        print(
            "Make sure PostgreSQL is running via: docker compose -f docker/docker-compose.yml up -d"
        )
        print("---------------------------------------\n")
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
