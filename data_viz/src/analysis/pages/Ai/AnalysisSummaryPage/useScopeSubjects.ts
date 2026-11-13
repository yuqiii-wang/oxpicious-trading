/** The page's scope layer — the classification nav plus the MULTI-pick
 *  subject list the Scopes panel's tag bar reads/writes. */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  findCodeInStrategyThemes,
  findCodeInThemes,
} from "@/components/CodeSearchBar";
import { DEFAULTS_BY_KIND } from "@/shared/components/sec-classification/constants";
import { useSecNav } from "@/shared/components/sec-nav";
import type { SecNavSecType } from "@/shared/components/sec-nav";
import { SUMMARY_THEMES_SOURCES } from "../constants";
import type { ScopeSubjectEntry } from "../AnalysisItemsTree";
import { itemKindFor } from "./helpers";

/** The scope nav, the subject tags and their mutators. `nav` carries
 *  the whole SecNav object the sections render from. */
export function useScopeSubjects() {
  // ---- Scope nav (classification-nav kit; the pick focuses the run) ----
  const nav = useSecNav({
    themesSources: SUMMARY_THEMES_SOURCES,
    defaultSecType: "index",
    dataLabel: "summary scope",
  });

  // ---- Scope subjects (the classification nav's MULTI-pick + the
  // Scopes panel's tag bar) ----
  // The run's scope subject list: the nav's L3 row is MULTI-SELECT (every
  // tick lands here), the Scopes panel's tag + input bar reads/writes the
  // SAME list (tags default-linked to the nav picks, typed codes join
  // them, a tag's cross removes the pick), and the top CodeSearchBar adds
  // its resolved code too. A sec_type switch wipes the list — a code of
  // one universe is meaningless under another — and re-seeds the
  // DEFAULTS_BY_KIND subject so the out-of-box run keeps its canonical
  // focus (the multi-select nav skips its own single-select defaults).
  const [scopeCodes, setScopeCodes] = useState<string[]>([]);
  const seededSecTypeRef = useRef<SecNavSecType | null>(null);
  const handleSecTypeChange = (t: SecNavSecType) => {
    seededSecTypeRef.current = null;
    setScopeCodes([]);
    nav.setSecType(t);
  };

  /** Suffix-insensitive code key (000001.SZ ≙ 000001) — matches the nav's
   *  own normCode so a typed suffixed code joins its bare twin's tag. */
  const subjectKey = (code: string): string =>
    code.toUpperCase().replace(/\.(SS|SZ|SH|BJ|HK)$/i, "");

  // code → display name across BOTH trees (the tags' labels and the
  // generate request's name field; a typed code outside the trees shows
  // and rides bare).
  const subjectNameMap = useMemo(() => {
    const m = new Map<string, string>();
    const add = (code: string, name: string) => {
      const key = subjectKey(code);
      if (!m.has(key)) m.set(key, name);
    };
    for (const s of nav.sectors) {
      for (const ind of s.industries) {
        for (const it of ind.items) add(it.code, it.name);
      }
    }
    for (const s of nav.strategies) {
      for (const th of s.industries) {
        for (const it of th.items) add(it.code, it.name);
      }
    }
    return m;
  }, [nav.sectors, nav.strategies]);

  // The input bar's suggestion list — the loaded trees' items, deduped by
  // normalized code (the Autocomplete's filter narrows them on typing).
  const subjectSuggestions = useMemo(() => {
    const out: Array<{ code: string; name: string }> = [];
    const seen = new Set<string>();
    for (const tree of [nav.sectors, nav.strategies]) {
      for (const s of tree) {
        for (const ind of s.industries) {
          for (const it of ind.items) {
            const key = subjectKey(it.code);
            if (!seen.has(key)) {
              seen.add(key);
              out.push({ code: it.code, name: it.name });
            }
          }
        }
      }
    }
    return out;
  }, [nav.sectors, nav.strategies]);

  // The tags the Scopes panel renders (code + resolved name).
  const subjects: ScopeSubjectEntry[] = useMemo(
    () =>
      scopeCodes.map((code) => {
        const name = subjectNameMap.get(subjectKey(code));
        return name === undefined ? { code } : { code, name };
      }),
    [scopeCodes, subjectNameMap],
  );

  const addScopeCode = useCallback((code: string) => {
    const key = subjectKey(code);
    setScopeCodes((prev) =>
      prev.some((c) => subjectKey(c) === key) ? prev : [...prev, code],
    );
  }, []);

  const removeScopeCode = useCallback((code: string) => {
    const key = subjectKey(code);
    setScopeCodes((prev) => prev.filter((c) => subjectKey(c) !== key));
  }, []);

  // Seed the subjects once per sec_type: the SAME default subject the
  // single-select nav drilled into (DEFAULTS_BY_KIND — the canonical
  // code, its industry's first item when classification drift moved it),
  // so the out-of-box run keeps the canonical focus instead of going
  // market-wide by accident. useNavDefaults no-ops in multi-select mode,
  // so the page owns the seed.
  useEffect(() => {
    if (seededSecTypeRef.current === nav.secType) return;
    const kind = itemKindFor(nav.secType);
    if (kind === undefined) return; // "all" — nothing to seed
    const def = DEFAULTS_BY_KIND[kind];
    if (def === undefined) return;
    const asStrategy = nav.strategies.find(
      (s) => s.sector_id === def.sectorId,
    );
    const asSector = nav.sectors.find((s) => s.sector_id === def.sectorId);
    const node = asStrategy ?? asSector;
    if (node === undefined) return; // trees not loaded yet — retry when they land
    seededSecTypeRef.current = nav.secType;
    const industry =
      node.industries.find((i) => i.industry_slug === def.industrySlug) ??
      node.industries[0];
    const item =
      industry?.items.find((it) => it.code === def.itemCode) ??
      industry?.items[0];
    if (industry === undefined || item === undefined) return;
    // Browse the nav to the default's location so the L3 chips show
    // where the seed came from.
    if (asStrategy !== undefined) {
      nav.handleStrategyChange(def.sectorId);
      nav.handleThemeChange(industry.industry_slug);
    } else {
      nav.handleSectorChange(def.sectorId);
      nav.handleIndustryChange(industry.industry_slug);
    }
    setScopeCodes([item.code]);
    // The nav handlers are stable in behavior but recreated per render;
    // the seeded ref makes re-runs no-ops.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nav.secType, nav.sectors, nav.strategies]);

  // The top search bar keeps its nav-jump job (resolve + highlight where
  // a code lives) AND adds the resolved code as a subject tag — the
  // pre-multi behavior had the search pick BE the scope, so a searched
  // code landing in the tags is the least surprising continuation.
  const handleScopeSearch = (code: string) => {
    const found =
      findCodeInThemes(nav.sectors, code) !== null ||
      findCodeInStrategyThemes(nav.strategies, code) !== null;
    nav.handleSearch(code);
    if (found) addScopeCode(code);
  };

  // The multi-pick summary label (the top caption + the history header):
  // the kind + count + the first tags, then "+n more"; empty (or the
  // "all" sec_type) reads market-wide.
  const scopeLabel =
    subjects.length === 0
      ? nav.secType === "all"
        ? "all (market-wide)"
        : "market-wide"
      : `${itemKindFor(nav.secType) ?? "Subject"} ×${subjects.length}: ` +
        subjects
          .slice(0, 3)
          .map((s) => (s.name === undefined ? s.code : `${s.code} ${s.name}`))
          .join(" + ") +
        (subjects.length > 3 ? ` +${subjects.length - 3} more` : "");

  return {
    nav,
    scopeCodes,
    setScopeCodes,
    subjects,
    subjectSuggestions,
    subjectNameMap,
    subjectKey,
    addScopeCode,
    removeScopeCode,
    handleSecTypeChange,
    handleScopeSearch,
    scopeLabel,
  };
}

export type ScopeSubjects = ReturnType<typeof useScopeSubjects>;
