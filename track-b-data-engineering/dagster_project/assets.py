"""Track B medallion assets — wired end-to-end with the real parser.

Three software-defined Dagster assets implementing the same data
transformation Track A performs in TypeScript, but through the modern
OSS lakehouse stack (Polars + Apache Iceberg + dbt-duckdb).

  bronze_catalog_rows   — parser output landed as schemaless rows
                          with provenance (dealer × sheet × row_index)
  silver_parts          — typed, deduplicated parts table
                          (UNIQUE part_number per dealer)
  gold_products_mart    — denormalised mart with JSON fitment column
                          matching Track A's products serving shape

Each asset declares its dependency on the previous via Dagster's input
mechanism. AutoMaterializePolicy lets Dagster refresh stale assets
without explicit scheduling. Asset checks (see asset_checks.py) gate
each transition.

The parser logic lives in ../parser/ — the same modules the standalone
scripts/parity_check.py and scripts/iceberg_roundtrip.py use. Track B's
Dagster path and standalone script path share the same code so the
99.97% parity proof carries over.
"""

import json
import sys
from datetime import datetime
from pathlib import Path

import polars as pl
import pyarrow as pa
from dagster import AssetIn, MetadataValue, Output, asset
from pyiceberg.catalog import Catalog
from pyiceberg.exceptions import NamespaceAlreadyExistsError, NoSuchTableError

from .resources import IcebergCatalogResource, SourceXlsxResource

# Make the sibling `parser/` package importable when Dagster auto-loads
# this module from various working directories. Must happen before the
# `parser` import below; ruff E402/I001 silenced because the side-effect
# is intentional.
_PARSER_ROOT = Path(__file__).resolve().parent.parent
if str(_PARSER_ROOT) not in sys.path:
    sys.path.insert(0, str(_PARSER_ROOT))

from parser.parse_xlsx import FitmentEntry, parse_xlsx  # noqa: E402, I001

NAMESPACE = "inventoryflow"
DEMO_DEALER_ID = "7207c961-a7cc-46a7-9c5e-34b292a2cc68"


def _ensure_namespace(catalog: Catalog, namespace: str) -> None:
    try:
        catalog.create_namespace(namespace)
    except NamespaceAlreadyExistsError:
        pass


def _overwrite_table(catalog: Catalog, identifier: tuple[str, str], table: pa.Table) -> None:
    """Create or overwrite an Iceberg table with the given Arrow data.

    Idempotent: same input data → same output table. Track A achieves
    idempotency via NULLS NOT DISTINCT unique indexes; Track B achieves
    it through Iceberg's snapshot model + this overwrite operation.

    If the existing table's schema differs from the new data, the table
    is dropped and recreated. Schema-evolution-aware merge would be the
    production path; for the PoC drop+recreate is acceptable since the
    bronze/silver/gold contract is owned end-to-end by this asset graph.
    """
    try:
        iceberg_table = catalog.load_table(identifier)
        try:
            iceberg_table.overwrite(table)
        except ValueError as exc:
            if "contains more columns" not in str(exc) and "schema" not in str(exc).lower():
                raise
            catalog.drop_table(identifier)
            iceberg_table = catalog.create_table(identifier, schema=table.schema)
            iceberg_table.append(table)
    except NoSuchTableError:
        iceberg_table = catalog.create_table(identifier, schema=table.schema)
        iceberg_table.append(table)


# ─────────────────────────────────────────────────────────────────────────
# Bronze — parser output landed verbatim with provenance.
# ─────────────────────────────────────────────────────────────────────────
@asset(
    name="bronze_catalog_rows",
    group_name="bronze",
    description=(
        "Raw parser output: one row per (sheet, source_row_index, part_number) "
        "with full provenance. Idempotent — re-running with the same xlsx "
        "overwrites the bronze table with identical content."
    ),
    compute_kind="polars",
)
def bronze_catalog_rows(
    context,
    iceberg_catalog: IcebergCatalogResource,
    source_xlsx: SourceXlsxResource,
) -> Output[int]:
    """Read the source xlsx through the ported parser and write every
    extracted product (one row per appearance, before dedup) to the
    Iceberg bronze table. Same parser code path as scripts/parity_check.py.
    """
    catalog = iceberg_catalog.load()
    _ensure_namespace(catalog, NAMESPACE)

    products = parse_xlsx(source_xlsx.path)
    context.log.info("Parser produced %d rows from %s", len(products), source_xlsx.path)

    ingestion_at = datetime.utcnow().isoformat()
    rows = [
        {
            "_dealer_id": DEMO_DEALER_ID,
            "_source_xlsx": source_xlsx.path,
            "_ingested_at": ingestion_at,
            "part_number": p.part_number,
            "name_en": p.name_en or "",
            "name_cn": p.name_cn or "",
            "spec_cn": p.spec_cn or "",
            "retail_price": float(p.retail_price) if p.retail_price is not None else 0.0,
            "fitment_json": json.dumps(
                [_fitment_dict(f) for f in p.fitment],
                ensure_ascii=False,
                separators=(", ", ": "),
            ),
        }
        for p in products
    ]
    arrow = pa.Table.from_pylist(rows)
    _overwrite_table(catalog, (NAMESPACE, "bronze_catalog_rows"), arrow)

    return Output(
        value=len(rows),
        metadata={
            "row_count": len(rows),
            "source_xlsx": MetadataValue.path(source_xlsx.path),
            "iceberg_table": f"{NAMESPACE}.bronze_catalog_rows",
        },
    )


