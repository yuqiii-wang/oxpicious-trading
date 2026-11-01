/**
 * Keywords tab — the seeded keyword → category map (static.keyword_maps)
 * as one filterable table: every keyword with its category type, category
 * (industry_id or the decoded market/theme label), lexical class,
 * negation flag and language.
 *
 * READ-ONLY by design: the map is a static seed table whose rows land
 * only via the SQL seed files under database/sql/static/keyword_maps/
 * (wave-1 bootstrap + statics/ expansion waves) — like every other
 * static config, editing means editing the owning seed file and
 * re-applying, so this tab displays and filters only. The filters are
 * client-side over the one GET /api/config/keyword-maps payload (825+
 * rows — small); the category dropdown's options follow the picked
 * category type (market → kw_market labels, broadmarket → kw_theme
 * labels, industry → industry_ids; skip rows and the grammatical
 * component rows (adj / adv / adv_noun / adv_verb — 大幅/高位/持续) carry
 * no category: skip words are dropped by the search-index tokenization,
 * the component layer is what the keyword n-gram matcher composes with
 * the market verbs).
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  FormControl,
  InputLabel,
  MenuItem,
  Select,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import Refresh from "@mui/icons-material/Refresh";
import {
  fetchKeywordMaps,
  type KeywordMapEntry,
} from "@/lib/api-client/config";
import {
  expandedTableBodyCellSx,
  expandedTableHeadCellSx,
} from "@/shared/styles/expanded-table-styles";

const CATEGORY_TYPES = [
  "industry",
  "market",
  "broadmarket",
  "skip",
  "adj",
  "adv",
  "adv_noun",
  "adv_verb",
] as const;
const POS_VALUES = ["noun", "verb", "noun_verb", "function"] as const;
const LANGUAGES = ["zh", "en"] as const;

function FilterSelect<T extends string>({
  label,
  value,
  options,
  onChange,
  width,
}: {
  label: string;
  value: T | "all";
  options: readonly T[];
  onChange: (v: T | "all") => void;
  width: number;
}) {
  return (
    <FormControl size="small" sx={{ minWidth: width }}>
      <InputLabel sx={{ fontSize: "0.78rem" }}>{label}</InputLabel>
      <Select
        value={value}
        label={label}
        onChange={(e) => onChange(e.target.value as T | "all")}
        sx={{ fontSize: "0.78rem" }}
      >
        <MenuItem value="all">all</MenuItem>
        {options.map((o) => (
          <MenuItem key={o} value={o} sx={{ fontSize: "0.78rem" }}>
            {o}
          </MenuItem>
        ))}
      </Select>
    </FormControl>
  );
}

export default function KeywordMapsPage() {
  const [entries, setEntries] = useState<KeywordMapEntry[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    setEntries(null);
    setError(null);
    fetchKeywordMaps()
      .then((resp) => setEntries(resp.entries))
      .catch((e) => setError(String(e)));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  // ---- Filters -----------------------------------------------------------
  const [search, setSearch] = useState("");
  const [typeFilter, setTypeFilter] = useState<(typeof CATEGORY_TYPES)[number] | "all">("all");
  const [categoryFilter, setCategoryFilter] = useState<string>("all");
  const [posFilter, setPosFilter] = useState<(typeof POS_VALUES)[number] | "all">("all");
  const [languageFilter, setLanguageFilter] = useState<(typeof LANGUAGES)[number] | "all">("all");
  const [negationFilter, setNegationFilter] = useState<"all" | "flagged" | "plain">("all");

  // Category options follow the picked category type (null when type is
  // "all" — the dropdown then lists every category of every type).
  const categoryOptions = useMemo(() => {
    if (!entries) return [];
    const inScope = entries.filter(
      (e) => typeFilter === "all" || e.category_type === typeFilter,
    );
    return Array.from(
      new Set(inScope.map((e) => e.category).filter((c): c is string => !!c)),
    ).sort();
  }, [entries, typeFilter]);

  const filtered = useMemo(() => {
    if (!entries) return [];
    const q = search.trim().toLowerCase();
    return entries.filter((e) => {
      if (typeFilter !== "all" && e.category_type !== typeFilter) return false;
      if (categoryFilter !== "all" && e.category !== categoryFilter) return false;
      if (posFilter !== "all" && e.parts_of_speech !== posFilter) return false;
      if (languageFilter !== "all" && e.language !== languageFilter) return false;
      if (negationFilter === "flagged" && !e.is_negation) return false;
      if (negationFilter === "plain" && e.is_negation) return false;
      if (q && !e.keyword.toLowerCase().includes(q)
            && !(e.category ?? "").toLowerCase().includes(q)) return false;
      return true;
    });
  }, [entries, search, typeFilter, categoryFilter, posFilter, languageFilter, negationFilter]);

  const resetFilters = useCallback(() => {
    setSearch("");
    setTypeFilter("all");
    setCategoryFilter("all");
    setPosFilter("all");
    setLanguageFilter("all");
    setNegationFilter("all");
  }, []);

  const hasFilter =
    search !== "" || typeFilter !== "all" || categoryFilter !== "all" ||
    posFilter !== "all" || languageFilter !== "all" || negationFilter !== "all";

  return (
    <Box sx={{ p: 2, display: "flex", flexDirection: "column", gap: 1.5 }}>
      <Box sx={{ display: "flex", alignItems: "center", gap: 1.5, flexWrap: "wrap" }}>
        <Typography variant="h6" sx={{ fontSize: "1rem" }}>
          Keyword Maps
        </Typography>
        <Typography variant="caption" sx={{ color: "text.secondary" }}>
          static.keyword_maps — the seeded keyword → category map (edit via
          the database/sql/static/keyword_maps/ seed files, not here)
        </Typography>
        <Box sx={{ flex: 1 }} />
        <TextField
          size="small"
          label="Search keyword / category"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          sx={{ minWidth: 220 }}
        />
        <FilterSelect
          label="Type"
          value={typeFilter}
          options={CATEGORY_TYPES}
          onChange={(v) => {
            setTypeFilter(v);
            setCategoryFilter("all");
          }}
          width={130}
        />
        <FilterSelect
          label="Category"
          value={categoryFilter}
          options={categoryOptions}
          onChange={setCategoryFilter}
          width={170}
        />
        <FilterSelect
          label="Pos"
          value={posFilter}
          options={POS_VALUES}
          onChange={(v) => setPosFilter(v as (typeof POS_VALUES)[number] | "all")}
          width={110}
        />
        <FilterSelect
          label="Language"
          value={languageFilter}
          options={LANGUAGES}
          onChange={(v) => setLanguageFilter(v as (typeof LANGUAGES)[number] | "all")}
          width={110}
        />
        <FormControl size="small" sx={{ minWidth: 110 }}>
          <InputLabel sx={{ fontSize: "0.78rem" }}>Negation</InputLabel>
          <Select
            value={negationFilter}
            label="Negation"
            onChange={(e) => setNegationFilter(e.target.value as "all" | "flagged" | "plain")}
            sx={{ fontSize: "0.78rem" }}
          >
            <MenuItem value="all">all</MenuItem>
            <MenuItem value="flagged">flagged</MenuItem>
            <MenuItem value="plain">plain</MenuItem>
          </Select>
        </FormControl>
        {hasFilter && (
          <Button size="small" onClick={resetFilters}>
            Reset filters
          </Button>
        )}
      </Box>

      {error && (
        <Alert severity="error" sx={{ py: 0.5 }}>
          Failed to load the keyword map: {error}
        </Alert>
      )}

      {!entries && !error ? (
        <Box sx={{ display: "flex", justifyContent: "center", py: 4 }}>
          <CircularProgress size={28} />
        </Box>
      ) : (
        <TableContainer sx={{ maxHeight: "calc(100vh - 220px)" }}>
          <Table size="small" stickyHeader>
            <TableHead>
              <TableRow>
                <TableCell sx={expandedTableHeadCellSx}>Keyword</TableCell>
                <TableCell sx={expandedTableHeadCellSx}>Type</TableCell>
                <TableCell sx={expandedTableHeadCellSx}>Category</TableCell>
                <TableCell sx={expandedTableHeadCellSx}>Pos</TableCell>
                <TableCell sx={expandedTableHeadCellSx}>Negation</TableCell>
                <TableCell sx={expandedTableHeadCellSx}>Language</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {filtered.map((e) => (
                <TableRow key={`${e.keyword}|${e.category_type}|${e.category ?? e.category_code}`}>
                  <TableCell sx={{ ...expandedTableBodyCellSx, fontWeight: 600 }}>
                    {e.keyword}
                  </TableCell>
                  <TableCell sx={expandedTableBodyCellSx}>{e.category_type}</TableCell>
                  <TableCell sx={expandedTableBodyCellSx}>{e.category ?? "—"}</TableCell>
                  <TableCell sx={expandedTableBodyCellSx}>{e.parts_of_speech}</TableCell>
                  <TableCell sx={expandedTableBodyCellSx}>
                    {e.is_negation ? (
                      <Tooltip title="A preceding negator (没有/并未/no/not/…) flips the keyword — negated occurrences don't count">
                        <Chip
                          size="small"
                          variant="outlined"
                          color="warning"
                          label="negation"
                          sx={{ height: 18, fontSize: "0.65rem" }}
                        />
                      </Tooltip>
                    ) : (
                      "—"
                    )}
                  </TableCell>
                  <TableCell sx={expandedTableBodyCellSx}>{e.language}</TableCell>
                </TableRow>
              ))}
              {filtered.length === 0 && (
                <TableRow>
                  <TableCell colSpan={6} sx={expandedTableBodyCellSx}>
                    {entries ? "no keywords match the filters" : " "}
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </TableContainer>
      )}

      <Box sx={{ display: "flex", alignItems: "center", gap: 1.5 }}>
        <Typography variant="caption" sx={{ color: "text.secondary" }}>
          {entries
            ? `${filtered.length} of ${entries.length} keywords${hasFilter ? " (filtered)" : ""}`
            : ""}
        </Typography>
        <Box sx={{ flex: 1 }} />
        <Refresh onClick={load} fontSize="small" sx={{ cursor: "pointer", color: "action.active" }} />
      </Box>
    </Box>
  );
}
