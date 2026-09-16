/**
 * ChartSection — one chart block in the futures analysis stack: bold title
 * line (with optional trailing muted subtitle), a centered spinner while
 * loading, an error line, then the chart itself.
 */
import React from "react";
import { Box, CircularProgress, Typography } from "@mui/material";

interface ChartSectionProps {
  title: React.ReactNode;
  loading?: boolean;
  error?: string | null;
  errorLabel?: string;
  children: React.ReactNode;
}

export function ChartSection({ title, loading, error, errorLabel, children }: ChartSectionProps) {
  return (
    <Box>
      <Typography variant="body2" sx={{ fontWeight: 600, mb: 0.5 }}>
        {title}
      </Typography>
      {loading && (
        <Box sx={{ display: "flex", justifyContent: "center", py: 3 }}>
          <CircularProgress size={28} />
        </Box>
      )}
      {error && (
        <Typography variant="body2" color="error">
          {errorLabel ?? "Failed to load data"}: {error}
        </Typography>
      )}
      {!loading && !error && children}
    </Box>
  );
}
