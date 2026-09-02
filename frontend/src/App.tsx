import { HashRouter, Routes, Route, Link, useLocation } from "react-router-dom";
import UploadPage from "./pages/Upload";
import DashboardPage from "./pages/Dashboard";
import HistoryPage from "./pages/History";
import SettingsPage from "./pages/Settings";
import { RobotIcon } from "./components/RobotIcon";

function Rail() {
  const location = useLocation();
  const isUpload = location.pathname === "/";
  const isHistory = location.pathname === "/history";
  const isSettings = location.pathname === "/settings";

  return (
    <aside className="rail">
      <div>
        <Link to="/" className="mark">
          <span className="mark-glyph">
            <RobotIcon className="bot-icon" />
          </span>
          <span className="mark-word">Data Loom</span>
        </Link>
        <div className="mark-sub">Multi-Agent Intelligence Extraction System</div>
      </div>
      <nav className="rail-nav">
        <Link to="/" className={`rail-link ${isUpload ? "is-active" : ""}`}>
          <span className="rail-ico">＋</span> New Analysis
        </Link>
        <Link to="/history" className={`rail-link ${isHistory ? "is-active" : ""}`}>
          <span className="rail-ico">▦</span> Job History
        </Link>
        <Link to="/settings" className={`rail-link ${isSettings ? "is-active" : ""}`}>
          <span className="rail-ico">⚙</span> Settings
        </Link>
      </nav>
      <div className="rail-footer">
        <div className="rail-footer-tag">◈ Air-gapped by default</div>
        <div>No outbound network calls unless a deployment explicitly opts in under Settings.</div>
      </div>
    </aside>
  );
}

export default function App() {
  return (
    <HashRouter>
      <div className="shell">
        <Rail />
        <main className="main">
          <Routes>
            <Route path="/" element={<UploadPage />} />
            <Route path="/jobs/:jobId" element={<DashboardPage />} />
            <Route path="/history" element={<HistoryPage />} />
            <Route path="/settings" element={<SettingsPage />} />
          </Routes>
        </main>
      </div>
    </HashRouter>
  );
}