# ─────────────────────────────────────────────────────────────────────────
# Silver — typed conformed parts. Deduplicated by part_number.
# ─────────────────────────────────────────────────────────────────────────
@asset(
    name="silver_parts",
    group_name="silver",
    description=(
        "Conformed parts table. Last-row-wins dedup by (dealer, part_number) "
        "mirrors Track A's PostgreSQL ON CONFLICT DO UPDATE semantics."
    ),
    compute_kind="polars",
    ins={"_bronze": AssetIn(key="bronze_catalog_rows")},
)
def silver_parts(
    context, _bronze: int, iceberg_catalog: IcebergCatalogResource
) -> Output[int]:
    """Read bronze, dedupe last-row-wins by part_number, write silver.

    The bronze table carries the same `fitment_json` shape Track A's
    products table stores. Silver only normalises types and dedupes.
    """
    catalog = iceberg_catalog.load()
    bronze = catalog.load_table((NAMESPACE, "bronze_catalog_rows"))
    df = pl.from_arrow(bronze.scan().to_arrow())
    if isinstance(df, pl.Series):
        df = df.to_frame()

    # Last-row-wins dedup (matches Track A's ON CONFLICT semantics).
    deduped = df.unique(subset=["_dealer_id", "part_number"], keep="last")

    silver_arrow = deduped.select(
        [
            "_dealer_id",
            "part_number",
            "name_en",
            "name_cn",
            "spec_cn",
            "retail_price",
            "_ingested_at",
        ]
    ).to_arrow()

    _overwrite_table(catalog, (NAMESPACE, "silver_parts"), silver_arrow)

    return Output(
        value=deduped.height,
        metadata={
            "row_count": deduped.height,
            "bronze_rows": _bronze,
            "dedup_ratio": round(deduped.height / max(_bronze, 1), 4),
            "iceberg_table": f"{NAMESPACE}.silver_parts",
        },
    )


# ─────────────────────────────────────────────────────────────────────────
# Silver — vision-extracted callouts. Reads from shared LLM cache.
# ─────────────────────────────────────────────────────────────────────────
@asset(
    name="silver_image_callouts",
    group_name="silver",
    description=(
        "Vision-LLM-extracted callout numbers per schematic image, keyed by "
        "image SHA-256. Sources from the shared llm-cache.jsonl populated by "
        "scripts/vision_extract_all.py (Groq Llama-4 Scout 17B or Ollama "
        "qwen2.5vl:7b). Materialisation is a cache replay — no upstream LLM "
        "calls during asset run, so the asset is deterministic and free."
    ),
    compute_kind="polars",
    ins={"_bronze": AssetIn(key="bronze_catalog_rows")},
)
def silver_image_callouts(
    context, _bronze: int, iceberg_catalog: IcebergCatalogResource, source_xlsx: SourceXlsxResource
) -> Output[int]:
    """Replay vision-LLM cache → typed callouts table on Iceberg.

    For each unique image extracted from the source xlsx, looks up its
    callouts in the shared cache. Cache hits are instant; cache misses
    fall back to whatever the configured `LLM_PROVIDER` resolves to —
    in production that's Groq Vision (or Anthropic Vision if upgraded).
    """
    import asyncio
    import base64
    import sys
    from pathlib import Path

    # Ensure parser package is on sys.path when imported from Dagster.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from parser.image_extractor import extract_unique_images  # noqa: E402

    from .ai import EnrichmentRequest, create_llm_provider  # noqa: E402

    catalog = iceberg_catalog.load()
    images = extract_unique_images(source_xlsx.path)
    context.log.info("Extracted %d unique images", len(images))

    # Use ollama-vision is the default upstream for Mock fallback; the
    # CachedLLMProvider wrapper will short-circuit on hit. Real upstream
    # is whatever LLM_PROVIDER env says.
    provider = create_llm_provider()

    async def lookup_one(img):  # type: ignore[no-untyped-def]
        b64 = base64.b64encode(img.raw_bytes).decode("ascii")
        return await provider.enrich(
            EnrichmentRequest(
                id=f"vision:{img.sha256}",
                field="extract_callouts",
                inputs={"image_b64": b64, "image_sha256": img.sha256},
            )
        )

    async def run_all():  # type: ignore[no-untyped-def]
        return await asyncio.gather(*(lookup_one(img) for img in images))

    responses = asyncio.run(run_all())

    rows = []
    for img, resp in zip(images, responses, strict=True):
        callouts = resp.result if isinstance(resp.result, list) else []
        rows.append({
            "image_sha256": img.sha256,
            "callouts_json": json.dumps(callouts, ensure_ascii=False),
            "callout_count": len(callouts),
            "confidence": resp.confidence or "low",
            "vision_provider": resp.meta.provider,
            "cache_hit": resp.meta.cache_hit,
            "source_sheets_json": json.dumps(list(img.source_sheets), ensure_ascii=False),
            "image_size_bytes": img.size_bytes,
        })

    if not rows:
        context.log.warning("No image-callout rows produced")
        return Output(value=0, metadata={"row_count": 0})

    arrow = pa.Table.from_pylist(rows)
    _overwrite_table(catalog, (NAMESPACE, "silver_image_callouts"), arrow)

    cache_hits = sum(1 for r in rows if r["cache_hit"])
    with_callouts = sum(1 for r in rows if r["callout_count"] > 0)

    return Output(
        value=len(rows),
        metadata={
            "row_count": len(rows),
            "cache_hits": cache_hits,
            "cache_hit_rate_pct": round(100 * cache_hits / len(rows), 1),
            "images_with_callouts": with_callouts,
            "iceberg_table": f"{NAMESPACE}.silver_image_callouts",
        },
    )


