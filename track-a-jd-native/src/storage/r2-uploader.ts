/**
 * R2 (S3-compatible) image uploader.
 *
 * Content-addressed: object key = `sha256/<aa>/<bb>/<full>.<ext>` where
 * aa/bb are the first 2 + next 2 hex chars. Identical bytes → identical
 * key, so concurrent uploads from many dealers natively dedup.
 *
 * Operation flow per image:
 *   1. SHA-256 the bytes.
 *   2. HEAD the key; if 200 → skip PUT (saves egress + cost).
 *   3. PUT if absent.
 *   4. Return key + public URL for persistence.
 *
 * Multipart upload is used automatically for >5 MB by @aws-sdk/lib-storage.
 */
import { createHash } from "node:crypto";
import { Upload } from "@aws-sdk/lib-storage";
import {
  HeadObjectCommand,
  S3Client,
  S3ServiceException,
} from "@aws-sdk/client-s3";
import { env } from "../lib/env.js";
import { logger } from "../lib/logger.js";

const log = logger.child({ component: "r2-uploader" });

const s3 = new S3Client({
  region: env.S3_REGION,
  endpoint: env.S3_ENDPOINT,
  credentials: {
    accessKeyId: env.S3_ACCESS_KEY,
    secretAccessKey: env.S3_SECRET_KEY,
  },
  forcePathStyle: env.S3_FORCE_PATH_STYLE,
});

export interface UploadResult {
  key: string;
  url: string;
  sha256: string;
  bytes: number;
  /** True if the object was newly uploaded; false if it already existed. */
  uploaded: boolean;
}

/**
 * Upload bytes to R2 with content-addressed key. Idempotent on retry.
 */
export async function uploadImage(
  bytes: Buffer,
  ext: string,
  contentType?: string,
): Promise<UploadResult> {
  const sha256 = createHash("sha256").update(bytes).digest("hex");
  const cleanExt = ext.startsWith(".") ? ext.slice(1) : ext;
  const key = `sha256/${sha256.slice(0, 2)}/${sha256.slice(2, 4)}/${sha256}.${cleanExt}`;

  // Skip if already present.
  if (await objectExists(key)) {
    log.debug({ key, sha256: sha256.slice(0, 12) }, "image exists, skipping upload");
    return {
      key,
      url: keyToUrl(key),
      sha256,
      bytes: bytes.length,
      uploaded: false,
    };
  }

  const start = Date.now();
  const upload = new Upload({
    client: s3,
    params: {
      Bucket: env.S3_BUCKET,
      Key: key,
      Body: bytes,
      ContentType: contentType ?? guessContentType(cleanExt),
    },
    queueSize: 4,
    partSize: 5 * 1024 * 1024,
  });

  await upload.done();
  log.debug(
    { key, sha256: sha256.slice(0, 12), bytes: bytes.length, ms: Date.now() - start },
    "image uploaded",
  );

  return {
    key,
    url: keyToUrl(key),
    sha256,
    bytes: bytes.length,
    uploaded: true,
  };
}

/** HEAD the object; return true on 200, false on 404. Rethrow other errors. */
async function objectExists(key: string): Promise<boolean> {
  try {
    await s3.send(new HeadObjectCommand({ Bucket: env.S3_BUCKET, Key: key }));
    return true;
  } catch (err) {
    if (err instanceof S3ServiceException) {
      const statusCode = err.$metadata?.httpStatusCode;
      if (statusCode === 404) return false;
      // 403 may indicate "no permission to head" which we treat as "doesn't exist"
      // for upload-only operations. Log and proceed.
      if (statusCode === 403) {
        log.warn({ key, status: 403 }, "head returned 403; treating as absent");
        return false;
      }
    }
    throw err;
  }
}

function keyToUrl(key: string): string {
  // For MinIO local: http://localhost:9000/<bucket>/<key>
  // For R2:        https://pub-<id>.r2.dev/<key>   (need public bucket)
  // Use the configured endpoint as the host.
  const endpoint = env.S3_ENDPOINT.replace(/\/$/, "");
  if (env.S3_FORCE_PATH_STYLE) {
    return `${endpoint}/${env.S3_BUCKET}/${key}`;
  }
  // virtual-hosted style: <bucket>.<host>/<key>
  const url = new URL(endpoint);
  return `${url.protocol}//${env.S3_BUCKET}.${url.host}/${key}`;
}

function guessContentType(ext: string): string {
  const lower = ext.toLowerCase();
  if (lower === "jpg" || lower === "jpeg") return "image/jpeg";
  if (lower === "png") return "image/png";
  if (lower === "gif") return "image/gif";
  if (lower === "webp") return "image/webp";
  return "application/octet-stream";
}

export async function closeR2Client(): Promise<void> {
  s3.destroy();
}
