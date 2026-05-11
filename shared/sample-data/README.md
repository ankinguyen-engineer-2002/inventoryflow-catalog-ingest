# Sample data

The 241 MB test xlsx is not committed. Place it locally as:

```
example.xlsx
```

## Source

Talemy x InventoryFlow Senior Engineer Test packet, sent 2026-05-08.

Original filename in the packet: `Copy of Example Data for Engineer.xlsx`.

## Content summary

- 110 sheets
- 1586 embedded schematic images
- ~12,000–18,000 distinct expected product rows
- OEM: Kayo (inferred — see `docs/QUESTIONS_FOR_RECRUITER.md` Q2)
- Languages: English + Simplified Chinese

## Generating from source

```bash
cp "/Users/MAC/Documents/InventoryFlowXTelamy/Copy of Example Data for Engineer.xlsx" example.xlsx
```

The pipeline computes SHA-256 on ingest; the same input produces the same `source_sha256` in `ingest_runs`.
