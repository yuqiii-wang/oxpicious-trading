/** One editable limit row of the Run Limits panel — the label, the
 *  info mark toggling the field's help inline, and the range-validated
 *  numeric TextField. */
import { Box, Collapse, IconButton, TextField, Typography } from "@mui/material";
import { InfoOutlined } from "@mui/icons-material";
import type { LimitFieldDef } from "./limitFields";

export function LimitFieldRow({
  field,
  value,
  onChange,
  helpOpen,
  onToggleHelp,
}: {
  field: LimitFieldDef;
  /** The field's current draft text. */
  value: string;
  onChange: (value: string) => void;
  helpOpen: boolean;
  onToggleHelp: () => void;
}) {
  const f = field;
  return (
    <Box sx={{ py: 0.25 }}>
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: 0.5,
        }}
      >
        <Typography
          variant="body2"
          noWrap
          sx={{ flexGrow: 1, minWidth: 0 }}
        >
          {f.label}
        </Typography>
        {/* The info mark — click toggles the field's explanation inline
            (cheaper and more deliberate than a hover tooltip). */}
        <IconButton
          size="small"
          aria-label={`About ${f.label}`}
          aria-expanded={helpOpen}
          onClick={(e) => {
            e.stopPropagation();
            onToggleHelp();
          }}
          sx={{
            p: 0.25,
            flexShrink: 0,
            color: helpOpen ? "primary.main" : "action.active",
          }}
        >
          <InfoOutlined sx={{ fontSize: 15 }} />
        </IconButton>
        <TextField
          size="small"
          type="number"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          error={
            (() => {
              const text = value.trim();
              if (text === "") return !f.nullable;
              const n = Number(text);
              return !Number.isFinite(n) || n < f.min ||
                (f.max !== null && n > f.max);
            })()
          }
          inputProps={{
            min: f.min,
            max: f.max ?? undefined,
            step: "any",
            "aria-label": f.label,
          }}
          sx={{ width: 92, flexShrink: 0 }}
        />
      </Box>
      <Collapse in={helpOpen}>
        <Typography
          variant="caption"
          color="text.secondary"
          sx={{
            display: "block",
            px: 0.5,
            pt: 0.25,
            whiteSpace: "normal",
          }}
        >
          {f.help}
        </Typography>
      </Collapse>
    </Box>
  );
}
