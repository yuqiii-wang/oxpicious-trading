/**
 * SignalTagsInput — the Singleton Strategy signal-family TAG selector.
 *
 * Replaces the former AlgoWeightMenu (per-algo weight sliders — weighting
 * was REMOVED, user-directed 2026-11-07): the selection is now a plain
 * SET of analysis signal families, entered as tags in an autocomplete.
 * The tag options are DATA-DRIVEN — loaded from the analysis signals
 * tier (GET /api/strategy/singleton/signal-families): a family appears
 * once it has recorded standalone-tier events for the sec_type. Each
 * option shows its evidence footprint (events · codes · latest date).
 *
 * Single tag → strategy_name = the family name; multiple tags → the
 * equal-blend "portfolio:a+b" name (see selectionToStrategyName).
 *
 * DEFAULT = ALL families: until the user touches the input, every
 * (re)loaded option set is adopted wholesale (user-directed 2026-11-16);
 * options render tick-style (checkbox per row, dropdown stays open).
 */
import { useEffect, useMemo, useRef, useState } from "react";
import {
  Autocomplete,
  Box,
  Checkbox,
  Chip,
  CircularProgress,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import SellIcon from "@mui/icons-material/Sell";
import {
  fetchSignalFamilies,
  type SignalFamilyInfo,
  type StrategySelection,
} from "@/lib/api-client";
import type { MaSpreadSecType } from "@shared/types";

interface SignalTagsInputProps {
  secType: MaSpreadSecType;
  selection: StrategySelection;
  onChange: (selection: StrategySelection) => void;
  disabled?: boolean;
}

export default function SignalTagsInput({
  secType,
  selection,
  onChange,
  disabled,
}: SignalTagsInputProps) {
  const [families, setFamilies] = useState<SignalFamilyInfo[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // "Select all by default": false until the user edits the tags — every
  // option-set load before that adopts the whole family list.
  const userTouchedRef = useRef(false);

  // Tag options are data-driven from the analysis signals tier; they
  // follow the sec_type (a family appears once its events exist there).
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchSignalFamilies(secType)
      .then((rows) => {
        if (cancelled) return;
        setFamilies(rows);
        if (!userTouchedRef.current && rows.length > 0) {
          onChange([...rows.map((f) => f.name)].sort());
        }
      })
      .catch((e) => {
        if (!cancelled) setError(String(e instanceof Error ? e.message : e));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [secType, onChange]);

  const byName = useMemo(() => {
    const m = new Map<string, SignalFamilyInfo>();
    for (const f of families) m.set(f.name, f);
    return m;
  }, [families]);

  // Selected tags may not be in the current options (e.g. a family with
  // no events for THIS sec_type) — keep them; the run is honestly empty.
  const value: string[] = [...new Set(selection)].sort();
  const toggle = (next: string[]) => {
    if (next.length === 0) return; // at least one family must stay selected
    onChange([...new Set(next)].sort());
  };

  const optionLabel = (name: string): string => {
    const f = byName.get(name);
    if (!f) return name;
    return `${name} — ${f.n_events.toLocaleString()} events · ${f.n_codes} codes · to ${f.latest_date ?? "—"}`;
  };

  return (
    <Box sx={{ minWidth: 300, maxWidth: 480, flexShrink: 1 }}>
      <Autocomplete
        multiple
        size="small"
        disabled={disabled}
        disableCloseOnSelect
        options={families.map((f) => f.name)}
        value={value}
        onChange={(_, next) => {
          userTouchedRef.current = true;
          toggle(next as string[]);
        }}
        getOptionLabel={(name) => name}
        renderOption={(props, name, { selected }) => {
          const { key, ...rest } = props as React.HTMLAttributes<HTMLLIElement> & { key: string };
          return (
            <Tooltip key={key} title={optionLabel(name)} placement="top">
              <li {...rest}>
                <Checkbox
                  size="small"
                  checked={selected}
                  sx={{ mr: 0.5, p: 0.25 }}
                  inputProps={{ "aria-label": name }}
                />
                <Box sx={{ display: "flex", flexDirection: "column" }}>
                  <Typography variant="body2" sx={{ fontWeight: 600 }}>
                    {name}
                  </Typography>
                  <Typography variant="caption" color="text.secondary">
                    {optionLabel(name)}
                  </Typography>
                </Box>
              </li>
            </Tooltip>
          );
        }}
        renderTags={(tags, getTagProps) =>
          tags.map((name, i) => {
            const { key, ...chipProps } = getTagProps({ index: i }) as {
              key: React.Key;
            };
            return (
              <Chip
                key={key}
                label={name}
                size="small"
                color="primary"
                variant="filled"
                icon={<SellIcon />}
                {...chipProps}
                disabled={disabled}
              />
            );
          })
        }
        renderInput={(params) => (
          <TextField
            {...params}
            placeholder="Signal families (from analysis signals)"
            InputProps={{
              ...params.InputProps,
              endAdornment: (
                <>
                  {loading ? <CircularProgress size={16} /> : null}
                  {params.InputProps.endAdornment}
                </>
              ),
            }}
          />
        )}
        noOptionsText={
          error
            ? `Failed to load families: ${error}`
            : loading
              ? "Loading analysis signal families…"
              : "No recorded analysis signal families for this sec_type"
        }
      />
    </Box>
  );
}
