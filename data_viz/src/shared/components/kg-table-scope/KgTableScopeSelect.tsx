/**
 * KgTableScopeSelect — the SHARED nested tick-select dropdown over the
 * KG's tables (2026-11-11), the one face both consumers mount:
 *
 *   - the Knowledge Graph config page's FORCED-TABLE-SCOPE preview
 *     (what a deep-agent run forced to the ticked tables may search);
 *   - the AI summary Generate dialog's "Forced table scope" knob
 *     (the run's request rides the ticked names as forced_tables).
 *
 * The dropdown renders the SAME nested tick hierarchy everywhere: one
 * sticky group header per SCHEMA (the Scopes panel's two-layer shape)
 * with the schema's tables under it, one checkbox tick per option,
 * selected names as removable chips. The underlying data is the
 * SHARED :hook:`useKgScopeTables` list (one ns0 fetch per session) —
 * a consumer may also hand its own option list via ``groups`` (the
 * config page previews whatever namespace it loaded).
 */
import { useMemo } from "react";
import {
  Autocomplete,
  Checkbox,
  Chip,
  createFilterOptions,
  TextField,
  Typography,
} from "@mui/material";
import { kgNameSchema, useKgScopeTables, type KgSchemaGroup } from "./useKgScopeTables";

/** The popper cap (the ns0 corpus carries hundreds of tables) — the
 *  same bound the analysis page's pickers run. */
const OPTION_CAP = 60;
const optionFilter = createFilterOptions<string>();

interface KgTableScopeSelectProps {
  value: string[];
  onChange: (next: string[]) => void;
  /** Disable the picker (e.g. ns1 on the config page — the prompt
   *  catalog names no tables, the serving layer exempts it). */
  disabled?: boolean;
  /** The input's placeholder (a schema-exempt note rides here). */
  placeholder?: string;
  /** The TextField helper line under the input. */
  helperText?: string;
  /** Override the option list — defaults to the shared ns0 table
   *  groups (one session fetch). The config page passes its own
   *  loaded namespace's tables so the dropdown always mirrors the
   *  canvas data. */
  groups?: KgSchemaGroup[];
}

export default function KgTableScopeSelect({
  value,
  onChange,
  disabled = false,
  placeholder = "Table scope: tick tables…",
  helperText,
  groups: groupsOverride,
}: KgTableScopeSelectProps) {
  const { groups: sharedGroups } = useKgScopeTables();
  const groups = groupsOverride ?? sharedGroups;

  // The flat option list, sorted schema-then-name so the groupBy
  // rendering nests cleanly.
  const options = useMemo(
    () => groups.flatMap((g) => g.tables),
    [groups],
  );
  const schemaOf = useMemo(() => {
    const m = new Map<string, string>();
    for (const g of groups) {
      for (const name of g.tables) m.set(name, g.schema);
    }
    return (name: string) => m.get(name) ?? kgNameSchema(name)[0];
  }, [groups]);

  return (
    <Autocomplete
      multiple
      disableCloseOnSelect
      fullWidth
      size="small"
      options={options}
      value={value}
      disabled={disabled}
      onChange={(_, next) => onChange([...next])}
      groupBy={(name) => schemaOf(name)}
      renderGroup={(params) => (
        <li key={params.key}>
          <Typography
            variant="caption"
            sx={{
              display: "block",
              px: 2,
              pt: 1,
              pb: 0.5,
              fontWeight: 700,
              color: "text.secondary",
              fontSize: "0.72rem",
              textTransform: "uppercase",
              letterSpacing: "0.04em",
              position: "sticky",
              top: 0,
              bgcolor: "background.paper",
              zIndex: 1,
            }}
          >
            {params.group}
          </Typography>
          <ul style={{ margin: 0, padding: 0, listStyle: "none" }}>
            {params.children}
          </ul>
        </li>
      )}
      filterOptions={(opts, state) =>
        optionFilter(opts, state).slice(0, OPTION_CAP)
      }
      renderOption={(props, option, { selected }) => {
        const { key, ...rest } = props;
        return (
          <li key={key} {...rest}>
            <Checkbox
              size="small"
              tabIndex={-1}
              sx={{ mr: 1, p: 0.5 }}
              checked={selected}
            />
            <Typography variant="caption" sx={{ fontSize: "0.78rem" }}>
              {option}
            </Typography>
          </li>
        );
      }}
      renderTags={(tags, getTagProps) =>
        tags.map((name, index) => (
          <Chip
            key={name}
            size="small"
            color="secondary"
            variant="outlined"
            label={name}
            sx={{ fontSize: "0.78rem" }}
            {...getTagProps({ index })}
          />
        ))
      }
      renderInput={(params) => (
        <TextField
          {...params}
          placeholder={placeholder}
          helperText={helperText}
          InputProps={{
            sx: { fontSize: "0.78rem" },
            ...params.InputProps,
          }}
        />
      )}
    />
  );
}
