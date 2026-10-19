import argparse
import asyncio
import statistics
import time
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from apex_telemetry.db.session import async_session_factory
from apex_telemetry.models import TestSession
from apex_telemetry.schemas.ingestion import TelemetryBatchCreate, TelemetryReadingCreate
from apex_telemetry.schemas.query import OffsetPaginationParams, TelemetryQueryFilter
from apex_telemetry.services.ingestion_service import IngestionService
from apex_telemetry.services.query_service import TelemetryQueryService


async def _create_benchmark_session(db: AsyncSession) -> uuid.UUID:
    session_id = uuid.uuid4()
    session = TestSession(
        id=session_id,
        session_slug=f"benchmark-{session_id.hex[:8]}",
        vehicle_id="BENCHMARK-MULE-01",
        track_name="Nardò Ring High-Speed Bowl",
    )
    db.add(session)
    await db.commit()
    return session_id


def _generate_benchmark_batch(
    batch_size: int, worker_id: int, batch_idx: int
) -> TelemetryBatchCreate:
    base_time = datetime(2026, 10, 19, 16, 0, 0, tzinfo=UTC)
    readings = [
        TelemetryReadingCreate(
            channel_name="wheel_speed_fl",
            recorded_at=base_time
            + timedelta(milliseconds=worker_id * 100000 + batch_idx * 1000 + i),
            value=120.0 + (i % 50),
            unit="km/h",
            quality_flag=1,
        )
        for i in range(batch_size)
    ]
    return TelemetryBatchCreate(readings=readings)


async def run_benchmark(
    concurrency: int = 8,
    batches_per_worker: int = 5,
    batch_size: int = 100,
) -> None:
    print("\n" + "=" * 65)
    print("   APEXTELEMETRY PERFORMANCE & CONCURRENCY BENCHMARK")
    print("=" * 65)
    print(f"Concurrency Workers:   {concurrency}")
    print(f"Batches per Worker:    {batches_per_worker}")
    print(f"Batch Size (records):  {batch_size}")
    total_expected_records = concurrency * batches_per_worker * batch_size
    print(f"Total Target Records:  {total_expected_records}")
    print("-" * 65)

    # 1. Setup Session
    async with async_session_factory() as db:
        session_id = await _create_benchmark_session(db)

    # 2. Benchmark Ingestion Throughput
    ingest_latencies_ms: list[float] = []

    async def ingestion_worker(worker_id: int) -> list[str]:
        keys = []
        async with async_session_factory() as worker_db:
            service = IngestionService(worker_db)
            for b_idx in range(batches_per_worker):
                key = f"bench-w{worker_id}-b{b_idx}-{uuid.uuid4().hex[:6]}"
                keys.append(key)
                batch = _generate_benchmark_batch(batch_size, worker_id, b_idx)

                t0 = time.perf_counter()
                await service.ingest_batch(session_id, key, batch)
                t1 = time.perf_counter()
                ingest_latencies_ms.append((t1 - t0) * 1000.0)
        return keys

    print("[1/3] Benchmarking Concurrent Batch Ingestion...")
    start_ingest = time.perf_counter()
    worker_tasks = [ingestion_worker(w) for w in range(concurrency)]
    all_keys = await asyncio.gather(*worker_tasks)
    total_ingest_time = time.perf_counter() - start_ingest

    records_per_sec = total_expected_records / total_ingest_time
    reqs_per_sec = (concurrency * batches_per_worker) / total_ingest_time

    # 3. Benchmark Idempotent Replays
    print("[2/3] Benchmarking Idempotent Replay Verification...")
    replay_latencies_ms: list[float] = []

    async def replay_worker(worker_id: int, keys: list[str]) -> None:
        async with async_session_factory() as worker_db:
            service = IngestionService(worker_db)
            for b_idx, key in enumerate(keys):
                batch = _generate_benchmark_batch(batch_size, worker_id, b_idx)
                t0 = time.perf_counter()
                resp = await service.ingest_batch(session_id, key, batch)
                t1 = time.perf_counter()
                assert resp.is_idempotent_replay is True
                replay_latencies_ms.append((t1 - t0) * 1000.0)

    start_replay = time.perf_counter()
    replay_tasks = [replay_worker(w, all_keys[w]) for w in range(concurrency)]
    await asyncio.gather(*replay_tasks)
    time.perf_counter() - start_replay

    # 4. Benchmark Query Slicing Latency
    print("[3/3] Benchmarking Concurrent Filtered Queries...")
    query_latencies_ms: list[float] = []

    async def query_worker() -> None:
        async with async_session_factory() as query_db:
            service = TelemetryQueryService(query_db)
            filters = TelemetryQueryFilter(channel_name="wheel_speed_fl", min_value=130.0)
            pagination = OffsetPaginationParams(limit=50, offset=0)

            for _ in range(10):
                t0 = time.perf_counter()
                await service.query_records_offset(session_id, filters, pagination)
                t1 = time.perf_counter()
                query_latencies_ms.append((t1 - t0) * 1000.0)

    query_tasks = [query_worker() for _ in range(concurrency)]
    await asyncio.gather(*query_tasks)

    # 5. Output Statistical Results
    def percentiles(data: list[float]) -> tuple[float, float, float]:
        data_sorted = sorted(data)
        p50 = statistics.median(data_sorted)
        p95 = data_sorted[int(len(data_sorted) * 0.95)]
        p99 = data_sorted[int(len(data_sorted) * 0.99)]
        return p50, p95, p99

    ing_p50, ing_p95, ing_p99 = percentiles(ingest_latencies_ms)
    rep_p50, rep_p95, rep_p99 = percentiles(replay_latencies_ms)
    qry_p50, qry_p95, qry_p99 = percentiles(query_latencies_ms)

    print("\n" + "=" * 65)
    print("                    BENCHMARK RESULTS SUMMARY")
    print("=" * 65)
    print(f"Batch Ingestion Throughput:  {records_per_sec:,.1f} records/sec")
    print(f"Request Throughput:          {reqs_per_sec:,.1f} requests/sec")
    print(f"Total Ingestion Wall Time:   {total_ingest_time:.3f} s")
    print("-" * 65)
    print("INGESTION LATENCY (New Commits):")
    print(f"   p50 (median):  {ing_p50:.2f} ms")
    print(f"   p95:           {ing_p95:.2f} ms")
    print(f"   p99:           {ing_p99:.2f} ms")
    print("-" * 65)
    print("IDEMPOTENT REPLAY LATENCY (Cache Hit / Skip):")
    print(f"   p50 (median):  {rep_p50:.2f} ms  ({ing_p50 / max(0.01, rep_p50):.1f}x speedup)")
    print(f"   p95:           {rep_p95:.2f} ms")
    print(f"   p99:           {rep_p99:.2f} ms")
    print("-" * 65)
    print("FILTERED QUERY LATENCY (PostgreSQL Index Scans):")
    print(f"   p50 (median):  {qry_p50:.2f} ms")
    print(f"   p95:           {qry_p95:.2f} ms")
    print(f"   p99:           {qry_p99:.2f} ms")
    print("=" * 65 + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ApexTelemetry Load & Concurrency Benchmark")
    parser.add_argument(
        "--concurrency", type=int, default=8, help="Number of concurrent client workers"
    )
    parser.add_argument("--batches", type=int, default=5, help="Batches submitted per worker")
    parser.add_argument(
        "--batch-size", type=int, default=100, help="Number of sensor readings per batch"
    )
    args = parser.parse_args()

    asyncio.run(
        run_benchmark(
            concurrency=args.concurrency,
            batches_per_worker=args.batches,
            batch_size=args.batch_size,
        )
    )
