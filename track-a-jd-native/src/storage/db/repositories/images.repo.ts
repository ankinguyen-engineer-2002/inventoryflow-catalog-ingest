/**
 * Product images repository.
 *
 * Many-to-many between `products` and the canonical R2 image objects.
 * Idempotency comes from the PK (product_id, sha256) — re-inserting the
 * same association is a no-op.
 */
import { db } from "../client.js";
import { productImages } from "../schema.js";

export interface ProductImageLink {
  productId: number;
  r2Key: string;
  r2Url: string;
  sha256: string;
  sectionLabel?: string | null;
  sourceSheet?: string | null;
  widthPx?: number | null;
  heightPx?: number | null;
}

export async function linkImage(input: ProductImageLink): Promise<void> {
  await db
    .insert(productImages)
    .values({
      productId: input.productId,
      r2Key: input.r2Key,
      r2Url: input.r2Url,
      sha256: input.sha256,
      sectionLabel: input.sectionLabel ?? null,
      sourceSheet: input.sourceSheet ?? null,
      widthPx: input.widthPx ?? null,
      heightPx: input.heightPx ?? null,
    })
    .onConflictDoNothing();
}

export async function linkImagesBatch(inputs: ReadonlyArray<ProductImageLink>): Promise<void> {
  if (inputs.length === 0) return;
  await db
    .insert(productImages)
    .values(
      inputs.map((i) => ({
        productId: i.productId,
        r2Key: i.r2Key,
        r2Url: i.r2Url,
        sha256: i.sha256,
        sectionLabel: i.sectionLabel ?? null,
        sourceSheet: i.sourceSheet ?? null,
        widthPx: i.widthPx ?? null,
        heightPx: i.heightPx ?? null,
      })),
    )
    .onConflictDoNothing();
}
