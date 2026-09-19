/**
 * Reactive chart theme — the single way chart components resolve light/dark.
 *
 * Replaces the three historic patterns: prop drilling `themeMode` from pages,
 * and (worse) non-reactive `useStore.getState().themeMode` inside option
 * builders, which silently kept stale colors after a theme toggle until some
 * other dependency changed. Subscribing here re-renders (and thus rebuilds
 * the option) on every toggle.
 */
import { useStore } from "@/store/filters";
import type { ThemeMode } from "@/store/filters";

export function useChartThemeMode(): ThemeMode {
  return useStore((s) => s.themeMode);
}
