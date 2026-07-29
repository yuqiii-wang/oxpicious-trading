/**
 * Frontend LRU cache with TTL — mirrors api/lib/lru-cache.ts.
 *
 * Used by api-client.ts to avoid re-fetching the same endpoint within the
 * TTL window. Keyed by URL string. Entries are evicted by LRU policy when
 * `maxSize` is exceeded, and expired automatically on `get`.
 */
export interface LruEntry<T> {
  value: T;
  expiresAt: number;
}

export class LruCache<T> {
  private map = new Map<string, LruEntry<T>>();
  private readonly maxSize: number;
  private readonly ttlMs: number;

  constructor(maxSize = 100, ttlMs = 5 * 60 * 1000) {
    this.maxSize = maxSize;
    this.ttlMs = ttlMs;
  }

  get(key: string): T | undefined {
    const entry = this.map.get(key);
    if (!entry) return undefined;
    if (Date.now() > entry.expiresAt) {
      this.map.delete(key);
      return undefined;
    }
    // Refresh insertion order (most-recently-used at end)
    this.map.delete(key);
    this.map.set(key, entry);
    return entry.value;
  }

  set(key: string, value: T): void {
    if (this.map.has(key)) {
      this.map.delete(key);
    } else if (this.map.size >= this.maxSize) {
      // Evict oldest entry (first key in insertion order)
      const oldestKey = this.map.keys().next().value;
      if (oldestKey !== undefined) {
        this.map.delete(oldestKey);
      }
    }
    this.map.set(key, { value, expiresAt: Date.now() + this.ttlMs });
  }

  clear(): void {
    this.map.clear();
  }

  /** Remove one entry by exact key. No-op if the key is not present. */
  delete(key: string): void {
    this.map.delete(key);
  }

  /** Remove every entry whose key starts with `prefix`. Used by the
   *  page-level refresh to invalidate all URLs that share a route prefix
   *  (e.g. all "/api/debt-baseline*" entries). */
  deletePrefix(prefix: string): void {
    if (!prefix) return;
    for (const key of Array.from(this.map.keys())) {
      if (key.startsWith(prefix)) {
        this.map.delete(key);
      }
    }
  }

  get size(): number {
    return this.map.size;
  }
}
