import { BrowserRouter as Router, Routes, Route, Navigate } from "react-router-dom";
import { Box } from "@mui/material";
import TopAppBar from "@/components/TopAppBar";
import DebtBaselinePage from "@/pages/DebtBaselinePage";
import SzseOptionsPage from "@/pages/SzseOptionsPage";
import EtfMarginPage from "@/pages/EtfMarginPage";
import IndexBaselinePage from "@/pages/IndexBaselinePage";

export default function App() {
  return (
    <Router>
      <TopAppBar />
      <Box component="main" sx={{ p: { xs: 1.5, md: 3 }, maxWidth: 1600, mx: "auto" }}>
        <Routes>
          <Route path="/" element={<Navigate to="/debt-baseline" replace />} />
          <Route path="/debt-baseline" element={<DebtBaselinePage />} />
          <Route path="/szse-options" element={<SzseOptionsPage />} />
          <Route path="/etf-margin" element={<EtfMarginPage />} />
          <Route path="/index-baseline" element={<IndexBaselinePage />} />
        </Routes>
      </Box>
    </Router>
  );
}
