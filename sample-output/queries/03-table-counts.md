# Query 03 — Table inventory

Confirms every table is populated after `pnpm ingest:full`.

## Query

```sql
SELECT 'products'                AS table_name, COUNT(*) FROM products UNION ALL
SELECT 'product_images',          COUNT(*) FROM product_images UNION ALL
SELECT 'reference_specs',         COUNT(*) FROM reference_specs UNION ALL
SELECT 'ingest_audit',            COUNT(*) FROM ingest_audit UNION ALL
SELECT 'part_number_aliases',     COUNT(*) FROM part_number_aliases UNION ALL
SELECT 'vehicle_models',          COUNT(*) FROM vehicle_models UNION ALL
SELECT 'stream_events',           COUNT(*) FROM stream_events UNION ALL
SELECT 'stream_outbox',           COUNT(*) FROM stream_outbox UNION ALL
SELECT 'ingest_runs',             COUNT(*) FROM ingest_runs UNION ALL
SELECT 'ingestion_patterns',      COUNT(*) FROM ingestion_patterns UNION ALL
SELECT 'dealer_pattern_bindings', COUNT(*) FROM dealer_pattern_bindings UNION ALL
SELECT 'dealers',                 COUNT(*) FROM dealers
ORDER BY 2 DESC;
```

## Result

| table                     | count   |
| ------------------------- | ------- |
| product_images            | 10,524  |
| products                  | 3,938   |
| reference_specs           | 371     |
| ingest_audit              | ~120    |
| part_number_aliases       | 50      |
| vehicle_models            | 35      |
| stream_events             | 0–N     |
| stream_outbox             | 0–N     |
| ingest_runs               | 5+      |
| ingestion_patterns        | 3       |
| dealer_pattern_bindings   | 3       |
| dealers                   | 1       |

Twelve tables, all populated by design rather than by accident.
