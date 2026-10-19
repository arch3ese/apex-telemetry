import argparse
import asyncio
import logging
import math
import random
import uuid
from datetime import UTC, datetime, timedelta

from apex_telemetry.config import get_settings
from apex_telemetry.core.logging import setup_logging
from apex_telemetry.db.session import async_session_factory
from apex_telemetry.models import SessionMetadata, TestSession
from apex_telemetry.schemas.ingestion import TelemetryBatchCreate, TelemetryReadingCreate
from apex_telemetry.services.ingestion_service import IngestionService

logger = logging.getLogger("apex_telemetry.seed_data")

TRACK_CONFIGS = [
    {
        "slug": "silverstone-gt3-dry-quali",
        "vehicle_id": "PORSCHE-992-GT3R-01",
        "track_name": "Silverstone Grand Prix Circuit",
        "driver_name": "K. Estre",
        "metadata": [
            ("tire_compound", "Michelin-Pilot-Sport-S9M", "string"),
            ("ambient_temp_c", "21.5", "number"),
            ("track_temp_c", "34.2", "number"),
            ("fuel_load_kg", "45.0", "number"),
            ("brake_bias_front_pct", "54.5", "number"),
        ],
    },
    {
        "slug": "spa-prototype-wet-shakedown",
        "vehicle_id": "FERRARI-499P-HYBRID-03",
        "track_name": "Circuit de Spa-Francorchamps",
        "driver_name": "A. Pier Guidi",
        "metadata": [
            ("tire_compound", "Michelin-Rain-FullWet", "string"),
            ("ambient_temp_c", "14.0", "number"),
            ("track_temp_c", "16.8", "number"),
            ("aero_downforce_package", "High-Downforce", "string"),
            ("hybrid_deployment_mode", "Qualifying-Boost", "string"),
        ],
    },
    {
        "slug": "nurburgring-chassis-validation",
        "vehicle_id": "BMW-M4-GT4-TEST-07",
        "track_name": "Nürburgring Nordschleife",
        "driver_name": "P. Eng",
        "metadata": [
            ("tire_compound", "Pirelli-PZero-DHD2", "string"),
            ("ambient_temp_c", "18.2", "number"),
            ("track_temp_c", "26.0", "number"),
            ("damper_rebound_clicks_fl", "8", "number"),
            ("anti_roll_bar_rear", "Stiff-Pos3", "string"),
        ],
    },
]


