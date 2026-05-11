# Recruiter / Reviewer Access Guide

Concrete commands to reproduce every output the test PDF asks for. Copy-paste, no guessing.

---

## 0. Prerequisites

```bash
# macOS — adapt for your OS
brew install node@22 pnpm libpq colima docker-compose
colima start --cpu 4 --memory 8
```

If you already have Docker Desktop / OrbStack running, skip the colima step.

---

## 1. Clone and boot the stack

```bash
git clone https://github.com/ankinguyen-engineer-2002/inventoryflow-catalog-ingest.git
cd inventoryflow-catalog-ingest/track-a-jd-native

cp .env.example .env                           # default config; no edits needed
docker-compose up -d                           # postgres + redis + minio
pnpm install                                   # ~50 s first time
pnpm db:migrate                                # creates 12 tables
```

**Verify** the three containers are healthy:

```bash
docker-compose ps
```

You should see `ifc_postgres`, `ifc_redis`, `ifc_minio` all `(healthy)`.

---

## 2. Get the source file

The 241 MB xlsx isn't committed (too large). Place it at the expected location:

```bash
# from the project root (one level above track-a-jd-native):
cp /path/to/"Copy of Example Data for Engineer.xlsx" \
   shared/sample-data/example.xlsx
```

---

## 3. Run the full pipeline

**One command runs everything** — main ingest, reference-sheets pass for the
~12 exception sheets, vehicle-models derivation, MDCP seed, and LLM audit:

```bash
cd track-a-jd-native    # if you're not already there
pnpm ingest:full ../shared/sample-data/example.xlsx
```

Expected runtime: **~60 seconds** on an M2 Mac, $0 in API spend (LLM hits cache).

If you'd rather run each stage individually:

```bash
pnpm ingest            ../shared/sample-data/example.xlsx    # parts catalog (96 sheets)
pnpm ingest:references ../shared/sample-data/example.xlsx    # ~12 spec sheets → reference_specs
pnpm populate:models                                         # derive vehicle_models from fitments
pnpm seed:mdcp                                               # seed dealers + ingestion_patterns + bindings
pnpm enrich --mode audit --limit 60                          # LLM cross-validation of 60 rows
```

If you want to see the pre-database parse output without writing anything:

```bash
pnpm ingest:dryrun ../shared/sample-data/example.xlsx --sheet "FOXStorm 70 AY70-2" --limit 5
```

---

## 4. Verify the test-PDF outputs

The test PDF asks for: **clean database** with **part_number / name_en / name_cn**, **schematic images in R2**, and a **JSONB column for year/make/model fitment**. Here are the commands to confirm each.

### 4.1 Clean database — every table populated

```bash
docker exec ifc_postgres psql -U dev -d catalog -c "
  SELECT 'products' AS t,                     COUNT(*) FROM products UNION ALL
  SELECT 'product_images',                    COUNT(*) FROM product_images UNION ALL
  SELECT 'reference_specs',                   COUNT(*) FROM reference_specs UNION ALL
  SELECT 'ingest_audit',                      COUNT(*) FROM ingest_audit UNION ALL
  SELECT 'part_number_aliases',               COUNT(*) FROM part_number_aliases UNION ALL
  SELECT 'vehicle_models',                    COUNT(*) FROM vehicle_models UNION ALL
  SELECT 'stream_events',                     COUNT(*) FROM stream_events UNION ALL
  SELECT 'stream_outbox',                     COUNT(*) FROM stream_outbox UNION ALL
  SELECT 'ingest_runs',                       COUNT(*) FROM ingest_runs UNION ALL
  SELECT 'ingestion_patterns',                COUNT(*) FROM ingestion_patterns UNION ALL
  SELECT 'dealer_pattern_bindings',           COUNT(*) FROM dealer_pattern_bindings UNION ALL
  SELECT 'dealers',                           COUNT(*) FROM dealers
  ORDER BY 2 DESC;
"
```

Expected (after `pnpm ingest:full`):

```
            t            | count
-------------------------+-------
 product_images          | 10524     ← schematic image associations
 products                |  3938     ← test-PDF priority output
 reference_specs         |   371     ← 12 exception sheets parsed
 ingest_audit            |   ~120    ← LLM call history (grows per run)
 part_number_aliases     |    50     ← engine sheets OLD/NEW pairs
 vehicle_models          |    35     ← derived from products.fitment
 stream_events           |     0+    ← grows when you POST /events/*
 stream_outbox           |     0+    ← grows alongside stream_events
 ingest_runs             |     5+    ← one row per ingest invocation
 ingestion_patterns      |     3     ← MDCP handler registry
 dealer_pattern_bindings |     3     ← MDCP per-tenant config
 dealers                 |     1     ← Kayo OEM Demo Dealer
```

Every table is populated; what's there exists by design rather than by accident.

> Run status of the main ingest is `PARTIAL` because some rows in the source xlsx are blank-row separators that the parser deliberately skips. See `docs/TRACK_A.md §3` for the row-accounting breakdown.

