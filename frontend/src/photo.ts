/** Shrink a camera photo before it leaves the phone.
 *
 *  A modern phone camera produces 3-8 MB at 4000px wide. Sending that raw costs
 *  the student real money on a metered connection, takes seconds on a weak link,
 *  and buys nothing: the vision call runs at `detail: "low"`, which downsamples
 *  server-side anyway. A 1000px JPEG is enough to read printed and neat
 *  handwritten text off a page, and lands around 150-300 kB.
 *
 *  Done with canvas rather than a library. Every image-resize package is 30 kB+
 *  against a 40 kB total budget, to do what four lines of drawImage already does.
 */

/** Long edge in pixels after downscaling.
 *
 *  1600 rather than a phone's native 4000: the vision model reads the image in
 *  tiles, so past roughly this point each extra pixel costs input tokens and
 *  upload seconds without making a pencil stroke any more legible. Below about
 *  1200, thin handwriting starts to break up. */
const MAX_EDGE = 1600;

/** Below this the JPEG artefacts start eating thin handwriting strokes. */
const QUALITY = 0.82;

/** Hard stop matching the server's MAX_IMAGE_BYTES, so an impossible upload
 *  fails here with a clear message rather than after the whole upload wait.
 *  1600px at q0.82 normally lands around 400-900 kB, so this is a ceiling for
 *  odd cases (a dense photo of a whole page), not the expected size. */
const MAX_BYTES = 5_000_000;

export class PhotoTooBig extends Error {}

export async function downscale(file: File): Promise<Blob> {
  const bitmap = await createImageBitmap(file);

  const scale = Math.min(1, MAX_EDGE / Math.max(bitmap.width, bitmap.height));
  const w = Math.round(bitmap.width * scale);
  const h = Math.round(bitmap.height * scale);

  const canvas = document.createElement("canvas");
  canvas.width = w;
  canvas.height = h;

  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("Canvas unavailable");
  // Better downsampling of fine strokes than the browser default, which matters
  // when the subject is handwriting rather than a photograph.
  ctx.imageSmoothingQuality = "high";
  ctx.drawImage(bitmap, 0, 0, w, h);
  // Frees the decoded original immediately rather than at the next GC. On a
  // 2 GB phone a 4000px bitmap is tens of megabytes.
  bitmap.close();

  const blob = await new Promise<Blob | null>((resolve) =>
    canvas.toBlob(resolve, "image/jpeg", QUALITY),
  );
  if (!blob) throw new Error("Could not process that photo");
  if (blob.size > MAX_BYTES) throw new PhotoTooBig("That photo is too large.");
  return blob;
}
