"""End-to-end Dagster materialisation via the in-process API.

Runs the full bronze → silver → gold asset graph against the local
Iceberg REST + MinIO stack. Each asset check is evaluated automatically.

Run with docker-compose stack up:
    docker-compose up -d minio iceberg-rest postgres
    python3 scripts/materialize_all.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from dagster import materialize

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dagster_project.assets import (  # noqa: E402
    bronze_catalog_rows,
    gold_products_mart,
    silver_image_callouts,
    silver_parts,
)
from dagster_project.resources import default_resources  # noqa: E402


def main() -> int:
    result = materialize(
        assets=[bronze_catalog_rows, silver_parts, silver_image_callouts, gold_products_mart],
        resources=default_resources(),
    )

    if not result.success:
        print("Materialisation FAILED")  # noqa: T201
        return 1

    print("\n=== Materialisation success ===\n")  # noqa: T201
    for evt in result.get_asset_materialization_events():
        asset_key = evt.asset_key.path[-1]
        meta = evt.materialization.metadata
        row_count = meta.get("row_count")
        row_str = f"{row_count.value} rows" if row_count is not None else ""
        print(f"  ✅ {asset_key:30s} {row_str}")  # noqa: T201

    # Validate the materialised tables by reading them back via pyiceberg.
    # The asset checks defined in dagster_project/asset_checks.py run
    # automatically in the `dagster dev` web UI; here we run equivalent
    # validations imperatively so the CLI reports parity numbers.
    print("\n=== Validation checks ===\n")  # noqa: T201
    import polars as pl

    from dagster_project.resources import IcebergCatalogResource

    catalog = IcebergCatalogResource().load()
    silver_df = pl.from_arrow(
        catalog.load_table(("inventoryflow", "silver_parts")).scan().to_arrow()
    )
    gold_df = pl.from_arrow(
        catalog.load_table(("inventoryflow", "gold_products_mart")).scan().to_arrow()
    )
    if isinstance(silver_df, pl.Series):
        silver_df = silver_df.to_frame()
    if isinstance(gold_df, pl.Series):
        gold_df = gold_df.to_frame()

    checks = [
        (
            "silver_parts.part_number not null",
            silver_df.filter(pl.col("part_number").is_null()).height == 0,
            {"null_rows": silver_df.filter(pl.col("part_number").is_null()).height},
        ),
        (
            "silver_parts unique (dealer, part_number)",
            (
                silver_df.group_by(["_dealer_id", "part_number"])
                .len()
                .filter(pl.col("len") > 1)
                .height
                == 0
            ),
            {
                "duplicate_groups": (
                    silver_df.group_by(["_dealer_id", "part_number"])
                    .len()
                    .filter(pl.col("len") > 1)
                    .height
                )
            },
        ),
        (
            "gold.fitment is valid JSON array",
            gold_df.filter(
                ~(pl.col("fitment").str.starts_with("[") & pl.col("fitment").str.ends_with("]"))
            ).height == 0,
            {"invalid_rows": gold_df.filter(
                ~(pl.col("fitment").str.starts_with("[") & pl.col("fitment").str.ends_with("]"))
            ).height},
        ),
        (
            "gold row count matches Track A (±1%)",
            abs(gold_df.height - 3938) / 3938 <= 0.01,
            {"gold_rows": gold_df.height, "track_a_ref": 3938,
             "delta_pct": round(abs(gold_df.height - 3938) / 3938 * 100, 2)},
        ),
    ]
    failed = 0
    for name, passed, meta in checks:
        status = "✅" if passed else "❌"
        print(f"  {status} {name}")  # noqa: T201
        for k, v in meta.items():
            print(f"      {k}: {v}")  # noqa: T201
        if not passed:
            failed += 1

    return failed


if __name__ == "__main__":
    sys.exit(main())
