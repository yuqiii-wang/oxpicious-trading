/**
 * Global filter / theme state for the data_viz dashboard.
 */
import { create } from "zustand";

export type ThemeMode = "light" | "dark";

export interface SnapshotDate {
  label: string;
  date: string;
}

interface AppState {
  /** Light or dark mode (Material Design). */
  themeMode: ThemeMode;
  toggleTheme: () => void;
  setThemeMode: (mode: ThemeMode) => void;

  /** Global date-range filter applied to all dashboards. */
  startDate: string | null;
  endDate: string | null;
  setStartDate: (d: string | null) => void;
  setEndDate: (d: string | null) => void;
  setDateRange: (start: string | null, end: string | null) => void;

  /** SZSE Options — currently selected underlying. */
  underlyingCode: string;
  setUnderlyingCode: (code: string) => void;

  /** SZSE Options — 4 snapshot dates (Q4 start / last quarter / last month / latest). */
  snapshotDates: SnapshotDate[];
  setSnapshotDate: (idx: number, date: string) => void;
  setSnapshotDates: (dates: SnapshotDate[]) => void;

  /** ETF + Margin — currently selected L1 sector id (e.g. "FIN", "TECH", "BROAD"). */
  sectorId: string | null;
  setSectorId: (id: string | null) => void;

  /** ETF + Margin — currently selected L2 industry slug (e.g. "banks", "semi"). */
  industrySlug: string | null;
  setIndustrySlug: (slug: string | null) => void;

  /** Exchange filter — 'SS' (SSE), 'SZ' (SZSE), 'BJ' (BSE), or null (All). */
  exchange: string | null;
  setExchange: (ex: string | null) => void;

  /** Legacy theme slug — kept for backward compat, mapped to industrySlug. */
  themeSlug: string | null;
  setThemeSlug: (slug: string | null) => void;
}

const DEFAULT_SNAPSHOTS: SnapshotDate[] = [
  { label: "Q4 Start", date: "" },
  { label: "Last Quarter", date: "" },
  { label: "Last Month", date: "" },
  { label: "Latest", date: "" },
];

export const useStore = create<AppState>((set) => ({
  themeMode: "light",
  toggleTheme: () =>
    set((s) => ({ themeMode: s.themeMode === "light" ? "dark" : "light" })),
  setThemeMode: (mode) => set({ themeMode: mode }),

  startDate: null,
  endDate: null,
  setStartDate: (d) => set({ startDate: d }),
  setEndDate: (d) => set({ endDate: d }),
  setDateRange: (start, end) => set({ startDate: start, endDate: end }),

  underlyingCode: "159919",
  setUnderlyingCode: (code) => set({ underlyingCode: code }),

  snapshotDates: DEFAULT_SNAPSHOTS,
  setSnapshotDate: (idx, date) =>
    set((s) => {
      const next = s.snapshotDates.map((sd, i) =>
        i === idx ? { ...sd, date } : sd,
      );
      return { snapshotDates: next };
    }),
  setSnapshotDates: (dates) => set({ snapshotDates: dates }),

  sectorId: "BROAD",
  setSectorId: (id) => set({ sectorId: id, industrySlug: null }),

  industrySlug: null,
  setIndustrySlug: (slug) => set({ industrySlug: slug }),

  exchange: null,
  setExchange: (ex) => set({ exchange: ex }),

  themeSlug: null,
  setThemeSlug: (slug) => set({ industrySlug: slug }),
}));
