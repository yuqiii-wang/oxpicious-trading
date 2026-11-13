/** The dashed empty-slot placeholder of the Prompts panel. */
import { Box, Typography } from "@mui/material";

export function EmptySlot({ icon, title, note }: { icon: React.ReactNode; title: string; note: string }) {
  return (
    <Box
      sx={{
        p: 2,
        border: "1px dashed",
        borderColor: "divider",
        borderRadius: 1,
        color: "text.secondary",
        display: "flex",
        alignItems: "center",
        gap: 1,
      }}
    >
      {icon}
      <Box sx={{ minWidth: 0 }}>
        <Typography variant="body2" sx={{ fontWeight: 600 }}>
          {title}
        </Typography>
        <Typography variant="caption">{note}</Typography>
      </Box>
    </Box>
  );
}
