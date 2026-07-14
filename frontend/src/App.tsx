import { NavLink, Route, Routes } from "react-router-dom";

import { BrandMark } from "./components/BrandMark";
import { FindingDetail } from "./routes/FindingDetail";
import { ReportDetail } from "./routes/ReportDetail";
import { ReportsOverview } from "./routes/ReportsOverview";

function Masthead() {
  return (
    <header className="sticky top-0 z-20 border-b border-line bg-ink/70 backdrop-blur-md">
      <div className="mx-auto flex max-w-[72rem] items-center justify-between gap-4 px-5 py-3.5">
        <NavLink to="/" className="group flex items-center gap-3" aria-label="revalid home">
          <BrandMark />
          <span className="leading-none">
            <span className="font-mono text-[17px] font-semibold tracking-tight text-fg">
              revalid
            </span>
            <span className="mt-1 hidden font-mono text-[10px] uppercase tracking-[0.22em] text-faint sm:block">
              revalidation console
            </span>
          </span>
        </NavLink>

        <span className="inline-flex items-center gap-2 rounded-full border border-line bg-panel/60 px-3 py-1.5 font-mono text-[11px] tracking-wide text-dim">
          <span aria-hidden="true" className="rev-live size-1.5 rounded-full bg-ok" />
          localhost
          <span className="hidden text-faint sm:inline">· single-user</span>
        </span>
      </div>
    </header>
  );
}

export function App() {
  return (
    <div className="flex min-h-screen flex-col">
      <Masthead />
      <main className="mx-auto w-full max-w-[72rem] flex-1 px-5 py-8">
        <Routes>
          <Route path="/" element={<ReportsOverview />} />
          <Route path="/reports/:id" element={<ReportDetail />} />
          <Route path="/findings/:id" element={<FindingDetail />} />
        </Routes>
      </main>
      <footer className="border-t border-line">
        <div className="mx-auto flex max-w-[72rem] flex-wrap items-center gap-x-2 gap-y-1 px-5 py-4 font-mono text-[11px] text-faint">
          <span className="text-dim">revalid</span>
          <span aria-hidden="true">·</span>
          <span>AI-driven revalidation of pentest findings</span>
          <span aria-hidden="true">·</span>
          <span>retests only ever hit allowlisted lab targets</span>
        </div>
      </footer>
    </div>
  );
}
