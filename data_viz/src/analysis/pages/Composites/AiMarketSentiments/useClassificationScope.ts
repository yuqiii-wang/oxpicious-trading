/**
 * Classification multi-select scope for the Market Sentiments by AI and
 * News page (INDUSTRY mode) — the same model as Industry Sentiments:
 * multi-select L2 industries (slugs persist across sector switches) merged
 * non-exclusively with the RIGHT strategy→theme column into one feed scope
 * + the BenchmarkPriceChart's industry shade selections.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import type { SecNavState } from "@/shared/components/sec-nav";
import { normCode } from "./constants";

export function useClassificationScope(nav: SecNavState): {
  /** Multi-select L2 industry slugs (persist across sector switches). */
  selectedIndustrySlugs: string[];
  setSelectedIndustrySlugs: React.Dispatch<React.SetStateAction<string[]>>;
  /** Sector changes only update the browsing context — the multi-select
   *  selection persists (same semantics as Industry Sentiments). */
  handleSectorChange: (id: string | null) => void;
  /** BenchmarkPriceChart's shade selections: selected industries (LEFT) +
   *  strategy themes (RIGHT), each with a display label. */
  selectedIndustries: Array<{ id: string; label: string }>;
  /** The INDUSTRY-mode feed scope resolved from the classification pick
   *  (the DataViz AI page's mapping, widened to multi-select):
   *   • L2 industry chips → their canonical industry_ids;
   *   • a strategy/theme pick → the UNION of its member securities'
   *     industry_ids PLUS the themes' own industry_ids;
   *   • nothing picked → {} (unscoped — the feed shows all industries). */
  industryScope: { industry_ids?: string[] };
  /** Feed scope label — selected industries + strategy/themes joined, or
   *  "全部行业" when nothing is picked (unscoped feed). */
  scopeLabel: string;
} {
  // Multi-select L2 industries: slugs persist across sector switches so the
  // user can pick industries from multiple sectors.
  const [selectedIndustrySlugs, setSelectedIndustrySlugs] = useState<string[]>([]);

  // slug/label lookups across both trees.
  const slugToIndustryId = useMemo(() => {
    const m = new Map<string, string>();
    for (const s of nav.sectors) {
      for (const ind of s.industries) m.set(ind.industry_slug, ind.industry_id);
    }
    return m;
  }, [nav.sectors]);
  const slugToIndustryLabel = useMemo(() => {
    const m = new Map<string, string>();
    for (const s of nav.sectors) {
      for (const ind of s.industries) m.set(ind.industry_slug, ind.industry_label);
    }
    return m;
  }, [nav.sectors]);
  // code → owning industry (LEFT tree) — resolves strategy members to
  // industry tags for the feed scope (the DataViz AI page's idiom).
  const codeToIndustry = useMemo(() => {
    const m = new Map<string, { industryId: string; label: string }>();
    for (const s of nav.sectors) {
      for (const ind of s.industries) {
        for (const it of ind.items) {
          m.set(normCode(it.code), {
            industryId: ind.industry_id,
            label: `${s.sector_label} / ${ind.industry_label}`,
          });
        }
      }
    }
    return m;
  }, [nav.sectors]);
  const strategyThemeIdToLabel = useMemo(() => {
    const m = new Map<string, string>();
    for (const s of nav.strategies) {
      for (const th of s.industries) m.set(th.industry_id, th.industry_label);
    }
    return m;
  }, [nav.strategies]);

  // Selected slugs → industry_ids (dropping any slug that no longer maps,
  // e.g. after an exchange switch pruned the tree).
  const selectedIndustryIds = useMemo(
    () =>
      selectedIndustrySlugs
        .map((slug) => slugToIndustryId.get(slug))
        .filter((id): id is string => Boolean(id)),
    [selectedIndustrySlugs, slugToIndustryId],
  );

  // RIGHT column: strategy theme industry_ids (theme pick → that theme;
  // strategy-only pick → every theme under it). The BenchmarkPriceChart
  // shades fetch these the SAME way as industries.
  const selectedStrategyThemeIds = useMemo(() => {
    if (!nav.strategyId) return [];
    const strat = nav.strategies.find((s) => s.sector_id === nav.strategyId);
    if (!strat) return [];
    if (nav.themeSlug) {
      const th = strat.industries.find((t) => t.industry_slug === nav.themeSlug);
      return th ? [th.industry_id] : [];
    }
    return strat.industries.map((t) => t.industry_id);
  }, [nav.strategyId, nav.themeSlug, nav.strategies]);

  // Prune multi-select slugs that no longer exist in the (re)loaded tree
  // (e.g. switching exchange drops mainland-only industries), then seed the
  // multi-select ONCE with the first industry of the first sector so the
  // page shows scoped data immediately on first load.
  const seededRef = useRef(false);
  useEffect(() => {
    if (nav.sectors.length === 0) return;
    const validSlugs = new Set<string>();
    for (const s of nav.sectors) {
      for (const ind of s.industries) validSlugs.add(ind.industry_slug);
    }
    setSelectedIndustrySlugs((prev) => {
      const next = prev.filter((slug) => validSlugs.has(slug));
      return next.length === prev.length ? prev : next;
    });
    if (!seededRef.current && nav.sectorId == null && selectedIndustrySlugs.length === 0) {
      seededRef.current = true;
      nav.handleSectorChange(nav.sectors[0].sector_id);
      const firstSlug = nav.sectors[0].industries[0]?.industry_slug ?? null;
      if (firstSlug) setSelectedIndustrySlugs([firstSlug]);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nav.sectors, nav.sectorId, selectedIndustrySlugs]);

  // Sector/strategy/theme changes only update the browsing context — the
  // multi-select selection persists (same semantics as Industry Sentiments).
  const handleSectorChange = (id: string | null) => {
    nav.handleSectorChange(id);
  };

  const industryScope = useMemo(() => {
    if (selectedIndustrySlugs.length === 0 && !nav.strategyId) return {};
    const ids = new Set<string>();
    for (const id of selectedIndustryIds) ids.add(id);
    const themes = (nav.strategies.find((s) => s.sector_id === nav.strategyId)
      ?.industries ?? []).filter(
      (t) => !nav.themeSlug || t.industry_slug === nav.themeSlug,
    );
    for (const t of themes) {
      ids.add(t.industry_id);
      for (const it of t.items) {
        const hit = codeToIndustry.get(normCode(it.code));
        if (hit) ids.add(hit.industryId);
      }
    }
    return { industry_ids: [...ids] };
  }, [selectedIndustrySlugs, selectedIndustryIds, nav.strategyId, nav.themeSlug, nav.strategies, codeToIndustry]);

  const selectedIndustries = useMemo(() => {
    const result = selectedIndustryIds.map((id) => ({
      id,
      label: (() => {
        const slugEntry = Array.from(slugToIndustryId.entries()).find(
          ([, i]) => i === id,
        );
        return slugEntry
          ? (slugToIndustryLabel.get(slugEntry[0]) ?? id)
          : id;
      })(),
    }));
    for (const id of selectedStrategyThemeIds) {
      result.push({
        id,
        label: strategyThemeIdToLabel.get(id) ?? id,
      });
    }
    return result;
  }, [selectedIndustryIds, slugToIndustryId, slugToIndustryLabel, selectedStrategyThemeIds, strategyThemeIdToLabel]);

  const scopeLabel = useMemo(() => {
    const parts: string[] = [];
    for (const slug of selectedIndustrySlugs) {
      const label = slugToIndustryLabel.get(slug);
      if (label) parts.push(label.split("  ")[0] || label);
    }
    if (nav.strategyId) {
      const strat = nav.strategies.find((s) => s.sector_id === nav.strategyId);
      if (strat) {
        parts.push(
          nav.themeSlug
            ? strat.industries.find((t) => t.industry_slug === nav.themeSlug)
                ?.industry_label ?? strat.sector_label
            : strat.sector_label,
        );
      }
    }
    return parts.length > 0 ? parts.join(" + ") : "全部行业";
  }, [selectedIndustrySlugs, slugToIndustryLabel, nav.strategyId, nav.themeSlug, nav.strategies]);

  return {
    selectedIndustrySlugs,
    setSelectedIndustrySlugs,
    handleSectorChange,
    selectedIndustries,
    industryScope,
    scopeLabel,
  };
}
