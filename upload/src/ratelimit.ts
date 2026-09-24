// Fixed-window rate limit per client IP, in memory (one process; resets on restart).
export class RateLimit {
  private hits = new Map<string, { start: number; count: number }>();

  constructor(readonly limit: number, readonly windowMs: number, private now = () => Date.now()) {}

  /** Count a request; false if the IP is over its limit for the current window. */
  take(ip: string): boolean {
    const t = this.now();
    if (this.hits.size > 10_000) this.sweep(t);
    const h = this.hits.get(ip);
    if (!h || t - h.start >= this.windowMs) {
      this.hits.set(ip, { start: t, count: 1 });
      return true;
    }
    h.count++;
    return h.count <= this.limit;
  }

  /** Seconds until the IP's window resets. */
  retryAfter(ip: string): number {
    const h = this.hits.get(ip);
    return h ? Math.max(1, Math.ceil((h.start + this.windowMs - this.now()) / 1000)) : 0;
  }

  private sweep(t: number) {
    for (const [ip, h] of this.hits) if (t - h.start >= this.windowMs) this.hits.delete(ip);
  }
}
