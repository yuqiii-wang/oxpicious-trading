/**
 * MUI v5 theme tuned for a quantitative dashboard. Light/dark variants share
 * the same chart palette tokens from _plot_commons.py.
 */
import { createTheme, type ThemeOptions } from "@mui/material/styles";
import {
  UP_COLOR,
  DOWN_COLOR,
  IV_BLUE,
  MA20_COLOR,
  MA60_COLOR,
  MA120_COLOR,
  MUTED_PALETTE,
  SUBTITLE_COLOR,
  axisColors,
} from "./chart-palette";

/**
 * Resolve a CSS color token (defined in colors.css) with a hardcoded fallback.
 * Mirrors chart-palette's cssVar() but local to the theme module so MUI's
 * createTheme stays in sync with the shared CSS source of truth.
 */
function cssVar(name: string, fallback: string): string {
  if (typeof window === "undefined") return fallback;
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}

const sharedTokens = {
  chart: {
    up: UP_COLOR,
    down: DOWN_COLOR,
    iv: IV_BLUE,
    ma20: MA20_COLOR,
    ma60: MA60_COLOR,
    ma120: MA120_COLOR,
    muted: MUTED_PALETTE,
  },
};

const lightAxis = axisColors("light");
const darkAxis = axisColors("dark");

const lightOptions: ThemeOptions = {
  palette: {
    mode: "light",
    primary: { main: cssVar("--mui-primary-light", "#1F3A5F") },
    secondary: { main: IV_BLUE },
    success: { main: UP_COLOR },
    error: { main: DOWN_COLOR },
    warning: { main: MA20_COLOR },
    info: { main: MA60_COLOR },
    background: {
      default: cssVar("--mui-bg-default-light", "#F7F8FA"),
      paper: cssVar("--mui-bg-paper-light", "#FFFFFF"),
    },
    text: {
      primary: cssVar("--mui-text-primary-light", "#1A1F2E"),
      secondary: lightAxis.textColor,
    },
    divider: lightAxis.splitLineColor,
  },
  shape: { borderRadius: 8 },
  typography: {
    fontFamily:
      'Roboto, "Helvetica Neue", "PingFang SC", "Microsoft YaHei", "Noto Sans CJK SC", system-ui, sans-serif',
    h6: { fontWeight: 600, fontSize: "1rem" },
    h5: { fontWeight: 600, fontSize: "1.15rem" },
    h4: { fontWeight: 700, fontSize: "1.35rem" },
    subtitle1: { fontSize: "0.85rem", color: lightAxis.textColor },
    subtitle2: { fontSize: "0.78rem", color: SUBTITLE_COLOR },
    body1: { fontSize: "0.875rem" },
    body2: { fontSize: "0.78rem" },
    caption: { fontSize: "0.72rem" },
    button: { textTransform: "none", fontWeight: 500 },
  },
  components: {
    MuiCard: {
      styleOverrides: {
        root: {
          boxShadow: "0 1px 2px rgba(15, 23, 42, 0.04), 0 1px 3px rgba(15, 23, 42, 0.06)",
          border: `1px solid ${lightAxis.splitLineColor}`,
          overflow: "visible",
        },
      },
    },
    MuiAppBar: {
      styleOverrides: {
        root: {
          backgroundColor: cssVar("--mui-appbar-bg-light", "#1F3A5F"),
          color: cssVar("--mui-appbar-text-light", "#FFFFFF"),
          boxShadow: "0 1px 0 rgba(15, 23, 42, 0.08)",
        },
      },
    },
    MuiTab: {
      styleOverrides: {
        root: { textTransform: "none", fontWeight: 500, minHeight: 56 },
      },
    },
    MuiChip: {
      styleOverrides: {
        root: { fontWeight: 500, borderRadius: 6 },
      },
    },
  },
};

const darkOptions: ThemeOptions = {
  palette: {
    mode: "dark",
    primary: { main: cssVar("--mui-primary-dark", "#5B8DEF") },
    secondary: { main: cssVar("--mui-secondary-dark", "#7AB8E8") },
    success: { main: UP_COLOR },
    error: { main: DOWN_COLOR },
    warning: { main: MA20_COLOR },
    info: { main: MA60_COLOR },
    background: {
      default: cssVar("--mui-bg-default-dark", "#0F1729"),
      paper: cssVar("--mui-bg-paper-dark", "#1A2238"),
    },
    text: {
      primary: cssVar("--mui-text-primary-dark", "#E6EAF2"),
      secondary: darkAxis.textColor,
    },
    divider: darkAxis.splitLineColor,
  },
  shape: { borderRadius: 8 },
  typography: lightOptions.typography,
  components: {
    MuiCard: {
      styleOverrides: {
        root: {
          boxShadow: "0 1px 2px rgba(0, 0, 0, 0.4)",
          border: `1px solid ${darkAxis.splitLineColor}`,
          overflow: "visible",
        },
      },
    },
    MuiAppBar: {
      styleOverrides: {
        root: {
          backgroundColor: cssVar("--mui-appbar-bg-dark", "#0A1124"),
          color: cssVar("--mui-appbar-text-dark", "#E6EAF2"),
          boxShadow: "0 1px 0 rgba(0, 0, 0, 0.6)",
        },
      },
    },
    MuiTab: {
      styleOverrides: {
        root: { textTransform: "none", fontWeight: 500, minHeight: 56 },
      },
    },
  },
};

export function buildTheme(mode: "light" | "dark") {
  const opts = mode === "dark" ? darkOptions : lightOptions;
  const theme = createTheme(opts);
  // Expose chart tokens via theme custom property so components can read them
  theme.palette = { ...theme.palette, ...sharedTokens } as typeof theme.palette;
  return theme;
}

export type AppTheme = ReturnType<typeof buildTheme>;
