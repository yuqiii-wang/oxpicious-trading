import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { ThemeProvider, CssBaseline } from "@mui/material";
import { LocalizationProvider } from "@mui/x-date-pickers/LocalizationProvider";
import { AdapterDayjs } from "@mui/x-date-pickers/AdapterDayjs";
import "dayjs/locale/en";
import App from "./App";
import { useStore } from "@/store/filters";
import { buildTheme } from "@/theme/mui-theme";
import { connectChartsByGroup } from "@/components/EChart";
import "./theme/colors.css";
import "./index.css";

function ThemedApp() {
  const themeMode = useStore((s) => s.themeMode);
  const theme = buildTheme(themeMode);
  return (
    <ThemeProvider theme={theme}>
      <CssBaseline />
      <LocalizationProvider dateAdapter={AdapterDayjs} adapterLocale="en">
        <App />
      </LocalizationProvider>
    </ThemeProvider>
  );
}

// Wire up ECharts cross-chart tooltip sync once.
connectChartsByGroup("debt-baseline");
connectChartsByGroup("annual-sentiment");
connectChartsByGroup("industry-sentiments");

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ThemedApp />
  </StrictMode>,
);
