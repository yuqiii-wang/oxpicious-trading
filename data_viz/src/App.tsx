import { BrowserRouter as Router, Routes, Route, Navigate } from "react-router-dom";
import { Box } from "@mui/material";
import TopAppBar from "@/components/TopAppBar";
import DebtBaselinePage from "@/dataviz/pages/DebtBaselinePage";
import SzseOptionsPage from "@/dataviz/pages/SzseOptionsPage";
import EtfMarginPage from "@/dataviz/pages/EtfMarginPage";
import IndexBaselinePage from "@/dataviz/pages/IndexBaselinePage";
import StockBaselinePage from "@/dataviz/pages/StockBaselinePage";
import LiveDataIndexPage from "@/live/pages/LiveDataIndexPage";
import LiveDataEtfPage from "@/live/pages/LiveDataEtfPage";
import LiveDataStockPage from "@/live/pages/LiveDataStockPage";
import CommonsPage from "@/analysis/pages/CommonsPage";
import DerivativesPage from "@/analysis/pages/DerivativesPage";
import CompositesPage from "@/analysis/pages/CompositesPage";
import MaSpreadPage from "@/analysis/pages/MaSpreadPage";
import IndustrySentimentsPage from "@/analysis/pages/IndustrySentimentsPage";
import SingletonStrategyPage from "@/strategy/SingletonStrategyPage";

export default function App() {
  return (
    <Router>
      <TopAppBar />
      <Box component="main" sx={{ p: { xs: 1.5, md: 3 }, maxWidth: 1600, mx: "auto" }}>
        <Routes>
          <Route path="/" element={<Navigate to="/dataviz/debt-baseline" replace />} />
          <Route path="/live" element={<Navigate to="/live/index" replace />} />
          <Route path="/live/index" element={<LiveDataIndexPage />} />
          <Route path="/live/etf" element={<LiveDataEtfPage />} />
          <Route path="/live/stock" element={<LiveDataStockPage />} />
          <Route path="/dataviz" element={<Navigate to="/dataviz/debt-baseline" replace />} />
          <Route path="/dataviz/debt-baseline" element={<DebtBaselinePage />} />
          <Route path="/dataviz/szse-options" element={<SzseOptionsPage />} />
          <Route path="/dataviz/etf-margin" element={<EtfMarginPage />} />
          <Route path="/dataviz/index-baseline" element={<IndexBaselinePage />} />
          <Route path="/dataviz/stock-baseline" element={<StockBaselinePage />} />
          <Route path="/analysis" element={<Navigate to="/analysis/commons" replace />} />
          <Route path="/analysis/commons" element={<CommonsPage />} />
          <Route path="/analysis/commons/ma-spread" element={<MaSpreadPage />} />
          <Route path="/analysis/commons/industry-sentiments" element={<IndustrySentimentsPage />} />
          <Route path="/analysis/derivatives" element={<DerivativesPage />} />
          <Route path="/analysis/composites" element={<CompositesPage />} />
          <Route path="/strategy" element={<Navigate to="/strategy/singleton" replace />} />
          <Route
            path="/strategy/commons"
            element={<Box sx={{ p: 4, color: "text.secondary" }}>Strategy Commons — coming soon.</Box>}
          />
          <Route path="/strategy/singleton" element={<SingletonStrategyPage />} />
        </Routes>
      </Box>
    </Router>
  );
}
