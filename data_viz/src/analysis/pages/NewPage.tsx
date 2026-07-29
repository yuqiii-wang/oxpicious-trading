import { Box, Typography } from "@mui/material";

export default function NewPage() {
  return (
    <Box>
      <Typography variant="h5" sx={{ fontWeight: 700 }}>
        New Analysis
      </Typography>
      <Typography variant="body2" color="text.secondary">
        Create a new analysis.
      </Typography>
    </Box>
  );
}
