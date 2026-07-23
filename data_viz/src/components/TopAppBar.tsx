/**
 * Top app bar with navigation tabs and theme toggle.
 */
import { AppBar, Tabs, Tab, IconButton, Box, Typography, Tooltip } from "@mui/material";
import { Brightness4, Brightness7, ShowChart } from "@mui/icons-material";
import { useLocation, useNavigate } from "react-router-dom";
import { useStore } from "@/store/filters";

const NAV_ITEMS = [
  { label: "Debt Baseline", path: "/debt-baseline" },
  { label: "SZSE Options", path: "/szse-options" },
  { label: "ETF + Margin", path: "/etf-margin" },
  { label: "Index", path: "/index-baseline" },
];

export default function TopAppBar() {
  const location = useLocation();
  const navigate = useNavigate();
  const themeMode = useStore((s) => s.themeMode);
  const toggleTheme = useStore((s) => s.toggleTheme);

  const activeIdx = NAV_ITEMS.findIndex((n) => location.pathname.startsWith(n.path));

  return (
    <AppBar position="sticky" elevation={0}>
      <Box sx={{ display: "flex", alignItems: "center", px: 2, py: 0.5 }}>
        <ShowChart fontSize="small" sx={{ mr: 1 }} />
        <Typography variant="h6" sx={{ fontWeight: 600, mr: 4 }}>
          Oxpicious DataViz
        </Typography>
        <Tabs
          value={activeIdx >= 0 ? activeIdx : 0}
          onChange={(_e, v) => navigate(NAV_ITEMS[v].path)}
          textColor="inherit"
          indicatorColor="secondary"
          sx={{ flexGrow: 1 }}
        >
          {NAV_ITEMS.map((n) => (
            <Tab key={n.path} label={n.label} sx={{ color: "rgba(255,255,255,0.85)" }} />
          ))}
        </Tabs>
        <Tooltip title={themeMode === "light" ? "Switch to dark" : "Switch to light"}>
          <IconButton color="inherit" onClick={toggleTheme} size="small">
            {themeMode === "light" ? <Brightness4 /> : <Brightness7 />}
          </IconButton>
        </Tooltip>
      </Box>
    </AppBar>
  );
}
