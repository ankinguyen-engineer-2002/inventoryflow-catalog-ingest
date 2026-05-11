"""`track-b enrich --mode audit` — LLM cross-validation pass.

Mirrors track-a-jd-native/src/cli/enrich.ts. Reads the gold products
mart from Iceberg, requests fresh translations through the configured
ILLMProvider, computes a Jaccard consensus score, and writes audit
findings back to a sidecar Delta/Iceberg table.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import logging
import sys
from typing import Literal

import polars as pl

from ..ai import EnrichmentRequest, create_llm_provider
from ..resources import IcebergCatalogResource

log = logging.getLogger(__name__)


def _consensus(a: str, b: str) -> tuple[Literal["agree", "partial", "disagree"], float]:
    """Token-set Jaccard between two English translations."""
    tok_a = {t for t in a.lower().split() if t.isalnum()}
    tok_b = {t for t in b.lower().split() if t.isalnum()}
    if not tok_a and not tok_b:
        return "agree", 1.0
    if not tok_a or not tok_b:
        return "disagree", 0.0
    intersect = len(tok_a & tok_b)
    union = len(tok_a | tok_b)
    score = intersect / union if union else 0.0
    label: Literal["agree", "partial", "disagree"]
    if score >= 0.5:
        label = "agree"
    elif score >= 0.2:
        label = "partial"
    else:
        label = "disagree"
    return label, score


async def _audit_rows(rows: pl.DataFrame, limit: int) -> list[dict]:
    provider = create_llm_provider()
    findings: list[dict] = []
    sample = rows.head(limit)

    for row in sample.iter_rows(named=True):
        cn = row.get("name_cn") or ""
        if not cn:
            continue
        task_id = f"translate:{hashlib.sha256(cn.encode()).hexdigest()[:16]}"
        req = EnrichmentRequest(
            id=task_id,
            field="translate_cn_to_en",
            inputs={"cn": cn},
        )
        response = await provider.enrich(req)
        llm_en = response.result if isinstance(response.result, str) else None
        current_en = row.get("name_en") or ""

        label, score = ("disagree", 0.0)
        if llm_en:
            label, score = _consensus(current_en, llm_en)

        findings.append({
            "part_number": row.get("part_number"),
            "name_cn": cn,
            "dealer_supplied_en": current_en,
            "llm_alternative_en": llm_en,
            "consensus_label": label,
            "consensus_score": round(score, 4),
            "cache_hit": response.meta.cache_hit,
        })

    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="track-b enrich")
    parser.add_argument("--mode", choices=["fill", "audit"], default="audit")
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args(argv)

    # Load gold mart from Iceberg.
    catalog = IcebergCatalogResource().load()
    try:
        gold = catalog.load_table(("inventoryflow", "gold_products_mart"))
    except Exception as exc:
        log.error("gold_products_mart not found in Iceberg: %s", exc)
        log.error("Run `track-b ingest` first to materialise the mart.")
        return 1

    df = pl.from_arrow(gold.scan().to_arrow())
    if isinstance(df, pl.Series):
        df = df.to_frame()

    log.info("Loaded %d rows from gold_products_mart", df.height)

    findings = asyncio.run(_audit_rows(df, args.limit))

    if not findings:
        log.warning("No audit findings produced (no rows with name_cn?).")
        return 0

    findings_df = pl.from_dicts(findings)
    print(findings_df)  # noqa: T201

    counts = findings_df.group_by("consensus_label").len().sort("consensus_label")
    print(counts)  # noqa: T201

    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sys.exit(main())
