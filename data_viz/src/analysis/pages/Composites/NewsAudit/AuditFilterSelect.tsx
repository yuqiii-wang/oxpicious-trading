/**
 * AuditFilterSelect — the News Audit page's toolbar dropdown filters
 * (Provider / Author): a single-select Autocomplete over the loaded
 * windows' distinct values. Both vocabularies run long (the corpus's
 * gov 来源 sources alone number ~150), so type-to-search beats a plain
 * Select menu (the SentimentsPage single-select Autocomplete pattern).
 * null = "(all)"; each option carries its row count in the current scope,
 * mirroring ValueFilterSelect's closed-vocabulary dropdown. "" is a
 * LEGITIMATE option value here (the author provider-level sentinel), so
 * "all" must be null — never "".
 */
import { Autocomplete, Box, TextField, Typography } from "@mui/material";

export interface AuditFilterOption {
  /** The raw filter value ("" = a sentinel bucket, e.g. the author
   *  provider-level rows). */
  value: string;
  /** Display label — e.g. authorFilterLabel's "(provider)" for "". */
  label: string;
  count: number;
}

interface AuditFilterSelectProps {
  /** Field label shown on the closed control. */
  label: string;
  options: AuditFilterOption[];
  /** Selected value — null = all ("" selects the sentinel bucket). */
  value: string | null;
  onChange: (next: string | null) => void;
  /** Fixed pixel width of the control (default 220 — names run long). */
  width?: number;
}

export default function AuditFilterSelect({
  label,
  options,
  value,
  onChange,
  width = 220,
}: AuditFilterSelectProps) {
  const selected = options.find((o) => o.value === value) ?? null;
  return (
    <Autocomplete
      size="small"
      sx={{ width, minWidth: 160 }}
      options={options}
      value={selected}
      onChange={(_, next) => onChange(next?.value ?? null)}
      isOptionEqualToValue={(o, v) => o.value === v.value}
      getOptionLabel={(o) => o.label}
      renderOption={({ key, ...optionProps }, o) => (
        <Box key={key} component="li" {...optionProps} sx={{ display: "flex", alignItems: "center", gap: 1, width: "100%" }}>
          <Typography variant="caption" noWrap sx={{ maxWidth: 280 }}>
            {o.label}
          </Typography>
          <Box sx={{ flexGrow: 1 }} />
          <Typography variant="caption" color="text.secondary">
            {o.count}
          </Typography>
        </Box>
      )}
      renderInput={(params) => <TextField {...params} size="small" label={label} />}
    />
  );
}
