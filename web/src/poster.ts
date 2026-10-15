/** Upstream poster CDNs hotlink-guard, so a broken cover is replaced by the
 * feicms mark rather than leaving an empty frame. */
export const POSTER_PLACEHOLDER = "/poster-placeholder.svg";

export function posterFallback(e: { currentTarget: HTMLImageElement }) {
  const img = e.currentTarget;
  if (img.dataset.fallback) return;
  img.dataset.fallback = "1";
  img.src = POSTER_PLACEHOLDER;
}
