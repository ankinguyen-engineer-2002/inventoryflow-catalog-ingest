# ADR-003: SHA-256-keyed idempotent image upload

## Status
Accepted — 2026-05-11

## Context

The xlsx contains 1586 embedded schematic images. Dealers re-send updated files weekly with ~90% image overlap. Naively uploading every image on every run wastes R2 storage, request budget, and time. Worse, mutable URLs (`image_42.jpg`) become a versioning nightmare across dealers.

Three options:

- **A. Sequential keys** (`dealer_X/image_1.jpg`, `image_2.jpg`, …) — simple, but identical images get duplicated across dealers.
- **B. UUID per upload** — guaranteed unique, but identical images uploaded forever.
- **C. Content-addressed (SHA-256) keys** — `sha256/ab/cd/abcd1234...jpg` — identical content = identical key. Upload becomes a no-op via HEAD-check.

## Decision

Use SHA-256 content-addressed keys. Algorithm:

```
1. Compute SHA-256 of image bytes
2. R2 HEAD on `sha256/{first2}/{next2}/{full}.{ext}`
3. If 200 → skip upload, link to existing object
4. If 404 → PUT
5. INSERT into product_images (product_id, r2_key, sha256, …)
```

R2 key structure:

```
catalog/
└── sha256/
    └── ab/                  ← first 2 hex chars (256-way fan-out)
        └── cd/              ← next 2 hex chars (65k-way fan-out per top)
            └── abcd1234efgh....{jpg|png}
```

Two-level prefix prevents single-prefix hot-spotting if R2 ever applies S3-style prefix-based throttling (S3 documents this; R2 hasn't yet but the pattern is free insurance).

## AI suggestion vs my override

**Claude initially suggested** UUID-keyed uploads with a separate `image_hash_index` table for deduplication.

**I overrode** because:

1. SHA-256 already gives global content addressing — no separate index table needed.
2. Idempotency becomes a property of the key, not of database state. `pnpm ingest` re-runs become free.
3. The HEAD-check pattern is the same one S3, IPFS, Git, and content-delivery networks use. Battle-tested at scale.
4. The separate index table proposed by the LLM adds a write path and a consistency window — both unnecessary.

## Trade-offs accepted

- **Reverse lookup is harder**: "give me all images for dealer X" requires the `product_images` table (which we have anyway). Direct R2 list-by-prefix won't filter by dealer.
- **Image renames are impossible** — SHA changes if bytes change. Acceptable; we never rename.
- **Cannot "expire" old content per-dealer** because content is shared across dealers. Mitigated: per-dealer image references are tracked in `product_images.source_dealer_id`; a dealer can be "off-boarded" by deleting their rows without touching the R2 object.
- **One-byte change = full re-upload** of that image (unavoidable, but acceptable — OEM rarely tweaks images).

## When to revisit

- If R2 egress becomes a major cost (>$500/mo attributable to image serving), introduce a CDN cache or signed-URL layer in front. Doesn't change the key strategy.
- If we add user-uploaded images (vs OEM-supplied), revisit: user uploads need per-upload metadata that content-addressing alone doesn't capture.

## Sources

- AWS S3 best practices on prefix design: https://docs.aws.amazon.com/AmazonS3/latest/userguide/optimizing-performance.html (retrieved 2026-05-11).
- Cloudflare R2 API parity with S3: https://developers.cloudflare.com/r2/api/s3/api/ (retrieved 2026-05-11).
- Git object storage uses identical two-char prefix scheme (`.git/objects/ab/cdef…`).
- Inspired by IPFS CID structure (multihash + base32).
