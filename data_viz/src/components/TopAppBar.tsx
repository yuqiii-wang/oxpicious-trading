/**
 * Top app bar with app-selector dropdown, navigation tabs, quick-access
 * Monitor / Config icon buttons, and theme toggle.
 *
 * The title acts as a dropdown to switch between the browsing sub-apps
 * (Live Data / DataViz / Analysis / Strategy); Monitor and Config live as
 * icon buttons next to the theme toggle instead of the dropdown, and
 * clicking the active one toggles back to the last browsed page.
 * Each sub-app exposes its own set of tabs in the nav bar.
 */
import { useEffect, useRef, useState, type ReactNode } from "react";
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
import {
  Brightness4,
  Brightness7,
  ShowChart,
  ArrowDropDown,
  Settings,
  Monitor,
} from "@mui/icons-material";
import { useLocation, useNavigate } from "react-router-dom";
import { useStore } from "@/store/filters";

type AppKey = "live" | "dataviz" | "analysis" | "strategy" | "monitor" | "config";

interface AppConfig {
  label: string;
  defaultPath: string;
  tabs: Array<{ label: string; path: string }>;
}

const APPS: Record<AppKey, AppConfig> = {
  live: {
    label: "Live Data",
    defaultPath: "/live/market-movements",
    tabs: [
      { label: "Market Movements", path: "/live/market-movements" },
      { label: "Trading Signals", path: "/live/trading-signals" },
      { label: "Options", path: "/live/options" },
      { label: "Index", path: "/live/index" },
      { label: "ETF", path: "/live/etf" },
      { label: "Stock", path: "/live/stock" },
    ],
  },
  dataviz: {
    label: "DataViz",
    defaultPath: "/dataviz/debt",
    tabs: [
      { label: "Debt", path: "/dataviz/debt" },
      { label: "Options", path: "/dataviz/szse-options" },
      { label: "ETF", path: "/dataviz/etf-margin" },
      { label: "Index", path: "/dataviz/index" },
      { label: "Stock", path: "/dataviz/stock" },
      { label: "Futures", path: "/dataviz/futures" },
      { label: "News", path: "/dataviz/news" },
      { label: "AI", path: "/dataviz/ai" },
    ],
  },
  analysis: {
    label: "Analysis",
    defaultPath: "/analysis/commons",
    tabs: [
      { label: "Commons", path: "/analysis/commons" },
      { label: "Derivatives", path: "/analysis/derivatives" },
      { label: "Composites", path: "/analysis/composites" },
      { label: "Signals", path: "/analysis/signals" },
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
  monitor: {
    label: "Monitor",
    defaultPath: "/monitor",
    tabs: [
      { label: "Services", path: "/monitor" },
      { label: "External Resources", path: "/monitor/ext-resources" },
      { label: "Source Data", path: "/monitor/src-data" },
    ],
  },
  config: {
    label: "Config",
    defaultPath: "/config/service",
    tabs: [
      { label: "Service", path: "/config/service" },
      { label: "External Resources", path: "/config/external" },
      { label: "Security Classification", path: "/config/security" },
    ],
  },
};

function QuickAppIconButton(props: {
  label: string;
  icon: ReactNode;
  active: boolean;
  onOpen: () => void;
}) {
  const { label, icon, active, onOpen } = props;
  return (
    <Tooltip title={label}>
      <IconButton
        color="inherit"
        size="small"
        aria-label={label}
        onClick={onOpen}
        sx={{
          opacity: active ? 1 : 0.85,
          bgcolor: active ? "rgba(255,255,255,0.15)" : "transparent",
        }}
      >
        {icon}
      </IconButton>
    </Tooltip>
  );
}

export default function TopAppBar() {
  const location = useLocation();
  const navigate = useNavigate();
  const themeMode = useStore((s) => s.themeMode);
  const toggleTheme = useStore((s) => s.toggleTheme);
  const lastMainPath = useStore((s) => s.lastMainPath);
  const setLastMainPath = useStore((s) => s.setLastMainPath);
  const [menuAnchor, setMenuAnchor] = useState<HTMLElement | null>(null);
  const [menuWidth, setMenuWidth] = useState<number | undefined>(undefined);
  const buttonRef = useRef<HTMLButtonElement>(null);

  // Cache the most recent non-Monitor/Config page so un-clicking the active
  // Monitor / Config icon button has a page to return to.
  useEffect(() => {
    if (
      !location.pathname.startsWith("/config") &&
      !location.pathname.startsWith("/monitor")
    ) {
      setLastMainPath(location.pathname);
    }
  }, [location.pathname, setLastMainPath]);

  const activeApp: AppKey = location.pathname.startsWith("/strategy")
    ? "strategy"
    : location.pathname.startsWith("/analysis")
      ? "analysis"
      : location.pathname.startsWith("/live")
        ? "live"
        : location.pathname.startsWith("/monitor")
          ? "monitor"
          : location.pathname.startsWith("/config")
            ? "config"
            : "dataviz";
  const config = APPS[activeApp];
  // Longest path wins ("/monitor" is a prefix of "/monitor/ext-resources",
  // so naive first-match would light the Services tab on the ext-resources page).
  const activeIdx = config.tabs
    .map((_, i) => i)
    .sort((a, b) => config.tabs[b].path.length - config.tabs[a].path.length)
    .find((i) => location.pathname.startsWith(config.tabs[i].path));

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
          {(Object.entries(APPS) as Array<[AppKey, AppConfig]>)
            .filter(([key]) => key !== "monitor" && key !== "config")
            .map(([key, app]) => (
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
        <QuickAppIconButton
          label="Config"
          icon={<Settings fontSize="small" />}
          active={activeApp === "config"}
          onOpen={() =>
            navigate(activeApp === "config" ? lastMainPath : APPS.config.defaultPath)
          }
        />
        <QuickAppIconButton
          label="Monitor"
          icon={<Monitor fontSize="small" />}
          active={activeApp === "monitor"}
          onOpen={() =>
            navigate(activeApp === "monitor" ? lastMainPath : APPS.monitor.defaultPath)
          }
        />
        <Tooltip title={themeMode === "light" ? "Switch to dark" : "Switch to light"}>
          <IconButton color="inherit" onClick={toggleTheme} size="small">
            {themeMode === "light" ? <Brightness4 /> : <Brightness7 />}
          </IconButton>
        </Tooltip>
      </Box>
    </AppBar>
  );
}
