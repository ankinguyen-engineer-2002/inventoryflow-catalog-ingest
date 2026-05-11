"""`track-b bench` — fitment-lookup latency benchmark on Iceberg via DuckDB.

Mirrors track-a-jd-native/test/benchmark/run-bench.ts. Writes results to
docs/bench/track-b-bench-results.json so COMPARISON.md can cite measured
numbers for Track B alongside Track A.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

import duckdb


log = logging.getLogger(__name__)


def _measure_fitment_query(con: duckdb.DuckDBPyConnection, iterations: int) -> dict[str, float | int]:
    """Time a fitment-lookup query repeated `iterations` times."""
    # Warm the planner.
    con.execute(
        """
        SELECT part_number FROM iceberg_warehouse.inventoryflow.gold_products_mart
        WHERE fitment LIKE '%AY70-2%'
        LIMIT 10
        """
    ).fetchall()

    samples: list[float] = []
    for _ in range(iterations):
        start = time.perf_counter()
        con.execute(
            """
            SELECT part_number, name_en
            FROM iceberg_warehouse.inventoryflow.gold_products_mart
            WHERE fitment LIKE '%AY70-2%'
            LIMIT 10
            """
        ).fetchall()
        samples.append((time.perf_counter() - start) * 1000)

    samples.sort()
    return {
        "iterations": iterations,
        "p50_ms": round(samples[len(samples) // 2], 3),
        "p95_ms": round(samples[int(len(samples) * 0.95)], 3),
        "p99_ms": round(samples[int(len(samples) * 0.99)], 3),
        "max_ms": round(samples[-1], 3),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="track-b bench")
    parser.add_argument("--queries", type=int, default=500)
    args = parser.parse_args(argv)

    con = duckdb.connect(":memory:")
    con.execute("INSTALL iceberg")
    con.execute("LOAD iceberg")
    con.execute("INSTALL httpfs")
    con.execute("LOAD httpfs")
    con.execute(
        """
        SET s3_endpoint = 'localhost:9100';
        SET s3_access_key_id = 'minioadmin';
        SET s3_secret_access_key = 'minioadmin';
        SET s3_use_ssl = false;
        SET s3_url_style = 'path';
        """
    )
    con.execute(
        """
        ATTACH 'http://localhost:8181' AS iceberg_warehouse (
            TYPE iceberg, ENDPOINT_TYPE 'rest'
        )
        """
    )

    log.info("Running benchmark: %d iterations", args.queries)
    fitment = _measure_fitment_query(con, args.queries)

    result = {
        "engine": "duckdb-on-iceberg",
        "hardware": f"{sys.platform}",
        "python_version": sys.version.split()[0],
        "fitment_query": fitment,
        "notes": "Run after `make track-b-batch` populates Iceberg gold.",
    }

    out_path = Path("../docs/bench/track-b-bench-results.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2) + "\n")

    print(json.dumps(result, indent=2))  # noqa: T201
    print(f"\nWritten to {out_path}")  # noqa: T201
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sys.exit(main())
