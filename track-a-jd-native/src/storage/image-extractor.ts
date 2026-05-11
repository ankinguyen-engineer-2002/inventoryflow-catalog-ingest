/**
 * Extract embedded image bytes from an xlsx file.
 *
 * Companion to drawing-parser.ts. The parser tells us "image42.jpg lives
 * inside this zip"; this module actually reads the bytes for upload.
 *
 * Why separate from the parser? Many callers only need anchor metadata
 * (to map rows ↔ images). Only the upload path needs bytes, which can
 * be GB-scale in aggregate.
 */
import unzipper from "unzipper";

const cache = new WeakMap<object, Map<string, Buffer>>();

/** Strongly-typed wrapper around the unzipper directory open result. */
interface Directory {
  files: Array<{
    path: string;
    type: string;
    buffer(): Promise<Buffer>;
  }>;
}

/** Open the xlsx zip once and return a directory handle. */
export async function openImageArchive(xlsxPath: string): Promise<Directory> {
  return (await unzipper.Open.file(xlsxPath)) as unknown as Directory;
}

/**
 * Get bytes for an image embedded in the xlsx.
 *
 * `imageFile` is the bare filename from drawing rels (eg "image42.jpg").
 * We resolve it to `xl/media/<imageFile>` inside the zip.
 *
 * The directory handle is keyed in a WeakMap so repeated reads from the
 * same directory short-circuit zip table-of-contents lookups.
 */
export async function getImageBytes(
  directory: Directory,
  imageFile: string,
): Promise<Buffer | null> {
  let dirCache = cache.get(directory);
  if (!dirCache) {
    dirCache = new Map();
    cache.set(directory, dirCache);
  }
  const cached = dirCache.get(imageFile);
  if (cached) return cached;

  const fullPath = `xl/media/${imageFile}`;
  const entry = directory.files.find((f) => f.path === fullPath);
  if (!entry) return null;

  const buf = await entry.buffer();
  dirCache.set(imageFile, buf);
  return buf;
}
