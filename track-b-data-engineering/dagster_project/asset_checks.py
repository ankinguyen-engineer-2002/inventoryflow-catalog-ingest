"""Asset checks for Track B.

Replaces Great Expectations for the PoC scope. Asset checks run after the
asset materialises and either pass or fail the run. They are the
recommended pattern in Dagster 1.5+ for data-quality validation.
"""

from __future__ import annotations

import polars as pl
from dagster import AssetCheckResult, asset_check

from .resources import IcebergCatalogResource


@asset_check(asset="silver_parts", name="silver_parts_have_non_null_part_number")
def check_silver_parts_have_part_number(
    iceberg_catalog: IcebergCatalogResource,
) -> AssetCheckResult:
    """Every silver row must carry a non-null `part_number`. Catches
    parser regressions that would slip into gold and propagate to the
    PostgreSQL serving layer.
    """
    catalog = iceberg_catalog.load()
    table = catalog.load_table(("inventoryflow", "silver_parts"))
    df = pl.from_arrow(table.scan().to_arrow())
    if isinstance(df, pl.Series):
        df = df.to_frame()

    if df.is_empty():
        return AssetCheckResult(passed=True, metadata={"empty_table": True})

    null_count = df.filter(pl.col("part_number").is_null()).shape[0]
    return AssetCheckResult(
        passed=null_count == 0,
        metadata={
            "total_rows": df.shape[0],
            "null_part_number_rows": null_count,
        },
        description=(
            f"silver_parts has {null_count} rows with NULL part_number "
            f"out of {df.shape[0]} total"
        ),
    )


@asset_check(asset="gold_products_mart", name="gold_fitment_is_valid_json_array")
def check_gold_fitment_shape(
    iceberg_catalog: IcebergCatalogResource,
) -> AssetCheckResult:
    """`fitment` column must be a JSON array starting with `[` and ending
    with `]`. Downstream consumers (eBay/Amazon catalog feeds) parse this
    directly without a schema-cast step.
    """
    catalog = iceberg_catalog.load()
    table = catalog.load_table(("inventoryflow", "gold_products_mart"))
    df = pl.from_arrow(table.scan().to_arrow())
    if isinstance(df, pl.Series):
        df = df.to_frame()

    if df.is_empty():
        return AssetCheckResult(passed=True, metadata={"empty_table": True})

    invalid = df.filter(
        ~(pl.col("fitment").str.starts_with("[") & pl.col("fitment").str.ends_with("]"))
    ).shape[0]

    return AssetCheckResult(
        passed=invalid == 0,
        metadata={"total_rows": df.shape[0], "invalid_fitment_rows": invalid},
    )
