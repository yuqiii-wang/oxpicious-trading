/**
 * Reusable exact-code search bar for the ETF and Index pages.
 *
 *   • Enter a code (with or without exchange suffix) and press Enter / click
 *     the search icon to look it up.
 *   • When a search is active (`activeCode` non-null) a "clear" chip is shown
 *     so the user can return to the normal sector/industry browsing view.
 *   • The parent page owns the lookup logic — `onSearch` is called with the
 *     raw input; the parent resolves it against the themes tree and decides
 *     whether to load a single-result plot or show a "not found" error.
 */
import { useState, type KeyboardEvent } from "react";
import { Box, Chip, InputAdornment, TextField } from "@mui/material";
import { Search, Clear } from "@mui/icons-material";
import type { SectorNode, StrategyNode } from "@shared/types";

interface Props {
  /** Currently active search code (null = browsing mode). */
  activeCode: string | null;
  /** Called with the trimmed input when the user submits a search. */
  onSearch: (code: string) => void;
  /** Called when the user clears the active search. */
  onClear: () => void;
  /** Placeholder text (e.g. "ETF code" / "Index code"). */
  placeholder?: string;
}

export default function CodeSearchBar({
  activeCode,
  onSearch,
  onClear,
  placeholder = "Code",
}: Props) {
  const [value, setValue] = useState("");

  const submit = () => {
    const trimmed = value.trim();
    if (trimmed) onSearch(trimmed);
  };

  const handleKey = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key === "Enter") {
      e.preventDefault();
      submit();
    }
  };

  return (
    <Box sx={{ display: "flex", alignItems: "center", gap: 1, flexWrap: "wrap" }}>
      <TextField
        size="small"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={handleKey}
        placeholder={placeholder}
        sx={{
          width: 180,
          "& .MuiInputBase-input": { fontSize: "0.8rem" },
        }}
        InputProps={{
          startAdornment: (
            <InputAdornment position="start">
              <Search fontSize="small" sx={{ color: "text.secondary" }} />
            </InputAdornment>
          ),
        }}
      />
      {activeCode && (
        <Chip
          label={`Code: ${activeCode}`}
          size="small"
          color="primary"
          variant="filled"
          onDelete={onClear}
          deleteIcon={<Clear fontSize="small" />}
          sx={{ fontSize: "0.7rem" }}
        />
      )}
    </Box>
  );
}

/**
 * Look up a code in the two-level themes tree and return the sector id +
 * industry slug that contain it.  The comparison is case-insensitive and
 * ignores exchange suffixes (.SS/.SZ/.SH/.BJ/.HK).
 *
 * Handles both suffixed ("000001.SZ") and bare ("000001") item codes — the
 * suffix is stripped from both sides before comparing so a bare input still
 * matches a suffixed stored code (and vice versa).
 *
 * Returns null when the code is not found in any sector/industry.
 */
export function findCodeInThemes(
  sectors: SectorNode[],
  rawCode: string,
): { sectorId: string; industrySlug: string; name: string } | null {
  const normalized = rawCode.trim().toUpperCase().replace(/\.(SS|SZ|SH|BJ|HK)$/i, "");
  if (!normalized) return null;
  const stripSuffix = (c: string) => c.toUpperCase().replace(/\.(SS|SZ|SH|BJ|HK)$/i, "");
  for (const s of sectors) {
    for (const ind of s.industries) {
      // Match either by suffixed code or by stripped 6-digit form.
      const hit = ind.items.find(
        (it) => it.code.toUpperCase() === normalized
          || stripSuffix(it.code) === normalized,
      );
      if (hit) {
        return { sectorId: s.sector_id, industrySlug: ind.industry_slug, name: hit.name };
      }
    }
  }
  return null;
}

/**
 * Look up a code in the strategy tree (RIGHT column) and return the strategy
 * id + theme slug that contain it.  Parallel to findCodeInThemes but for the
 * RIGHT column of the two-column selector.
 *
 * Returns null when the code is not found in any strategy/industry.
 */
export function findCodeInStrategyThemes(
  strategies: StrategyNode[],
  rawCode: string,
): { strategyId: string; themeSlug: string; name: string } | null {
  const normalized = rawCode.trim().toUpperCase().replace(/\.(SS|SZ|SH|BJ|HK)$/i, "");
  if (!normalized) return null;
  const stripSuffix = (c: string) => c.toUpperCase().replace(/\.(SS|SZ|SH|BJ|HK)$/i, "");
  for (const s of strategies) {
    for (const th of s.industries) {
      const hit = th.items.find(
        (it) => it.code.toUpperCase() === normalized
          || stripSuffix(it.code) === normalized,
      );
      if (hit) {
        return { strategyId: s.sector_id, themeSlug: th.industry_slug, name: hit.name };
      }
    }
  }
  return null;
}
