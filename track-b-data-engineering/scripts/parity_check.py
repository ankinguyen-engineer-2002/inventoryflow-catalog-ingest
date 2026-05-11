"""Parity check — Track B parser vs Track A CSV output.

Runs the Track B Python parser against the source xlsx, writes products
to CSV with the same schema Track A exports, then diffs against Track A's
committed reference at sample-output/data/products-full.csv.

Goal: prove that two infrastructures (PG vs Iceberg) solving the same
problem produce the same logical dataset.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
from pathlib import Path

# Add the project root (one level up) to sys.path so `parser` resolves
# to our local parser package, not Python's deprecated stdlib parser.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from parser.parse_xlsx import ParsedProduct, parse_xlsx  # noqa: E402

log = logging.getLogger(__name__)

# Track A's CSV column order (from sample-output/data/products-full.csv).
CSV_COLUMNS = ("part_number", "name_en", "name_cn", "spec_cn", "retail_price", "fitment")


def write_csv(products: list[ParsedProduct], out_path: Path) -> None:
    """Write parsed products to CSV in Track A's schema.

    Dedupe by part_number with last-row-wins semantics — mirrors Track A's
    PostgreSQL `ON CONFLICT (part_number) DO UPDATE SET ...` behaviour
    where the final products.fitment ends up with the most recent sheet's
    affiliation rather than the union.
    """
    # Dedupe by part_number with last-row-wins — mirrors Track A's
    # PostgreSQL `ON CONFLICT (part_number) DO UPDATE SET ...` semantics.
    deduped: dict[str, ParsedProduct] = {}
    for p in products:
        deduped[p.part_number] = p

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS, quoting=csv.QUOTE_MINIMAL)
        writer.writeheader()
        for p in deduped.values():
            writer.writerow({
                "part_number": p.part_number,
                "name_en": p.name_en or "",
                "name_cn": p.name_cn or "",
                "spec_cn": p.spec_cn or "",
                "retail_price": _format_price(p.retail_price),
                "fitment": json.dumps(
                    [_fitment_as_dict(f) for f in p.fitment],
                    ensure_ascii=False,
                    separators=(", ", ": "),
                ),
            })


def _format_price(v: float | None) -> str:
    if v is None:
        return ""
    if v == int(v):
        return f"{int(v)}"
    return f"{v:g}"


def _fitment_as_dict(f) -> dict:  # type: ignore[no-untyped-def]
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


def diff_summary(track_b_csv: Path, track_a_csv: Path) -> dict:
    """Compare two CSV files on (part_number, name_en) overlap.

    Quick parity heuristic — not a strict bit-diff, since float formatting
    and fitment ordering may differ but the data is the same.
    """
    b_rows = _read_keyed(track_b_csv)
    a_rows = _read_keyed(track_a_csv)

    common = b_rows.keys() & a_rows.keys()
    only_b = b_rows.keys() - a_rows.keys()
    only_a = a_rows.keys() - b_rows.keys()

    name_mismatch = 0
    cn_mismatch = 0
    price_mismatch = 0
    fitment_model_mismatch = 0
    fitment_year_mismatch = 0
    fitment_model_match = 0
    sample_mismatch_keys: list[str] = []

    for k in common:
        a = a_rows[k]
        b = b_rows[k]
        if (a["name_en"] or "") != (b["name_en"] or ""):
            name_mismatch += 1
            if len(sample_mismatch_keys) < 5:
                sample_mismatch_keys.append(k)
        if (a["name_cn"] or "") != (b["name_cn"] or ""):
            cn_mismatch += 1
        if _norm_price(a["retail_price"]) != _norm_price(b["retail_price"]):
            price_mismatch += 1

        a_fit = _first_fitment(a["fitment"])
        b_fit = _first_fitment(b["fitment"])
        if a_fit and b_fit:
            if a_fit.get("model_code") == b_fit.get("model_code"):
                fitment_model_match += 1
            else:
                fitment_model_mismatch += 1
            if a_fit.get("year") != b_fit.get("year"):
                fitment_year_mismatch += 1

    return {
        "track_a_rows": len(a_rows),
        "track_b_rows": len(b_rows),
        "common_part_numbers": len(common),
        "only_in_track_a": len(only_a),
        "only_in_track_b": len(only_b),
        "name_en_mismatches": name_mismatch,
        "name_cn_mismatches": cn_mismatch,
        "retail_price_mismatches": price_mismatch,
        "fitment_model_match": fitment_model_match,
        "fitment_model_mismatch": fitment_model_mismatch,
        "fitment_year_mismatch": fitment_year_mismatch,
        "parity_pct": round(100 * (len(common) - name_mismatch) / max(len(a_rows), 1), 2),
        "sample_only_a": sorted(only_a)[:5],
        "sample_only_b": sorted(only_b)[:5],
        "sample_name_mismatch": sample_mismatch_keys,
    }


def _norm_price(s: str | None) -> str:
    if s is None or s == "":
        return ""
    try:
        return f"{float(s):.4f}"
    except ValueError:
        return s.strip()


def _first_fitment(s: str) -> dict | None:
    if not s:
        return None
    try:
        arr = json.loads(s)
    except json.JSONDecodeError:
        return None
    if isinstance(arr, list) and arr:
        return arr[0]
    return None


def _read_keyed(path: Path) -> dict[str, dict]:
    """Read a CSV keyed by part_number. Last row wins on duplicates."""
    out: dict[str, dict] = {}
    with path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            pn = row.get("part_number", "").strip()
            if pn:
                out[pn] = row
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="parity_check")
    parser.add_argument(
        "--xlsx",
        default="../shared/sample-data/example.xlsx",
        help="Path to source xlsx",
    )
    parser.add_argument(
        "--out",
        default="../sample-output/track-b/data/products-full.csv",
        help="Where to write Track B's CSV",
    )
    parser.add_argument(
        "--ref",
        default="../sample-output/data/products-full.csv",
        help="Track A's reference CSV to diff against",
    )
    args = parser.parse_args(argv)

    xlsx = Path(args.xlsx).resolve()
    out = Path(args.out).resolve()
    ref = Path(args.ref).resolve()

    if not xlsx.exists():
        log.error("xlsx not found: %s", xlsx)
        return 1

    log.info("Parsing %s ...", xlsx)
    products = parse_xlsx(xlsx)
    log.info("Parsed %d product rows", len(products))

    log.info("Writing %s", out)
    write_csv(products, out)

    if not ref.exists():
        log.warning("Reference CSV not found: %s — skipping diff", ref)
        return 0

    log.info("Comparing against %s", ref)
    summary = diff_summary(out, ref)
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    sys.exit(main())
