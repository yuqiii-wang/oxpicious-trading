/**
 * Futures Analysis page — CFFEX futures basis + correlation analysis.
 *
 * Layout:
 *   • Header — title + subtitle + back button + Refresh
 *   • Product selector (IC/IF/IH/IM index or T/TF/TL/TS bond)
 *   • FuturesCharts — 2 plots:
 *       1. Futures price curves (identical to Data Viz) with gap_price_vs_underlying
 *          added to the tooltip for each contract.
 *       2. Correlation (corr_price_vs_underlying, 20d rolling) per contract.
 */
import { useEffect, useState } from "react";
import {
  Alert,
  Box,
  CircularProgress,
  FormControl,
  IconButton,
  InputLabel,
  MenuItem,
  Select,
  type SelectChangeEvent,
  Stack,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from "@mui/material";
import { ArrowBack } from "@mui/icons-material";
import { useNavigate } from "react-router-dom";
import RefreshButton from "@/components/RefreshButton";
import {
  fetchFuturesProducts,
  fetchFuturesCombined,
  invalidateCacheForPrefix,
} from "@/lib/api-client";
import type {
  FuturesProduct,
  FuturesCombinedResponse,
} from "@shared/types";
import { FuturesCharts } from "./FuturesCharts";

const DEFAULT_PRODUCT = "IF";
type ViewMode = "future" | "history";

export default function FuturesAnalysisPage() {
  const navigate = useNavigate();

  const [products, setProducts] = useState<FuturesProduct[]>([]);
  const [product, setProduct] = useState<string>(DEFAULT_PRODUCT);
  const [data, setData] = useState<FuturesCombinedResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [viewMode, setViewMode] = useState<ViewMode>("future");
  const [refreshKey, setRefreshKey] = useState(0);

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
    return () => { cancelled = true; };
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
    invalidateCacheForPrefix("/api/analysis/futures/");
    setRefreshKey((k) => k + 1);
  };

  const productLabel =
    products.find((p) => p.product_code === product)?.name ?? product;
  const underlyingLabel =
    data?.underlying_name && data?.underlying_code
      ? `underlying ${data.underlying_code} (${data.underlying_name})`
      : "";

  return (
    <Stack spacing={2}>
      {/* Header */}
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
          <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
            <IconButton
              onClick={() => navigate("/analysis/derivatives")}
              size="small"
              aria-label="back to derivatives"
            >
              <ArrowBack />
            </IconButton>
            <Typography variant="h5" sx={{ fontWeight: 700 }}>
              Futures Analysis
            </Typography>
          </Box>
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

      {/* Controls */}
      <Box
        sx={{
          display: "flex",
          gap: 2,
          flexWrap: "wrap",
          alignItems: "center",
        }}
      >
        <FormControl size="small" sx={{ minWidth: 200 }}>
          <InputLabel id="futures-analysis-product-label">Product</InputLabel>
          <Select
            labelId="futures-analysis-product-label"
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

      {/* Loading / Error */}
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

      {/* Charts */}
      {!loading && !error && data && (
        <FuturesCharts
          product={product}
          combinedData={data}
          viewMode={viewMode}
        />
      )}
    </Stack>
  );
}