def generate_synthetic_lap_telemetry(
    base_time: datetime,
    num_samples: int = 200,
    sample_rate_hz: float = 10.0,
) -> list[TelemetryReadingCreate]:
    """Generate realistic kinematic telemetry simulating straightaways, braking zones, and corners."""
    readings: list[TelemetryReadingCreate] = []
    dt = timedelta(seconds=1.0 / sample_rate_hz)
    t = base_time

    # Physical vehicle model state variables
    speed_kmh = 80.0

    for i in range(num_samples):
        # Synthetic track phase cycling:
        # 0.0 - 0.4: Straightaway acceleration
        # 0.4 - 0.6: Hard braking zone
        # 0.6 - 0.8: Corner entry & apex
        # 0.8 - 1.0: Corner exit
        cycle_phase = (i % 60) / 60.0

        if cycle_phase < 0.4:  # Acceleration on straight
            accel_x = random.uniform(0.3, 0.8)
            speed_kmh = min(290.0, speed_kmh + (accel_x * 9.81 * 3.6 * (1.0 / sample_rate_hz)))
            brake_pressure = 0.0
            steer_angle = random.gauss(0.0, 0.5)
            accel_y = random.gauss(0.0, 0.05)
            yaw_rate = random.gauss(0.0, 0.2)

        elif cycle_phase < 0.6:  # Heavy braking zone into turn
            brake_pressure = random.uniform(70.0, 140.0)
            accel_x = random.uniform(-2.8, -1.8)
            speed_kmh = max(65.0, speed_kmh + (accel_x * 9.81 * 3.6 * (1.0 / sample_rate_hz)))
            steer_angle = random.gauss(0.0, 1.0)
            accel_y = random.gauss(0.0, 0.1)
            yaw_rate = random.gauss(0.0, 0.5)

        elif cycle_phase < 0.8:  # Mid-corner apex
            brake_pressure = max(0.0, random.uniform(5.0, 25.0))
            steer_angle = random.uniform(15.0, 32.0) * (1 if (i // 60) % 2 == 0 else -1)
            accel_y = (speed_kmh / 3.6) ** 2 / 80.0 / 9.81 * math.copysign(1, steer_angle)
            accel_y = max(-3.8, min(3.8, accel_y))
            accel_x = random.uniform(-0.2, 0.1)
            yaw_rate = (speed_kmh / 3.6) / 80.0 * (180.0 / math.pi) * math.copysign(1, steer_angle)

        else:  # Corner exit
            brake_pressure = 0.0
            steer_angle = random.uniform(2.0, 10.0) * (1 if (i // 60) % 2 == 0 else -1)
            accel_x = random.uniform(0.4, 0.9)
            speed_kmh = min(260.0, speed_kmh + (accel_x * 9.81 * 3.6 * (1.0 / sample_rate_hz)))
            accel_y = random.uniform(0.4, 1.2) * math.copysign(1, steer_angle)
            yaw_rate = random.uniform(1.0, 4.0) * math.copysign(1, steer_angle)

        # Append multi-channel synchronous telemetry observations
        readings.append(
            TelemetryReadingCreate(
                channel_name="wheel_speed_fl",
                recorded_at=t,
                value=round(speed_kmh, 2),
                unit="km/h",
                quality_flag=1,
            )
        )
        readings.append(
            TelemetryReadingCreate(
                channel_name="brake_pressure_bar",
                recorded_at=t,
                value=round(brake_pressure, 2),
                unit="bar",
                quality_flag=1,
            )
        )
        readings.append(
            TelemetryReadingCreate(
                channel_name="lateral_accel_g",
                recorded_at=t,
                value=round(accel_y, 3),
                unit="g",
                quality_flag=1,
            )
        )
        readings.append(
            TelemetryReadingCreate(
                channel_name="longitudinal_accel_g",
                recorded_at=t,
                value=round(accel_x, 3),
                unit="g",
                quality_flag=1,
            )
        )
        readings.append(
            TelemetryReadingCreate(
                channel_name="steering_angle_deg",
                recorded_at=t,
                value=round(steer_angle, 2),
                unit="deg",
                quality_flag=1,
            )
        )
        readings.append(
            TelemetryReadingCreate(
                channel_name="yaw_rate_deg_s",
                recorded_at=t,
                value=round(yaw_rate, 2),
                unit="deg/s",
                quality_flag=1,
            )
        )

        t += dt

    return readings


async def seed_database(num_samples_per_session: int = 150) -> None:
    """Populate database with deterministic synthetic test sessions and realistic telemetry."""
    random.seed(42)  # Deterministic seed for reproducible testing
    get_settings()
    setup_logging(log_level="INFO", json_format=False)

    logger.info("Starting deterministic database seed...")
    total_records_inserted = 0

    async with async_session_factory() as db:
        ingestion_service = IngestionService(db)

        for cfg in TRACK_CONFIGS:
            session_id = uuid.uuid4()
            session = TestSession(
                id=session_id,
                session_slug=cfg["slug"],
                vehicle_id=cfg["vehicle_id"],
                track_name=cfg["track_name"],
                driver_name=cfg["driver_name"],
                status="active",
            )
            db.add(session)

            for key, val, vtype in cfg["metadata"]:
                meta = SessionMetadata(
                    session_id=session_id,
                    key=key,
                    value=val,
                    value_type=vtype,
                )
                db.add(meta)

            await db.commit()
            logger.info(
                "Created TestSession", extra={"slug": cfg["slug"], "track": cfg["track_name"]}
            )

            # Ingest synthetic lap readings in batches
            lap_start = datetime(2026, 10, 19, 14, 0, 0, tzinfo=UTC)
            all_readings = generate_synthetic_lap_telemetry(
                base_time=lap_start,
                num_samples=num_samples_per_session,
                sample_rate_hz=10.0,
            )

            # Ingest in batches of 300 readings
            chunk_size = 300
            for chunk_idx in range(0, len(all_readings), chunk_size):
                chunk = all_readings[chunk_idx : chunk_idx + chunk_size]
                batch_payload = TelemetryBatchCreate(readings=chunk)
                idempotency_key = f"seed-{cfg['slug']}-chunk-{chunk_idx // chunk_size:03d}"

                job_resp = await ingestion_service.ingest_batch(
                    session_id=session_id,
                    idempotency_key=idempotency_key,
                    batch=batch_payload,
                )
                total_records_inserted += job_resp.record_count

            logger.info(f"Ingested {len(all_readings)} channels for session {cfg['slug']}")

    print("\n=======================================================")
    print("   DETERMINISTIC SEEDING COMPLETED SUCCESSFULLY        ")
    print(f"   Sessions Created:  {len(TRACK_CONFIGS)}")
    print(f"   Total Observations Ingested: {total_records_inserted}")
    print("=======================================================\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Deterministic Telemetry Data Seeder")
    parser.add_argument(
        "--samples",
        type=int,
        default=150,
        help="Number of time steps per session (each step produces 6 synchronized channels)",
    )
    args = parser.parse_args()
    asyncio.run(seed_database(num_samples_per_session=args.samples))