### 4.2 Sample rows — `part_number / name_en / name_cn`

```bash
docker exec ifc_postgres psql -U dev -d catalog -c "
  SELECT part_number, name_en, name_cn, retail_price
  FROM products
  ORDER BY id
  LIMIT 10;
"
```

Sample output:

```
 part_number | name_en                | name_cn  | retail_price
-------------+------------------------+----------+--------------
 602006-0015 | black handle bar grip  | 把套     |         10.2
 602006-0026 | black handle bar grip  | 把  套   |         10.2
 313001-0008 | multi-function switch  | 组合开关 |           34
 ...
```

### 4.3 JSONB fitment column — the test's stated focus

```bash
docker exec ifc_postgres psql -U dev -d catalog -c "
  SELECT
    part_number,
    name_en,
    jsonb_pretty(fitment) AS fitment
  FROM products
  WHERE part_number = '602006-0015';
"
```

Sample output:

```
 part_number | name_en               | fitment
-------------+-----------------------+-----------------------------
 602006-0015 | black handle bar grip | [                          ↵
             |                       |     {                       ↵
             |                       |         "year": 0,          ↵
             |                       |         "make": "Kayo",     ↵
             |                       |         "model": "AY70-2",  ↵
             |                       |         "section": null,    ↵
             |                       |         "variant": null,    ↵
             |                       |         "callout_no": "1.0",↵
             |                       |         "confidence": "high"↵
             |                       |     }                       ↵
             |                       | ]
```

### 4.4a Reference sheets (the 12 "exception" sheets) — `reference_specs`

The main parts catalog uses three regular header signatures (`chassis`,
`engine`, `chassis_u8`). The xlsx also has ~12 sheets with completely
different schemas — spark plugs by model, carburetor jet sizes, wheel
bolt patterns, etc. — that the main parser intentionally skips. They're
loaded by `pnpm ingest:references` into a separate table keyed by category.

```bash
docker exec ifc_postgres psql -U dev -d catalog -c "
  SELECT category, COUNT(*) AS rows
  FROM reference_specs
  GROUP BY category
  ORDER BY rows DESC;
"
```

Example: lookup spark plug equivalencies by model

```bash
docker exec ifc_postgres psql -U dev -d catalog -c "
  SELECT model_code, attributes
  FROM reference_specs
  WHERE category = 'spark_plugs'
  LIMIT 5;
"
```

### 4.4b Vehicle dimension table — `vehicle_models`

