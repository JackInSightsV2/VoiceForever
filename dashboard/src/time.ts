// The pipeline writes naive local ISO times (datetime.now().isoformat(timespec="seconds")); match that format.

export function localIso(d: Date): string {
  const off = d.getTimezoneOffset() * 60_000;
  return new Date(d.getTime() - off).toISOString().slice(0, 19);
}

/** Parse a naive local ISO time (no offset: ECMAScript reads it as local). */
export function parseLocal(s: string | null | undefined): Date | null {
  if (!s) return null;
  const d = new Date(s.includes("T") ? s : s.replace(" ", "T"));
  return Number.isNaN(d.getTime()) ? null : d;
}

export function secondsBetween(a: Date, b: Date): number {
  return (b.getTime() - a.getTime()) / 1000;
}
