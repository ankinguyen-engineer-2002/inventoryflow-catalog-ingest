/**
 * Products repository.
 *
 * Owns upserts into the `products` table with our idempotency contract:
 *   UNIQUE (part_number_norm, source_dealer_id) → ON CONFLICT DO UPDATE.
 *
 * Re-running the same ingest produces no duplicate rows; updated fields
 * land in-place. This is the centrepiece of "dealer re-uploads weekly".
 */
import { sql as drizzleSql } from "drizzle-orm";
import { db } from "../client.js";
import { products, partNumberAliases, type FitmentEntry } from "../schema.js";
import type { NormalisedRow } from "../../../ingest/row-normalizer.js";

export interface ProductPersistInput {
  row: NormalisedRow;
  fitment: FitmentEntry[];
  sourceFileSha256: string;
  sourceDealerId: string | null;
  primaryImageR2Key?: string | null;
}

export interface ProductUpsertResult {
  productId: number;
  /** True if the row was newly inserted, false if it was updated. */
  inserted: boolean;
}

/**
 * Upsert one product row. Returns the product id (auto-generated or existing).
 * Caller is responsible for batching when ingesting many rows.
 */
export async function upsertProduct(input: ProductPersistInput): Promise<ProductUpsertResult> {
  const { row, fitment, sourceFileSha256, sourceDealerId, primaryImageR2Key } = input;

  const inserted = await db
    .insert(products)
    .values({
      partNumber: row.partNumber,
      nameEn: row.nameEn,
      nameCn: row.nameCn,
      specCn: row.specCn,
      qtyPerVehicle: row.qtyPerVehicle === null ? null : String(row.qtyPerVehicle),
      dealerCost: row.dealerCost === null ? null : String(row.dealerCost),
      unit: row.unit,
      retailPrice: row.retailPrice === null ? null : String(row.retailPrice),
      fitment,
      primaryImageR2Key: primaryImageR2Key ?? null,
      sourceDealerId,
      sourceFileSha256,
      sourceSheet: row.sourceSheet,
      sourceRowIndex: row.sourceRowIndex,
    })
    .onConflictDoUpdate({
      target: [products.partNumberNorm, products.sourceDealerId],
      set: {
        nameEn: drizzleSql`COALESCE(EXCLUDED.name_en, ${products.nameEn})`,
        nameCn: drizzleSql`COALESCE(EXCLUDED.name_cn, ${products.nameCn})`,
        specCn: drizzleSql`COALESCE(EXCLUDED.spec_cn, ${products.specCn})`,
        qtyPerVehicle: drizzleSql`EXCLUDED.qty_per_vehicle`,
        dealerCost: drizzleSql`EXCLUDED.dealer_cost`,
        unit: drizzleSql`EXCLUDED.unit`,
        retailPrice: drizzleSql`EXCLUDED.retail_price`,
        fitment: drizzleSql`EXCLUDED.fitment`,
        primaryImageR2Key: drizzleSql`COALESCE(EXCLUDED.primary_image_r2_key, ${products.primaryImageR2Key})`,
        sourceFileSha256: drizzleSql`EXCLUDED.source_file_sha256`,
        sourceSheet: drizzleSql`EXCLUDED.source_sheet`,
        sourceRowIndex: drizzleSql`EXCLUDED.source_row_index`,
        updatedAt: drizzleSql`now()`,
      },
    })
    .returning({ productId: products.id });

  if (!inserted[0]) {
    throw new Error(`upsertProduct: no row returned for ${row.partNumber}`);
  }

  // If the source row provided an OLD PART NUMBER, persist it as an alias.
  if (row.partNumberAlias) {
    await db
      .insert(partNumberAliases)
      .values({
        productId: inserted[0].productId,
        alias: row.partNumberAlias,
        aliasNorm: row.partNumberAlias.toUpperCase().replace(/\s+/g, ""),
        aliasType: "old",
      })
      .onConflictDoNothing();
  }

  return { productId: inserted[0].productId, inserted: true };
}

/**
 * Bulk variant of upsertProduct. Runs all upserts inside a single transaction
 * for atomicity. Use with care — batch size should be O(thousands), not
 * O(hundreds-of-thousands), to avoid long-running transactions.
 */
export async function upsertProductsBatch(
  inputs: ReadonlyArray<ProductPersistInput>,
): Promise<ProductUpsertResult[]> {
  return db.transaction(async () => {
    const out: ProductUpsertResult[] = [];
    for (const input of inputs) {
      out.push(await upsertProduct(input));
    }
    return out;
  });
}
