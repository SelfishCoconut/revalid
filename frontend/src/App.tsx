import { NavLink, Route, Routes } from "react-router-dom";

import { FindingDetail } from "./routes/FindingDetail";
import { ReportDetail } from "./routes/ReportDetail";
import { ReportsOverview } from "./routes/ReportsOverview";

export function App() {
  return (
    <div className="min-h-screen bg-slate-50 text-slate-900">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-5xl items-center gap-3 px-4 py-3">
          <NavLink to="/" className="text-lg font-semibold tracking-tight text-slate-800">
            revalid
          </NavLink>
          <span className="text-sm text-slate-400">
            pentest finding revalidation
          </span>
        </div>
      </header>
      <main className="mx-auto max-w-5xl px-4 py-6">
        <Routes>
          <Route path="/" element={<ReportsOverview />} />
          <Route path="/reports/:id" element={<ReportDetail />} />
          <Route path="/findings/:id" element={<FindingDetail />} />
        </Routes>
      </main>
    </div>
  );
}
