const MEDIA_ROUTE_ROOTS = ["clips", "exports", "recordings", "cache"];

/** Convert a database filesystem path into a path served by the web gateway. */
export function mediaPathToUrl(path: string | null | undefined): string {
  if (!path) {
    return "";
  }

  const normalized = path.replaceAll("\\", "/");
  const folded = normalized.toLowerCase();

  for (const root of MEDIA_ROUTE_ROOTS) {
    const marker = `/${root}/`;
    const index = folded.lastIndexOf(marker);
    if (index >= 0) {
      return normalized.slice(index + 1);
    }
  }

  // Preserve relative API paths returned by newer endpoints.
  return normalized.replace(/^\/+/, "");
}
