import { BrowserRouter as Router, Routes, Route, Navigate } from "react-router-dom";
import { Box } from "@mui/material";
import TopAppBar from "@/components/TopAppBar";
import DebtBaselinePage from "@/dataviz/pages/DebtBaselinePage";
import SzseOptionsPage from "@/dataviz/pages/SzseOptionsPage";
import EtfMarginPage from "@/dataviz/pages/EtfMarginPage";
import IndexBaselinePage from "@/dataviz/pages/IndexBaselinePage";
import StockBaselinePage from "@/dataviz/pages/StockBaselinePage";
import CommonsPage from "@/analysis/pages/CommonsPage";
import MaSpreadPage from "@/analysis/pages/MaSpreadPage";
import PerfAttrPage from "@/analysis/pages/PerfAttrPage";
import CapitalFlowPage from "@/analysis/pages/CapitalFlowPage";

export default function App() {
  return (
    <Router>
      <TopAppBar />
      <Box component="main" sx={{ p: { xs: 1.5, md: 3 }, maxWidth: 1600, mx: "auto" }}>
        <Routes>
          <Route path="/" element={<Navigate to="/dataviz/debt-baseline" replace />} />
          <Route path="/dataviz" element={<Navigate to="/dataviz/debt-baseline" replace />} />
          <Route path="/dataviz/debt-baseline" element={<DebtBaselinePage />} />
          <Route path="/dataviz/szse-options" element={<SzseOptionsPage />} />
          <Route path="/dataviz/etf-margin" element={<EtfMarginPage />} />
          <Route path="/dataviz/index-baseline" element={<IndexBaselinePage />} />
          <Route path="/dataviz/stock-baseline" element={<StockBaselinePage />} />
          <Route path="/analysis" element={<Navigate to="/analysis/commons" replace />} />
          <Route path="/analysis/commons" element={<CommonsPage />} />
          <Route path="/analysis/commons/ma-spread" element={<MaSpreadPage />} />
          <Route path="/analysis/commons/perf-attr" element={<PerfAttrPage />} />
          <Route path="/analysis/commons/capital-flow" element={<CapitalFlowPage />} />
        </Routes>
      </Box>
    </Router>
  );
}
