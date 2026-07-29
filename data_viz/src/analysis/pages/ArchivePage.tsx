import { Box, Typography } from "@mui/material";

export default function ArchivePage() {
  return (
    <Box>
      <Typography variant="h5" sx={{ fontWeight: 700 }}>
        Archive
      </Typography>
      <Typography variant="body2" color="text.secondary">
        Archived analyses.
      </Typography>
    </Box>
  );
}
