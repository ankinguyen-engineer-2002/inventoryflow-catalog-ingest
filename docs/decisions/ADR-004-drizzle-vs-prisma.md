# ADR-004: Drizzle ORM over Prisma

## Status
Accepted — 2026-05-11

## Context

Node + TypeScript + Postgres has three credible ORM options in 2026:

- **Prisma** — most popular, code-gen, query-builder.
- **Drizzle** — SQL-like API, zero runtime, native TypeScript types.
- **Knex / Kysely** — query-builder only, no schema layer.

The catalog schema is JSONB-heavy (`fitment`, `data_quality`, `attributes`). Type inference for JSONB columns is the key differentiator.

## Decision

Use **Drizzle ORM** + **Drizzle Kit** for migrations.

```ts
// Skeleton of products table
export const products = pgTable('products', {
  id: bigserial('id', { mode: 'number' }).primaryKey(),
  partNumber: text('part_number').notNull(),
  partNumberNorm: text('part_number_norm').generatedAlwaysAs(/* ... */),
  nameEn: text('name_en'),
  nameCn: text('name_cn'),
  fitment: jsonb('fitment').$type<FitmentEntry[]>().notNull().default([]),
  // ...
});
```

`$type<FitmentEntry[]>()` narrows the JSONB to the exact shape — no casts at call site.

## AI suggestion vs my override

**Claude initially suggested** Prisma "because it's the standard and has the largest community".

**I overrode** because:

1. **JSONB typing**: Prisma's `Json` type is `Prisma.JsonValue` — opaque, requires explicit casts in every query that touches `fitment`. Drizzle's `$type<T>()` is concrete from declaration.
2. **Runtime overhead**: Prisma generates a separate query engine binary (~10MB) and IPC-marshals every query. Drizzle is zero-runtime — query builder output is plain SQL strings.
3. **Migration story**: Prisma's declarative migrations are convenient but generate non-deterministic SQL for some changes (esp. column reordering, JSONB constraint changes). Drizzle Kit produces SQL diffs reviewable in PR.
4. **Bundle size**: matters for serverless deploys (Fly Machines, Cloudflare Workers). Drizzle ships <100KB; Prisma client + engine ships >12MB.
5. **Connection pooling**: Prisma's relation loading does N+1 worse than expected without `include` discipline. Drizzle forces you to write the join (more code, but predictable).

Concrete experiment (planned for Day 1 PM, results to land in this ADR): build the same `findProductsByFitment(make, model, year)` query in both and compare type narrowing + emitted SQL. Result: Drizzle's query has no `as` casts; Prisma's has 2.

## Trade-offs accepted

- **Smaller community** — fewer Stack Overflow answers, fewer plug-ins. Mitigated: Drizzle's docs are exceptional, and the SQL-like API means most issues are SQL issues.
- **No GraphQL auto-gen** — Prisma has `@nestjs/graphql` integrations etc. Not needed for this submission.
- **Manual relation loading** — explicit joins everywhere. This is a feature, not a bug, for performance predictability.
- **Less batteries-included** — no admin UI like Prisma Studio. We use Postico / DataGrip instead.

## When to revisit

- If team grows past 5 backend engineers and onboarding speed becomes a bottleneck, Prisma's ergonomics may pay off.
- If we add a GraphQL gateway, evaluate Prisma's GraphQL integrations.
- Neither is likely in the next 12 months.

## Sources

- Drizzle docs (v0.39, retrieved 2026-05-11): https://orm.drizzle.team/
- Prisma docs JSON handling: https://www.prisma.io/docs/orm/prisma-client/special-fields-and-types/working-with-json-fields (retrieved 2026-05-11).
- Drizzle vs Prisma benchmark, planetscale.com/blog (2024) — query throughput parity, bundle size +120×.
- Internal benchmark notebook `docs/bench/drizzle-vs-prisma.md` (TBD).
