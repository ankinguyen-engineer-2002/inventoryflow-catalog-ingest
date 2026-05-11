#!/usr/bin/env python3
"""DuckDB-on-Iceberg analytical query demonstration.

Shows the Track B serving path for ad-hoc analytics: DuckDB reads
directly from Iceberg tables through the Iceberg REST catalog, with
no separate analytical database required.

This is the same pattern that scales to petabyte-class lakehouses;
DuckDB makes it accessible at small scale.

Usage:
    poetry run python notebooks/duckdb_iceberg_demo.py
"""

from __future__ import annotations

import duckdb


def main() -> None:
    con = duckdb.connect(":memory:")

    # Configure DuckDB to talk to MinIO + Iceberg REST catalog.
    con.execute("INSTALL iceberg")
    con.execute("LOAD iceberg")
    con.execute("INSTALL httpfs")
    con.execute("LOAD httpfs")
    con.execute("""
        SET s3_endpoint = 'localhost:9100';
        SET s3_access_key_id = 'minioadmin';
        SET s3_secret_access_key = 'minioadmin';
        SET s3_use_ssl = false;
        SET s3_url_style = 'path';
    """)

    con.execute("""
        ATTACH 'http://localhost:8181' AS iceberg_warehouse (
            TYPE iceberg,
            ENDPOINT_TYPE 'rest'
        )
    """)

    print("\n=== Bronze row counts per sheet ===")
    rows = con.execute("""
        SELECT _source_sheet, COUNT(*) AS rows
        FROM iceberg_warehouse.inventoryflow.bronze_catalog_rows
        GROUP BY _source_sheet
        ORDER BY rows DESC
        LIMIT 10
    """).fetchall()
    for r in rows:
        print(f"  {r[0]:40s} {r[1]:>6}")

    print("\n=== Silver parts sample ===")
    rows = con.execute("""
        SELECT part_number, name_en
        FROM iceberg_warehouse.inventoryflow.silver_parts
        WHERE part_number IS NOT NULL
        LIMIT 5
    """).fetchall()
    for r in rows:
        print(f"  {r[0]:25s} {r[1] or '(null)'}")

    print("\n=== Gold mart count ===")
    (count,) = con.execute("""
        SELECT COUNT(*) FROM iceberg_warehouse.inventoryflow.gold_products_mart
    """).fetchone()
    print(f"  {count} rows in gold mart")


if __name__ == "__main__":
    main()
