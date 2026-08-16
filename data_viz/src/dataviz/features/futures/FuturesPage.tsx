/**
 * Futures page — CFFEX futures curve visualization.
 *
 *  • Product selector (IC/IF/IH/IM index or T/TF/TL/TS bond)
 *  • Top plot: multi-line ECharts (one line per contract)
 *      - Blue gradient for active qualifying contracts (nearer = darker)
 *      - Ghost very-light-grey for matured contracts
 *  • Future/History toggle (additive — history shows matured in clearer grey)
 */
import { useEffect, useMemo, useState } from "react";
import {
  Alert,
  Box,
  CircularProgress,
  FormControl,
  InputLabel,
  MenuItem,
  Select,
  Stack,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from "@mui/material";
import type { SelectChangeEvent } from "@mui/material";
import RefreshButton from "@/components/RefreshButton";
import EChart from "@/components/EChart";
import {
  fetchFuturesProducts,
  fetchFuturesCombined,
  invalidateCacheForPrefix,
} from "@/lib/api-client";
import type {
  FuturesProduct,
  FuturesCombinedResponse,
  FuturesRow,
} from "../../../../shared/types";
import { buildFuturesChartOption, type ZoomRange } from "./chartOption";

type ViewMode = "future" | "history";

// Default product — the most liquid index future
const DEFAULT_PRODUCT = "IF";

export default function FuturesPage() {
  const [products, setProducts] = useState<FuturesProduct[]>([]);
  const [product, setProduct] = useState<string>(DEFAULT_PRODUCT);
  const [data, setData] = useState<FuturesCombinedResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [viewMode, setViewMode] = useState<ViewMode>("future");
  const [refreshKey, setRefreshKey] = useState(0);
  const [zoomRange, setZoomRange] = useState<ZoomRange | undefined>(undefined);

  // Load products list
  useEffect(() => {
    fetchFuturesProducts()
      .then((r) => setProducts(r.products))
      .catch((e: Error) => setError(e.message));
  }, [refreshKey]);

  // Load combined data for the selected product
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchFuturesCombined(product)
      .then((d) => {
        if (cancelled) return;
        setData(d);
        setLoading(false);
      })
      .catch((e: Error) => {
        if (cancelled) return;
        setError(e.message);
        setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [product, refreshKey]);

  const handleProductChange = (e: SelectChangeEvent<string>) => {
    setProduct(e.target.value);
  };

  const handleViewModeChange = (
    _e: React.MouseEvent<HTMLElement>,
    value: ViewMode | null,
  ) => {
    if (value !== null) setViewMode(value);
  };

  const handleRefresh = () => {
    invalidateCacheForPrefix("/api/futures/");
    setRefreshKey((k) => k + 1);
  };

  const chartOption = useMemo(() => {
    if (!data) return null;
    return buildFuturesChartOption(data, viewMode, zoomRange);
  }, [data, viewMode, zoomRange]);

  const handleDataZoom = (params: unknown) => {
    const p = params as { batch?: Array<{ start?: number; end?: number }> };
    if (p?.batch && p.batch.length > 0) {
      const batch = p.batch[0];
      if (batch.start != null && batch.end != null) {
        setZoomRange((prev) => {
          if (prev && prev.start === batch.start && prev.end === batch.end) {
            return prev;
          }
          return { start: batch.start!, end: batch.end! };
        });
      }
    }
  };

  const onEvents = useMemo(() => ({
    dataZoom: handleDataZoom,
  }), []);

  const summary = useMemo(() => {
    if (!data) return null;
    const active = data.contracts.filter(
      (c) => c.is_alive && c.is_continuous,
    ).length;
    const matured = data.contracts.filter((c) => !c.is_alive).length;
    return {
      active,
      matured,
      nDays: data.dates.length,
      d0: data.dates[0],
      d1: data.dates[data.dates.length - 1],
      contractType: data.contract_type,
    };
  }, [data]);

  const productLabel =
    products.find((p) => p.product_code === product)?.name ?? product;
  const underlyingLabel =
    data?.underlying_name && data?.underlying_code
      ? `underlying ${data.underlying_code} (${data.underlying_name})`
      : "";

  return (
    <Stack spacing={2}>
      <Box
        sx={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "flex-start",
          gap: 2,
          flexWrap: "wrap",
        }}
      >
        <Box>
          <Typography variant="h5" sx={{ fontWeight: 700 }}>
            CFFEX Futures
          </Typography>
          <Typography variant="body2" color="text.secondary">
            {productLabel} · {data?.contract_type === "bond" ? "bond" : "index"} future{" "}
            {underlyingLabel}
          </Typography>
        </Box>
        <RefreshButton
          onClick={handleRefresh}
          loading={loading}
          label="Refresh"
          tooltip="Refresh futures data (bypass cache)"
        />
      </Box>

      <Box
        sx={{
          display: "flex",
          gap: 2,
          flexWrap: "wrap",
          alignItems: "center",
        }}
      >
        <FormControl size="small" sx={{ minWidth: 200 }}>
          <InputLabel id="futures-product-label">Product</InputLabel>
          <Select
            labelId="futures-product-label"
            value={product}
            label="Product"
            onChange={handleProductChange}
          >
            {products.map((p) => (
              <MenuItem key={p.product_code} value={p.product_code}>
                {p.product_code} · {p.name}
              </MenuItem>
            ))}
          </Select>
        </FormControl>

        <ToggleButtonGroup
          value={viewMode}
          exclusive
          onChange={handleViewModeChange}
          size="small"
        >
          <ToggleButton value="future">Future</ToggleButton>
          <ToggleButton value="history">History</ToggleButton>
        </ToggleButtonGroup>
      </Box>

      {loading && (
        <Box sx={{ display: "flex", justifyContent: "center", py: 4 }}>
          <CircularProgress size={32} />
        </Box>
      )}
      {error && (
        <Alert severity="error" variant="filled">
          Failed to load futures data: {error}
        </Alert>
      )}
      {!loading && !error && data && chartOption && summary && (
        <>
          <Typography variant="caption" color="text.secondary">
            {summary.active} active · {summary.matured} matured ·{" "}
            {summary.nDays} trading days · {summary.d0} → {summary.d1} ·{" "}
            {summary.contract_type}
          </Typography>

          <EChart
            option={chartOption}
            height={520}
            minHeight={360}
            onEvents={onEvents}
          />
        </>
      )}
    </Stack>
  );
}

// Re-export Row type for the chartOption builder if needed later
export type { FuturesRow };