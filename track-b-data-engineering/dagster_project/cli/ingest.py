"""`track-b ingest <xlsx>` — materialises bronze → silver → gold via Dagster.

Mirrors track-a-jd-native/src/cli/ingest.ts. Single command runs the full
medallion pipeline against the supplied source xlsx.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import os

from dagster import AssetSelection, materialize

from ..assets import bronze_catalog_rows, gold_products_mart, silver_parts
from ..resources import default_resources


log = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="track-b ingest",
        description="Run the Track B medallion pipeline end-to-end.",
    )
    parser.add_argument("xlsx_path", help="Path to source xlsx (defaults to shared/sample-data/example.xlsx)")
    parser.add_argument("--dry-run", action="store_true", help="Materialise bronze only; skip silver and gold")
    parser.add_argument("--dealer-id", default=None, help="Override the default demo dealer UUID")
    args = parser.parse_args(argv)

    source = Path(args.xlsx_path).resolve()
    if not source.exists():
        log.error("xlsx not found: %s", source)
        return 1

    os.environ["SOURCE_XLSX_PATH"] = str(source)
    if args.dealer_id:
        os.environ["DEMO_DEALER_ID"] = args.dealer_id

    log.info("Starting ingest: source=%s dry_run=%s", source, args.dry_run)

    if args.dry_run:
        assets = [bronze_catalog_rows]
        selection: AssetSelection | None = AssetSelection.assets("bronze_catalog_rows")
    else:
        assets = [bronze_catalog_rows, silver_parts, gold_products_mart]
        selection = None

    result = materialize(
        assets=assets,
        selection=selection,
        resources=default_resources(),
    )

    if not result.success:
        log.error("Materialisation failed")
        return 2

    log.info("Materialisation succeeded")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    sys.exit(main())