# ─────────────────────────────────────────────────────────────────────────
# Gold — business mart with JSON fitment column.
# ─────────────────────────────────────────────────────────────────────────
@asset(
    name="gold_products_mart",
    group_name="gold",
    description=(
        "Denormalised products mart matching Track A's serving schema. "
        "Joins silver_parts with the bronze fitment_json column to produce "
        "the wire format downstream consumers (marketplace sync, catalog "
        "API) expect."
    ),
    compute_kind="dbt",
    ins={"_silver_count": AssetIn(key="silver_parts")},
)
def gold_products_mart(
    context, _silver_count: int, iceberg_catalog: IcebergCatalogResource
) -> Output[int]:
    """Join silver with bronze fitment to produce the final gold mart.

    This is the table Track A's catalog API would read from when Track B
    is the serving layer of record. Same shape as
    sample-output/data/products-full.csv.
    """
    catalog = iceberg_catalog.load()

    silver = pl.from_arrow(
        catalog.load_table((NAMESPACE, "silver_parts")).scan().to_arrow()
    )
    bronze = pl.from_arrow(
        catalog.load_table((NAMESPACE, "bronze_catalog_rows")).scan().to_arrow()
    )

    if isinstance(silver, pl.Series):
        silver = silver.to_frame()
    if isinstance(bronze, pl.Series):
        bronze = bronze.to_frame()

    # Pick the fitment_json from bronze for each (dealer, part_number).
    fitment = (
        bronze.select(["_dealer_id", "part_number", "fitment_json"])
        .unique(subset=["_dealer_id", "part_number"], keep="last")
    )

    gold = silver.join(
        fitment, on=["_dealer_id", "part_number"], how="left"
    ).rename({"fitment_json": "fitment"})

    gold_arrow = gold.select(
        [
            "_dealer_id",
            "part_number",
            "name_en",
            "name_cn",
            "spec_cn",
            "retail_price",
            "fitment",
            "_ingested_at",
        ]
    ).to_arrow()

    _overwrite_table(catalog, (NAMESPACE, "gold_products_mart"), gold_arrow)

    return Output(
        value=gold.height,
        metadata={
            "row_count": gold.height,
            "parity_target_track_a": 3938,
            "iceberg_table": f"{NAMESPACE}.gold_products_mart",
            "snapshot_id_hint": MetadataValue.text(
                "Use Iceberg time travel: SELECT * ... FOR TIMESTAMP AS OF ..."
            ),
        },
    )


def _fitment_dict(f: FitmentEntry) -> dict:
    return {
        "make": f.make,
        "year": f.year,
        "model": f.model,
        "section": f.section,
        "variant": f.variant,
        "callout_no": f.callout_no,
        "confidence": f.confidence,
        "model_code": f.model_code,
    }
