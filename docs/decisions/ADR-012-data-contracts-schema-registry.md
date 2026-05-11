# ADR-012: Data contracts + schema registry

## Status
Accepted — 2026-05-11 · scope: contract docs + Iceberg-as-registry + Zod runtime validation; full Buz/schemata integration deferred

## Context

When InventoryFlow has 100+ dealers producing catalog rows and N marketplaces consuming them, the **producer-consumer interface** needs to be a contract — versioned, validated at write time, and breaking-change-aware. Without contracts:

- Producer changes a field name → silent breakage downstream.
- Consumer adds a required field → no way to communicate to upstream.
- Schema drift accumulates → mystery data quality bugs in production.

The 2025-2026 trend is **data contracts as code**, popularized by tools like Buz, schemata, Confluent Schema Registry, and Apache Iceberg's column-level metadata.

## Decision

Three layered mechanisms — each addressing a different scope:

### 1. Internal schemas (compile-time + runtime)

- **Zod schemas** in TypeScript (`shared/schemas/*.ts`) for every external boundary: webhook payloads, xlsx row shape, fitment JSONB, LLM request/response, R2 key format.
- **JSON Schema** exports of the Zod schemas (`shared/schemas/*.schema.json`) for cross-language use (Python in Track B reads these).
- Generated at build time; committed to repo for reviewers to inspect.

### 2. Lakehouse schema registry (Track B)

**Apache Iceberg catalog is the schema registry**. Every Iceberg table has:
- Typed columns (no schema-on-read fragility).
- A schema version per metadata commit.
- `ALTER TABLE` operations are first-class and audited.
- Schema evolution rules enforced at write time (no implicit narrowing, no implicit nullability changes).

This means: when a dealer's xlsx adds a new column "Country of Origin", the bronze→silver materialization detects the new field; `mergeSchema=true` adds it to the silver table; **OpenLineage emits a `schemaChangeEvent`**; an alert fires; consumers opt-in to the new column at their own pace.

### 3. Cross-team data contracts (documented, partially implemented)

For external API surfaces (webhook ingestion, marketplace sync output), a **data contract** is a versioned document at `shared/contracts/`:

```yaml
# shared/contracts/inventory_event.v1.yaml
name: inventory_event
version: 1.0
owner: aric@inventoryflow.ai
description: Dealer inventory level change webhook
status: stable
sla:
  ingestion_latency_p95_ms: 500
  retention_days: 90
schema:
  $ref: ../schemas/inventory-event.schema.json
breaking_changes:
  - Adding required field
  - Removing optional field
  - Changing field type
non_breaking_changes:
  - Adding optional field
  - Documentation changes
deprecation_policy: 6 months notice before removal
example:
  dealer_id: "..."
  part_number: "602006-0015"
  stock_level: 42
  timestamp: "2026-05-11T10:00:00Z"
```

The contract is **the source of truth**; both producer (dealer client SDK) and consumer (our webhook route handler) reference it. Breaking changes require:
1. Publishing a new version (`v2.yaml`) alongside `v1.yaml`.
2. Running both versions in parallel for the deprecation period.
3. Announcing the deprecation in the contract metadata.
4. Logging usage of deprecated fields to plan removal.

## Implementation status in PoC

| Element                            | PoC status                                |
| ---------------------------------- | ----------------------------------------- |
| Zod schemas for internal boundaries | ✅ Implemented (every boundary has one)   |
| JSON Schema generated exports       | ✅ Implemented                            |
| Iceberg as registry (Track B)       | ✅ Implemented (in Track B PoC)           |
| Cross-team contracts YAML           | ⚠️ One example contract for inventory_event; full set for production |
| Contract enforcement at runtime     | ⚠️ Zod validation present; explicit "contract version" header check deferred |
| Schema change CI checks             | 📋 GitHub Action sketched in `.github/workflows/contract-check.yml` (not in PoC scope) |

## AI suggestion vs my override

**Claude initially suggested** using a centralized Confluent Schema Registry deployment.

**I overrode** because:

1. **Confluent Schema Registry requires a Kafka deployment** — heavyweight for our scale.
2. **Iceberg already is a schema registry** for Track B's analytical paths.
3. **For external API contracts, plain YAML + JSON Schema** is more portable and easier for dealers to read than Avro/Protobuf binary formats.
4. **Centralized registry is overkill** at <500 dealers; can migrate to Buz or Confluent SR when scale demands it.

## Trade-offs accepted

- **No central registry service** in PoC. Acceptable; Iceberg + git-tracked contracts cover the use cases at current scale.
- **Contract version negotiation not implemented**: clients send the contract version they understand in a header; server must support multiple versions. PoC only supports `v1`.
- **Backward-compat is enforced by convention**, not by tooling. Future: GitHub Action that diffs contract YAMLs against `main` and blocks breaking changes without a major-version bump.
- **No protocol buffer / Avro encoding** — JSON only. Saves complexity; sacrifices ~30% size efficiency over the wire. Acceptable at current event volumes (<1M/day).

## When to revisit

- **>500 dealers**: introduce a registry service (Buz, schemata.dev, or Apicurio).
- **Marketplace consumers other than eBay/Amazon arrive**: codify outbound contracts as well.
- **Need cross-language clients (Python, Go, etc.)**: switch JSON Schema → Avro or Protobuf for cross-language type generation.
- **First production incident from schema drift**: that's the trigger to invest in automated contract CI checks.

## Sources

- Buz (data contracts platform): https://buz.dev/ (retrieved 2026-05-11). [Verified]
- schemata.dev: https://github.com/ananthdurai/schemata (retrieved 2026-05-11). [Verified]
- Apache Iceberg schema evolution: https://iceberg.apache.org/docs/latest/evolution/ (retrieved 2026-05-11). [Verified]
- "Data Contracts" — Chad Sanderson: https://dataproducts.substack.com/ (retrieved 2026-05-11). [Verified]
- OpenLineage `schemaChangeEvent` spec: https://openlineage.io/docs/spec/run-cycle (retrieved 2026-05-11). [Verified]
- Confluent Schema Registry docs: https://docs.confluent.io/platform/current/schema-registry/index.html (retrieved 2026-05-11). [Verified]
