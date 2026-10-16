/**
 * Hooks of the Service Monitor page.
 */
import { useEffect, useState } from "react";

/** Ticking wall clock (1s) — beat ages count up live between API polls. */
export function useNowTick(intervalMs = 1000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(id);
  }, [intervalMs]);
  return now;
}
