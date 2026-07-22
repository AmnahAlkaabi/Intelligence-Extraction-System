import { HashRouter, Routes, Route, Link } from "react-router-dom";
import UploadPage from "./pages/Upload";
import DashboardPage from "./pages/Dashboard";
import HistoryPage from "./pages/History";

export default function App() {
  return (
    <HashRouter>
      <div className="app-shell">
        <header className="topbar">
          <Link to="/" className="brand">
            <span className="brand-mark">◈</span> Intelligence Extraction System
            <span className="brand-tag">air-gapped</span>
          </Link>
          <nav className="topnav">
            <Link to="/">New Analysis</Link>
            <Link to="/history">Job History</Link>
          </nav>
        </header>
        <main className="app-main">
          <Routes>
            <Route path="/" element={<UploadPage />} />
            <Route path="/jobs/:jobId" element={<DashboardPage />} />
            <Route path="/history" element={<HistoryPage />} />
          </Routes>
        </main>
      </div>
    </HashRouter>
  );
}
