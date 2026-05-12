"""Asset checks for Track B.

Asset checks run after the corresponding asset materialises and either
pass or fail the run. They are the Dagster-native pattern for data
quality validation, replacing Great Expectations for the PoC scope.
"""

import polars as pl
from dagster import AssetCheckResult, asset_check
from pyiceberg.exceptions import NoSuchTableError

from .resources import IcebergCatalogResource

NAMESPACE = "inventoryflow"


def _load_df(catalog, table_name: str) -> pl.DataFrame | None:  # type: ignore[no-untyped-def]
    try:
        table = catalog.load_table((NAMESPACE, table_name))
    except NoSuchTableError:
        return None
    df = pl.from_arrow(table.scan().to_arrow())
    if isinstance(df, pl.Series):
        df = df.to_frame()
    return df


@asset_check(asset="silver_parts", name="silver_parts_have_non_null_part_number")
def check_silver_parts_have_part_number(
    iceberg_catalog: IcebergCatalogResource,
) -> AssetCheckResult:
    """Every silver row must carry a non-null `part_number`. Catches
    parser regressions that would slip into gold and propagate to the
    serving layer.
    """
    df = _load_df(iceberg_catalog.load(), "silver_parts")
    if df is None or df.is_empty():
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


@asset_check(asset="silver_parts", name="silver_parts_unique_part_number")
def check_silver_parts_unique(
    iceberg_catalog: IcebergCatalogResource,
) -> AssetCheckResult:
    """Silver must have UNIQUE (dealer_id, part_number). Mirrors Track A's
    UNIQUE NULLS NOT DISTINCT constraint on products.
    """
    df = _load_df(iceberg_catalog.load(), "silver_parts")
    if df is None or df.is_empty():
        return AssetCheckResult(passed=True, metadata={"empty_table": True})

    duplicates = df.group_by(["_dealer_id", "part_number"]).len().filter(
        pl.col("len") > 1
    )
    return AssetCheckResult(
        passed=duplicates.height == 0,
        metadata={
            "total_rows": df.shape[0],
            "duplicate_groups": duplicates.height,
        },
    )


@asset_check(asset="silver_image_callouts", name="silver_image_callouts_row_count")
def check_silver_image_callouts_count(
    iceberg_catalog: IcebergCatalogResource,
) -> AssetCheckResult:
    """silver_image_callouts must have one row per unique image (1,586)."""
    df = _load_df(iceberg_catalog.load(), "silver_image_callouts")
    if df is None or df.is_empty():
        return AssetCheckResult(passed=False, metadata={"reason": "table_missing_or_empty"})

    target = 1586
    rows = df.shape[0]
    with_callouts = df.filter(pl.col("callout_count") > 0).shape[0]
    return AssetCheckResult(
        passed=abs(rows - target) <= 1,
        metadata={
            "total_images": rows,
            "target": target,
            "images_with_callouts": with_callouts,
            "coverage_pct": round(100 * with_callouts / max(rows, 1), 1),
        },
    )


@asset_check(asset="gold_products_mart", name="gold_fitment_is_valid_json_array")
def check_gold_fitment_shape(
    iceberg_catalog: IcebergCatalogResource,
) -> AssetCheckResult:
    """`fitment` column must be a JSON array starting with `[` and ending
    with `]`. Downstream consumers (eBay/Amazon catalog feeds) parse this
    directly without a schema-cast step.
    """
    df = _load_df(iceberg_catalog.load(), "gold_products_mart")
    if df is None or df.is_empty():
        return AssetCheckResult(passed=True, metadata={"empty_table": True})

    invalid = df.filter(
        ~(
            pl.col("fitment").str.starts_with("[")
            & pl.col("fitment").str.ends_with("]")
        )
    ).shape[0]

    return AssetCheckResult(
        passed=invalid == 0,
        metadata={"total_rows": df.shape[0], "invalid_fitment_rows": invalid},
    )


@asset_check(asset="gold_products_mart", name="gold_row_count_matches_track_a")
def check_gold_row_count_matches_track_a(
    iceberg_catalog: IcebergCatalogResource,
) -> AssetCheckResult:
    """Gold mart row count must be within ±1% of Track A's reference
    (3,938 products). This is the headline parity assertion the
    docs/TRACK_B.md §8.4 table cites.
    """
    df = _load_df(iceberg_catalog.load(), "gold_products_mart")
    if df is None:
        return AssetCheckResult(passed=False, metadata={"reason": "table_missing"})

    target = 3938
    rows = df.shape[0]
    delta_pct = abs(rows - target) / target * 100
    return AssetCheckResult(
        passed=delta_pct <= 1.0,
        metadata={
            "gold_row_count": rows,
            "track_a_reference": target,
            "delta_pct": round(delta_pct, 2),
        },
        description=(
            f"Gold row count {rows} vs Track A reference {target} "
            f"({delta_pct:.2f}% delta)"
        ),
    )
