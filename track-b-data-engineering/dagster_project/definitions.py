"""Dagster Definitions — the entry point for `dagster dev`.

Bundles assets, asset checks, and resources into the single object that
Dagster's webserver consumes.
"""

from __future__ import annotations

from dagster import Definitions

from .asset_checks import (
    check_gold_fitment_shape,
    check_gold_row_count_matches_track_a,
    check_silver_parts_have_part_number,
    check_silver_parts_unique,
)
from .assets import bronze_catalog_rows, gold_products_mart, silver_parts
from .resources import default_resources

defs = Definitions(
    assets=[
        bronze_catalog_rows,
        silver_parts,
        gold_products_mart,
    ],
    asset_checks=[
        check_silver_parts_have_part_number,
        check_silver_parts_unique,
        check_gold_fitment_shape,
        check_gold_row_count_matches_track_a,
    ],
    resources=default_resources(),
)
