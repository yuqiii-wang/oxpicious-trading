/**
 * useTableHeaderFilters — config-driven, OPT-IN per-column header filters
 * for tables using the shared expanded-table style.
 *
 * Three filter types:
 *   • "ticks" — checkbox list of the column's distinct values (labels /
 *     limited option sets: side, 1%/5% buckets, window sizes, ...). Match =
 *     value ∈ ticked set; empty selection = no filter.
 *   • "date"  — from/to range over comparable date strings (month inputs
 *     "YYYY-MM" or date inputs "YYYY-MM-DD"); inclusive bounds, empty =
 *     unbounded on that side.
 *   • "range" — min/max over continuous numeric magnitudes (prices,
 *     amounts, ratios); rows with a null/non-numeric value never match an
 *     active range. NOT for numeric columns with a small discrete value set
 *     — those are ticks.
 *
 * Only columns listed in `defs` get a filter menu; every other header keeps
 * its plain style. Filters AND across columns and default to no selection
 * ("(All)"). Menus are PREFILLED with the data's own bounds so the inputs
 * never look empty: ticks show every value ticked, date from/to show the
 * earliest/latest value, range min/max show the data extremes — while the
 * underlying state stays "unset" (null/empty = unbounded, badge off), so a
 * prefilled filter does not filter anything until the user edits a bound.
 * `scopeDeps` resets all filters when the table's data scope changes (new
 * code / sec_type / refresh) — prefill then re-derives from the new rows.
 *
 * Returns `filtered` rows, a `menuFor(def)` header renderer that picks the
 * right menu component, `anyActive` (any column currently filtering) and
 * `reset`.
 */
import { useEffect, useMemo, useState, type ReactNode } from "react";
import HeaderFilterMenu from "@/components/HeaderFilterMenu";
import HeaderDateFilterMenu from "@/components/HeaderDateFilterMenu";
import HeaderNumericRangeFilterMenu from "@/components/HeaderNumericRangeFilterMenu";

export type HeaderFilterType = "ticks" | "date" | "range";

export interface HeaderFilterDef<T> {
  /** Unique column key (also the filter-state key). */
  key: string;
  /** Header label (rendered before the filter button). */
  label: string;
  type: HeaderFilterType;
  /** The row's filterable value — ticks: string; date: comparable date
   *  string ("YYYY-MM" / "YYYY-MM-DD"); range: number. null values are
   *  excluded from ticks options and never match an active date/range. */
  value: (r: T) => string | number | null;
  /** date type only — input granularity (default "date"). */
  granularity?: "date" | "month";
}

interface TicksState {
  kind: "ticks";
  selected: string[];
}
interface DateState {
  kind: "date";
  from: string | null;
  to: string | null;
}
interface RangeState {
  kind: "range";
  min: number | null;
  max: number | null;
}
type ColFilterState = TicksState | DateState | RangeState;

const defaultState = (type: HeaderFilterType): ColFilterState =>
  type === "ticks"
    ? { kind: "ticks", selected: [] }
    : type === "date"
      ? { kind: "date", from: null, to: null }
      : { kind: "range", min: null, max: null };

