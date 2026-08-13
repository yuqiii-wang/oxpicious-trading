/**
 * Top app bar with app-selector dropdown, navigation tabs, and theme toggle.
 *
 * The title acts as a dropdown to switch between sub-apps (DataViz / Analysis).
 * Each sub-app exposes its own set of tabs in the nav bar.
 */
import { useRef, useState } from "react";
import {
  AppBar,
  Tabs,
  Tab,
  IconButton,
  Box,
  Button,
  Menu,
  MenuItem,
  Tooltip,
} from "@mui/material";
import { Brightness4, Brightness7, ShowChart, ArrowDropDown } from "@mui/icons-material";
import { useLocation, useNavigate } from "react-router-dom";
import { useStore } from "@/store/filters";

type AppKey = "live" | "dataviz" | "analysis" | "strategy";

interface AppConfig {
  label: string;
  defaultPath: string;
  tabs: Array<{ label: string; path: string }>;
}

const APPS: Record<AppKey, AppConfig> = {
  live: {
    label: "Live Data",
    defaultPath: "/live/index",
    tabs: [
      { label: "Index", path: "/live/index" },
      { label: "ETF", path: "/live/etf" },
      { label: "Stock", path: "/live/stock" },
    ],
  },
  dataviz: {
    label: "DataViz",
    defaultPath: "/dataviz/debt-baseline",
    tabs: [
      { label: "Debt Baseline", path: "/dataviz/debt-baseline" },
      { label: "SZSE Options", path: "/dataviz/szse-options" },
      { label: "ETF + Margin", path: "/dataviz/etf-margin" },
      { label: "Index", path: "/dataviz/index-baseline" },
      { label: "Stock", path: "/dataviz/stock-baseline" },
    ],
  },
  analysis: {
    label: "Analysis",
    defaultPath: "/analysis/commons",
    tabs: [
      { label: "Commons", path: "/analysis/commons" },
      { label: "Derivatives", path: "/analysis/derivatives" },
      { label: "Composites", path: "/analysis/composites" },
    ],
  },
  strategy: {
    label: "Strategy",
    defaultPath: "/strategy/singleton",
    tabs: [
      { label: "Singleton", path: "/strategy/singleton" },
      { label: "Commons", path: "/strategy/commons" },
    ],
  },
};

export default function TopAppBar() {
  const location = useLocation();
  const navigate = useNavigate();
  const themeMode = useStore((s) => s.themeMode);
  const toggleTheme = useStore((s) => s.toggleTheme);
  const [menuAnchor, setMenuAnchor] = useState<HTMLElement | null>(null);
  const [menuWidth, setMenuWidth] = useState<number | undefined>(undefined);
  const buttonRef = useRef<HTMLButtonElement>(null);

  const activeApp: AppKey = location.pathname.startsWith("/strategy")
    ? "strategy"
    : location.pathname.startsWith("/analysis")
      ? "analysis"
      : location.pathname.startsWith("/live")
        ? "live"
        : "dataviz";
  const config = APPS[activeApp];
  const activeIdx = config.tabs.findIndex((t) => location.pathname.startsWith(t.path));

  return (
    <AppBar position="sticky" elevation={0}>
      <Box sx={{ display: "flex", alignItems: "center", px: 2, py: 0.5 }}>
        <ShowChart fontSize="small" sx={{ mr: 1 }} />
        <Button
          ref={buttonRef}
          color="inherit"
          endIcon={<ArrowDropDown />}
          onClick={(e) => {
            setMenuWidth(buttonRef.current?.clientWidth);
            setMenuAnchor(e.currentTarget);
          }}
          sx={{
            fontWeight: 600,
            fontSize: "1.1rem",
            textTransform: "none",
            mr: 4,
            py: 0.5,
          }}
        >
          Oxpicious {config.label}
        </Button>
        <Menu
          anchorEl={menuAnchor}
          open={Boolean(menuAnchor)}
          onClose={() => setMenuAnchor(null)}
          slotProps={{
            paper: {
              style: { minWidth: menuWidth, width: menuWidth },
            },
          }}
        >
          {(Object.entries(APPS) as Array<[AppKey, AppConfig]>).map(([key, app]) => (
            <MenuItem
              key={key}
              selected={key === activeApp}
              onClick={() => {
                setMenuAnchor(null);
                if (key !== activeApp) {
                  navigate(APPS[key].defaultPath);
                }
              }}
            >
              {app.label}
            </MenuItem>
          ))}
        </Menu>
        <Tabs
          value={activeIdx >= 0 ? activeIdx : 0}
          onChange={(_e, v) => navigate(config.tabs[v].path)}
          textColor="inherit"
          indicatorColor="secondary"
          sx={{ flexGrow: 1 }}
        >
          {config.tabs.map((t) => (
            <Tab key={t.path} label={t.label} sx={{ color: "rgba(255,255,255,0.85)" }} />
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
