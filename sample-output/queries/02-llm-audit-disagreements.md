# Query 02 — LLM-flagged translation defects

The LLM audit pass (Layer 3 of the five-layer accuracy framework, ADR-007) cross-validates dealer-supplied EN names against a fresh LLM translation. Rows where the two disagree are surfaced for human review.

## Query

```sql
SELECT
  name_cn                                       AS chinese,
  name_en                                       AS dealer_supplied,
  data_quality->>'translation_llm_alt'          AS llm_alternative,
  data_quality->>'translation_consensus_score'  AS jaccard_score
FROM products
WHERE data_quality->>'translation_consensus' = 'disagree'
ORDER BY (data_quality->>'translation_consensus_score')::float ASC;
```

## Result (11 rows flagged out of 68 audited — 16% defect rate)

| Chinese          | Dealer EN              | LLM alternative                            | Defect type           |
| ---------------- | ---------------------- | ------------------------------------------ | --------------------- |
| 转向冶金衬套     | `busher`               | steering column sintered bushing           | Typo + missing context|
| 前左右减震       | `front fork`           | front left and right shock absorbers       | Wrong part category   |
| 平垫 GB97.1-85   | `flat gasket`          | flat washer GB97.1-85                      | Gasket ≠ washer       |
| 前碟刹驻车手柄   | `front park lock kit`  | front parking brake handle                 | Imprecise terminology |

Each disagreement was caught at **zero variable cost** (cache hit) during the audit pass. Production scale runs this nightly across all dealers via Anthropic Batch API at approximately $0.05 per 1000-dealer run.