export function useTableHeaderFilters<T>(
  defs: HeaderFilterDef<T>[],
  rows: T[],
  scopeDeps: unknown[] = [],
) {
  const [state, setState] = useState<Record<string, ColFilterState>>({});
  // New scope → the previous column values don't apply: clear all filters.
  useEffect(() => {
    setState({});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, scopeDeps);

  const getState = (d: HeaderFilterDef<T>): ColFilterState =>
    state[d.key] ?? defaultState(d.type);

  const isActive = (d: HeaderFilterDef<T>): boolean => {
    const s = getState(d);
    if (s.kind === "ticks") return s.selected.length > 0;
    if (s.kind === "date") return s.from != null || s.to != null;
    return s.min != null || s.max != null;
  };

  const matches = (d: HeaderFilterDef<T>, r: T): boolean => {
    const s = getState(d);
    const v = d.value(r);
    if (s.kind === "ticks") {
      return s.selected.length === 0 || (v != null && s.selected.includes(String(v)));
    }
    if (s.kind === "date") {
      if (s.from == null && s.to == null) return true;
      if (v == null) return false;
      const sv = String(v);
      return (s.from == null || sv >= s.from) && (s.to == null || sv <= s.to);
    }
    if (s.min == null && s.max == null) return true;
    if (typeof v !== "number" || !Number.isFinite(v)) return false;
    return (s.min == null || v >= s.min) && (s.max == null || v <= s.max);
  };

  // Distinct sorted values per ticks column — numeric sort when every value
  // is numeric, lexicographic otherwise (month strings sort correctly).
  const tickValues = useMemo(() => {
    const m = new Map<string, string[]>();
    for (const d of defs) {
      if (d.type !== "ticks") continue;
      const seen = new Set<string>();
      for (const r of rows) {
        const v = d.value(r);
        if (v != null) seen.add(String(v));
      }
      const vals = [...seen];
      if (vals.length > 0 && vals.every((v) => Number.isFinite(Number(v)))) {
        vals.sort((a, b) => Number(a) - Number(b));
      } else {
        vals.sort();
      }
      m.set(d.key, vals);
    }
    return m;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [defs, rows]);

  const filtered = useMemo(
    () => rows.filter((r) => defs.every((d) => matches(d, r))),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [rows, defs, state],
  );

  // Data-derived bounds per date/range column — the PREFILL shown in the
  // inputs while the bound is unset (null state). Date bounds are the
  // lexicographic min/max (earliest/latest) of the column's values; range
  // bounds the numeric min/max. Ticks need none: the tri-state menu already
  // renders every value ticked while no filter is set.
  const dataBounds = useMemo(() => {
    const m = new Map<string, { lo: string | number | null; hi: string | number | null }>();
    for (const d of defs) {
      if (d.type === "ticks") continue;
      if (d.type === "date") {
        let lo: string | null = null;
        let hi: string | null = null;
        for (const r of rows) {
          const v = d.value(r);
          if (v == null) continue;
          const s = String(v);
          if (lo == null || s < lo) lo = s;
          if (hi == null || s > hi) hi = s;
        }
        m.set(d.key, { lo, hi });
      } else {
        let lo: number | null = null;
        let hi: number | null = null;
        for (const r of rows) {
          const v = d.value(r);
          if (typeof v !== "number" || !Number.isFinite(v)) continue;
          if (lo == null || v < lo) lo = v;
          if (hi == null || v > hi) hi = v;
        }
        m.set(d.key, { lo, hi });
      }
    }
    return m;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [defs, rows]);

  const menuFor = (d: HeaderFilterDef<T>): ReactNode => {
    const s = getState(d);
    if (d.type === "ticks") {
      return (
        <HeaderFilterMenu
          label={d.label}
          values={tickValues.get(d.key) ?? []}
          selected={s.kind === "ticks" ? s.selected : []}
          onChange={(selected) => setFilter(d.key, { kind: "ticks", selected })}
        />
      );
    }
    if (d.type === "date") {
      const ds = s.kind === "date" ? s : defaultState("date");
      const b = dataBounds.get(d.key);
      return (
        <HeaderDateFilterMenu
          label={d.label}
          from={ds.kind === "date" ? ds.from : null}
          to={ds.kind === "date" ? ds.to : null}
          prefillFrom={(b?.lo as string | undefined) ?? null}
          prefillTo={(b?.hi as string | undefined) ?? null}
          granularity={d.granularity}
          onChange={(next) => setFilter(d.key, { kind: "date", ...next })}
        />
      );
    }
    const rs = s.kind === "range" ? s : defaultState("range");
    const b = dataBounds.get(d.key);
    return (
      <HeaderNumericRangeFilterMenu
        label={d.label}
        min={rs.kind === "range" ? rs.min : null}
        max={rs.kind === "range" ? rs.max : null}
        prefillMin={(b?.lo as number | undefined) ?? null}
        prefillMax={(b?.hi as number | undefined) ?? null}
        onChange={(next) => setFilter(d.key, { kind: "range", ...next })}
      />
    );
  };

  const setFilter = (key: string, s: ColFilterState) =>
    setState((prev) => ({ ...prev, [key]: s }));

  return {
    filtered,
    menuFor,
    /** True when ANY enabled column currently filters rows. */
    anyActive: defs.some(isActive),
    isActive,
    reset: () => setState({}),
  };
}

export default useTableHeaderFilters;
