"""Benchmark — DuckDB-on-Iceberg fitment lookup latency.

Mirrors Track A's track-a-jd-native/test/benchmark/run-bench.ts. Writes
results to docs/bench/track-b-bench-results.json so COMPARISON.md can
cite measured numbers for Track B alongside Track A.

Prerequisite: `iceberg_roundtrip.py` has populated the gold table.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

import duckdb
from pyiceberg.catalog import load_catalog

log = logging.getLogger(__name__)


def get_metadata_location() -> str:
    catalog = load_catalog(
        "rest",
        **{
            "type": "rest",
            "uri": "http://localhost:8181",
            "warehouse": "s3://catalog-warehouse/",
            "s3.endpoint": "http://localhost:9100",
            "s3.access-key-id": "minioadmin",
            "s3.secret-access-key": "minioadmin",
            "s3.path-style-access": "true",
        },
    )
    table = catalog.load_table(("inventoryflow", "gold_products_mart"))
    return table.metadata_location


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bench_fitment")
    parser.add_argument("--queries", type=int, default=500)
    args = parser.parse_args(argv)

    metadata = get_metadata_location()
    log.info("Iceberg metadata: %s", metadata)

    con = duckdb.connect(":memory:")
    con.execute("INSTALL iceberg")
    con.execute("LOAD iceberg")
    con.execute("INSTALL httpfs")
    con.execute("LOAD httpfs")
    con.execute("""
        CREATE SECRET (TYPE s3,
            KEY_ID 'minioadmin',
            SECRET 'minioadmin',
            ENDPOINT 'localhost:9100',
            URL_STYLE 'path',
            USE_SSL false)
    """)

    query = f"""
    SELECT part_number, name_en
    FROM iceberg_scan('{metadata}')
    WHERE fitment LIKE '%AY70-2%'
    LIMIT 10
    """

    # Warm the planner.
    con.execute(query).fetchall()

    samples: list[float] = []
    for _ in range(args.queries):
        start = time.perf_counter()
        con.execute(query).fetchall()
        samples.append((time.perf_counter() - start) * 1000)

    samples.sort()
    result = {
        "engine": "duckdb-on-iceberg",
        "hardware": sys.platform,
        "python_version": sys.version.split()[0],
        "fitment_query": {
            "iterations": args.queries,
            "p50_ms": round(samples[len(samples) // 2], 3),
            "p95_ms": round(samples[int(len(samples) * 0.95)], 3),
            "p99_ms": round(samples[int(len(samples) * 0.99)], 3),
            "max_ms": round(samples[-1], 3),
        },
        "notes": "Real measurement against Iceberg gold mart on MinIO via "
                 "docker-compose stack. Run after `iceberg_roundtrip.py` "
                 "populates the table.",
    }

    out_path = Path("../docs/bench/track-b-bench-results.json").resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2) + "\n")

    print(json.dumps(result, indent=2))  # noqa: T201
    print(f"\nWritten to {out_path}")  # noqa: T201
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    sys.exit(main())
