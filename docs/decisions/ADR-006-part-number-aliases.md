# ADR-006: Part number aliases table for OEM rename history

## Status
Accepted — 2026-05-11

## Context

Engine-parts sheets in the xlsx have **two part-number columns**: `OLD PART NUMBER` and `NEW PART NUMBER`. Example from `FOXStorm 70 AY70-2 Engine`:

| No. | OLD PART NUMBER | NEW PART NUMBER | EN name           |
|-----|-----------------|-----------------|-------------------|
| 1   | 101032-0166     | 101043-0117     | Cylinder Body Gasket |
| 2   | 101032-0217     | 101043-0131     | Dowel Pin Ф8×12     |
| 3   | 101032-0210     | 101043-0001     | Cylinder Body       |

The OEM renamed many parts. **Dealers downstream may still have old part numbers in their inventory, on customer orders, or in PO histories**. A catalog system that only stores the new number breaks lookup workflows like "I have part 101032-0166 in my warehouse — what is it?"

Three options:

- **A. Store only `NEW PART NUMBER`** — clean but breaks old-number lookups.
- **B. Store both as separate rows** — duplicate rows for the same physical part; ugly joins downstream.
- **C. One canonical row + `part_number_aliases` table** — single source of truth for the part, multiple lookup keys.

## Decision

**Option C**. Schema:

```sql
CREATE TABLE products (
  id                 BIGSERIAL PRIMARY KEY,
  part_number        TEXT NOT NULL,        -- ← the "current" / canonical
  part_number_norm   TEXT GENERATED ALWAYS AS (...) STORED,
  -- ... other columns
  UNIQUE (part_number_norm, source_dealer_id)
);

CREATE TABLE part_number_aliases (
  product_id   BIGINT REFERENCES products(id) ON DELETE CASCADE,
  alias        TEXT NOT NULL,         -- "101032-0166" (old)
  alias_norm   TEXT NOT NULL,
  alias_type   TEXT NOT NULL,         -- 'old' | 'oem_alt' | 'distributor'
  PRIMARY KEY (alias_norm, product_id)
);

CREATE INDEX idx_aliases_norm ON part_number_aliases (alias_norm);
```

Lookup pattern (search by any known number):

```sql
SELECT p.*
FROM products p
WHERE p.part_number_norm = $1
   OR p.id IN (
     SELECT product_id FROM part_number_aliases WHERE alias_norm = $1
   );
```

Or as a single query via UNION ALL with appropriate ordering for canonical-first.

## AI suggestion vs my override

**Claude initially suggested** storing only the NEW PART NUMBER and "noting the rename in a comment field".

**I overrode** because:

1. **Dealer workflows require old-number lookups**. The catalog isn't a green-field system; it's a normalisation layer over years of dealer state. Old part numbers exist on POs, in DMS systems (Lightspeed mentioned in JD), and in customer service contexts. Discarding them breaks those workflows.
2. **Comment fields are unsearchable** (well, sortof — trgm GIN would work but is wasteful).
3. **Aliases generalise**: distributors often have their own SKUs that map to the OEM. Same table handles "distributor X calls this part FOO-123" with `alias_type='distributor'`.
4. **`oem_alt`** — some parts have multiple OEM-issued numbers (manufacturer changed mid-cycle, region-specific). Same pattern.
5. **Audit trail value**: the rename history *is* useful for debugging "why doesn't this part match" issues post-launch.

## Trade-offs accepted

- **Two-table lookup** for any "find by part number" query — slower than single-table. Mitigated by `alias_norm` index + small table size (~10% of products).
- **Aliases may collide across products** (rare, but if OEM reuses an old number). `PRIMARY KEY (alias_norm, product_id)` allows the alias to map to multiple products; the consumer disambiguates by recency or by context.
- **Insertion is two-step** — insert product, then insert aliases. Wrapped in a transaction; not a real complexity cost.
- **The `part_number` column on `products` ages out** as OEMs keep renaming. Acceptable — the `alias_type='old'` rows preserve history.

## When to revisit

- If alias lookups become >30% of catalog query traffic, denormalise into a materialised view `products_with_aliases` for fast OR-search.
- If a new alias dimension appears (e.g., supersession chain: A → B → C → D), evaluate adding `superseded_by` FK on aliases.

## Sources

- Empirical: `xl/worksheets/sheet4.xml` (Engine sheet) probe results showing OLD/NEW columns.
- Pattern reference: SAP/Oracle ERP catalog systems handle part-number aliasing identically (terms: "Cross-Reference Number", "Alt Part Number").
- Lightspeed DMS API docs (JD mentions Lightspeed integration): cross-reference table is first-class concept.