Derived post-ingest from `products.fitment` (distinct tuples). Used for
analytics joins and as a DQ target ("does fitment.model_code exist in
vehicle_models?").

```bash
docker exec ifc_postgres psql -U dev -d catalog -c "
  SELECT make, model_code, year_start, year_end, variant
  FROM vehicle_models
  ORDER BY model_code, year_start NULLS LAST
  LIMIT 10;
"
```

### 4.4c MDCP control-plane tables — `dealers`, `ingestion_patterns`, `dealer_pattern_bindings`

ADR-014's metadata-driven dispatch. Seeded by `pnpm seed:mdcp` with one
realistic example each so the relationship is inspectable.

```bash
docker exec ifc_postgres psql -U dev -d catalog -c "
  SELECT d.name AS dealer, p.pattern_name, p.pattern_type, b.schedule
  FROM dealer_pattern_bindings b
  JOIN dealers d            ON d.id = b.dealer_id
  JOIN ingestion_patterns p ON p.pattern_name = b.pattern_name;
"
```

The dispatcher loop that *consumes* these bindings is documented as the
next milestone in ADR-014 — the tables and relationships are scaffolded;
runtime selection by `LLM_PROVIDER`-style env-driven loop is the production
follow-up.

### 4.4d Query parts that fit a vehicle — `@>` containment

This is the access pattern the test was designed around.

```bash
docker exec ifc_postgres psql -U dev -d catalog -c "
  SELECT part_number, name_en, name_cn
  FROM products
  WHERE fitment @> '[{\"make\":\"Kayo\",\"model_code\":\"AY70-2\"}]'
  LIMIT 10;
"
```

The `@>` operator is indexed by `GIN (fitment jsonb_path_ops)`; at scale this returns in <50 ms on millions of rows.

### 4.5 Schematic images in R2 (MinIO locally)

**Browser UI (easiest):**

Open <http://localhost:9001> in your browser. Login `minioadmin` / `minioadmin`. Click on the `catalog` bucket. You'll see ~382 `.jpg` and `.png` files under `sha256/<aa>/<bb>/<full-hash>.<ext>`.

**CLI (list, count, sample):**

```bash
# Total image objects
docker run --rm --network host \
  -e MC_HOST_local=http://minioadmin:minioadmin@localhost:9000 \
  minio/mc ls --recursive local/catalog | wc -l

# First 5 keys
docker run --rm --network host \
  -e MC_HOST_local=http://minioadmin:minioadmin@localhost:9000 \
  minio/mc ls --recursive local/catalog | head -5

# Pull one image to disk
docker run --rm --network host -v "$PWD":/host \
  -e MC_HOST_local=http://minioadmin:minioadmin@localhost:9000 \
  minio/mc cp local/catalog/sha256/<paste-prefix-here> /host/sample.jpg
```

**Direct HTTP (the bucket is read-anonymous in dev):**

```bash
# Get the URL of the schematic for a product
docker exec ifc_postgres psql -U dev -d catalog -tA -c "
  SELECT r2_url
  FROM product_images
  WHERE product_id = (SELECT id FROM products WHERE part_number='602006-0015')
  LIMIT 1;
"
# → http://localhost:9000/catalog/sha256/87/68/876869...jpg

curl -I "http://localhost:9000/catalog/sha256/87/68/876869...jpg"
# → 200 OK, content-type: image/jpeg
```

> **In production**: change `S3_ENDPOINT` from `http://localhost:9000` to your Cloudflare R2 endpoint (`https://<account>.r2.cloudflarestorage.com`). Same SDK, same key strategy, same code.

---

## 5. The LLM audit pass

The test mentions Vision LLM tooling. Track A integrates an `ILLMProvider` abstraction with a committed cache so this runs at zero cost for you:

```bash
pnpm enrich --mode audit --limit 60
```

Expected output ends with:

```
{
  attempted: 60,
  enriched:  60,
  skipped:   0,
  llmCalls:  60,
  llmCost:   0      # all cache hits
}
```

Inspect the audit results:

```bash
docker exec ifc_postgres psql -U dev -d catalog -c "
  SELECT
    COUNT(*) FILTER (WHERE data_quality->>'translation_verified' = 'true') AS verified,
    COUNT(*) FILTER (WHERE data_quality->>'translation_consensus' = 'agree')    AS agree,
    COUNT(*) FILTER (WHERE data_quality->>'translation_consensus' = 'partial')  AS partial,
    COUNT(*) FILTER (WHERE data_quality->>'translation_consensus' = 'disagree') AS disagree
  FROM products;
"
```

Sample real disagreements caught — these are defects in the dealer-supplied EN names:

```bash
docker exec ifc_postgres psql -U dev -d catalog -c "
  SELECT
    name_cn,
    name_en                                  AS current_en,
    data_quality->>'translation_llm_alt'     AS llm_en,
    data_quality->>'translation_consensus_score' AS score
  FROM products
  WHERE data_quality->>'translation_consensus' = 'disagree'
  ORDER BY (data_quality->>'translation_consensus_score')::float
  LIMIT 5;
"
```

---

## 6. The HTTP surface

Start the API server:

```bash
pnpm api
```

Health probes:

```bash
curl -s http://localhost:3000/healthz | jq
# { "ok": true, "ts": "2026-05-11T..." }

curl -s http://localhost:3000/readyz | jq
# { "ok": true, "checks": { "postgres": "ok", "redis": "ok" } }
```

Streaming webhook (in another terminal, after `pnpm worker` is also running):

```bash
curl -X POST http://localhost:3000/events/inventory \
  -H 'content-type: application/json' \
  -H 'x-dealer-id: 11111111-1111-1111-1111-111111111111' \
  -d '{"part_number":"602006-0015","stock_level":42}'
# → { "eventId": "...", "accepted": true }
```

Verify it was processed:

```bash
docker exec ifc_postgres psql -U dev -d catalog -c "
  SELECT
    status,
    processed_at IS NOT NULL AS processed,
    payload->>'stock_level' AS stock
  FROM stream_events
  ORDER BY received_at DESC
  LIMIT 1;
"
```

And inspect what landed on the product:

```bash
docker exec ifc_postgres psql -U dev -d catalog -c "
  SELECT part_number, data_quality
  FROM products
  WHERE part_number = '602006-0015';
"
# → data_quality now contains stock_level + stock_updated_at
```

---

## 7. Confirm idempotency

Re-run the ingest — row counts should not change:

```bash
docker exec ifc_postgres psql -U dev -d catalog -tAc "SELECT COUNT(*) FROM products;"
pnpm ingest ../shared/sample-data/example.xlsx
docker exec ifc_postgres psql -U dev -d catalog -tAc "SELECT COUNT(*) FROM products;"
```

Both counts equal — same 3,938 products. This is the `NULLS NOT DISTINCT` unique-index doing its job.

---

## 8. Run the test suite

```bash
pnpm test
```

Expected: `32 passing` across 5 test files in <500 ms.

---

## 9. Tear down

```bash
docker-compose down -v    # -v wipes the volumes too
```

---

## 10. What to read next

- **`docs/TRACK_A.md`** — engineering write-up: what was built, why, what broke, what's deferred.
- **`PLAN.md`** — full strategic plan, two-track strategy, v10 capability matrix.
- **`docs/COMPARISON.md`** — Track A vs Track B on 18 dimensions.
- **`docs/decisions/`** — 14 ADRs covering every non-trivial design choice.
- **`docs/QUESTIONS_FOR_RECRUITER.md`** — 5 open questions + 8 signals I caught reading the source data.
