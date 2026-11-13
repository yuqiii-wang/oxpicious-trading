/**
 * usePanelFold — the AI summary page's foldable left-panel state, shared
 * by the Scopes / Prompts / Run Limits panels: whether the panel is
 * expanded or folded into its slim vertical strip, persisted per panel
 * in localStorage (`ai-summary:fold:<key>`) so a reload keeps the
 * user's layout. First visit falls back to `defaultExpanded` — the two
 * working panels (Scopes, Prompts) start expanded, the run-limits
 * config panel starts folded.
 */
import { useCallback, useEffect, useState } from "react";

const KEY_PREFIX = "ai-summary:fold:";

/** Restore the persisted fold state; falls back to the panel default. */
function readFold(key: string, defaultExpanded: boolean): boolean {
  try {
    const raw = localStorage.getItem(KEY_PREFIX + key);
    if (raw === "expanded") return true;
    if (raw === "folded") return false;
  } catch {
    // corrupted/unavailable storage → default
  }
  return defaultExpanded;
}

/** `[expanded, toggleExpanded]` — every fold click flips and persists. */
export function usePanelFold(
  key: string,
  defaultExpanded: boolean,
): [boolean, () => void] {
  const [expanded, setExpanded] = useState(() => readFold(key, defaultExpanded));
  const toggleExpanded = useCallback(() => setExpanded((prev) => !prev), []);
  useEffect(() => {
    try {
      localStorage.setItem(KEY_PREFIX + key, expanded ? "expanded" : "folded");
    } catch {
      // storage unavailable — the fold still works this session
    }
  }, [key, expanded]);
  return [expanded, toggleExpanded];
}
