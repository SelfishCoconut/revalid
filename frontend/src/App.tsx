import { useEffect, useState } from "react";
import { NavLink, Route, Routes, useLocation } from "react-router-dom";

import { BrandMark } from "./components/BrandMark";
import { FindingLayout } from "./components/FindingLayout";
import { SidebarContent } from "./components/Sidebar";
import { useTheme } from "./lib/theme";
import { NewReport } from "./routes/NewReport";
import { ReportDetail } from "./routes/ReportDetail";
import { ReportsOverview } from "./routes/ReportsOverview";
import { RetestSessionRoute } from "./routes/RetestSessionRoute";
import Settings from "./routes/Settings";
import { ExtractStage } from "./routes/stages/ExtractStage";
import { GoalStage } from "./routes/stages/GoalStage";
import { RetestStage } from "./routes/stages/RetestStage";
import { StageRedirect } from "./routes/stages/StageRedirect";
import { VerdictStage } from "./routes/stages/VerdictStage";

function MenuIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 18 18" fill="none" aria-hidden="true">
      <path
        d="M2.5 4.5h13M2.5 9h13M2.5 13.5h13"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
      />
    </svg>
  );
}

export function App() {
  const { theme, setTheme } = useTheme();
  const [drawerOpen, setDrawerOpen] = useState(false);
  // The agentic retest console is a cockpit: it earns the full column width so the
  // conversation, goal rail, and terminal aren't squeezed into the reading-width
  // cap the rest of the app uses. Both entry points (the finding stage and the
  // deep-link session route) get it.
  const { pathname } = useLocation();
  // Every finding stage (extract/goal/retest/verdict) and the retest console
  // get the full-width shell; overview/report/settings stay in the reading column.
  const wideRoute =
    pathname.startsWith("/findings/") || pathname.startsWith("/retest-sessions/");

  // Sidebar links close the drawer via onNavigate; also close it on Escape.
  useEffect(() => {
    if (!drawerOpen) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setDrawerOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
    };
  }, [drawerOpen]);

  return (
    <div className="min-h-screen lg:grid lg:grid-cols-[260px_1fr]">
      <aside className="sticky top-0 hidden h-screen flex-col border-r border-line bg-ink/50 backdrop-blur lg:flex">
        <SidebarContent theme={theme} setTheme={setTheme} />
      </aside>

      <div className="flex min-h-screen min-w-0 flex-col">
        <header className="sticky top-0 z-20 flex items-center gap-3 border-b border-line bg-ink/70 px-4 py-3 backdrop-blur-md lg:hidden">
          <button
            type="button"
            onClick={() => {
              setDrawerOpen(true);
            }}
            aria-label="Open navigation"
            className="rounded-lg border border-line p-1.5 text-dim transition-colors hover:text-fg"
          >
            <MenuIcon />
          </button>
          <NavLink to="/" className="flex items-center gap-2.5" aria-label="revalid home">
            <BrandMark size={26} />
            <span className="font-mono text-[15px] font-semibold tracking-tight text-fg">
              revalid
            </span>
          </NavLink>
        </header>

        <main
          className={`mx-auto w-full min-w-0 flex-1 px-5 py-8 ${
            wideRoute ? "max-w-[100rem]" : "max-w-[64rem]"
          }`}
        >
          <Routes>
            <Route path="/" element={<ReportsOverview />} />
            <Route path="/new" element={<NewReport />} />
            <Route path="/reports/:id" element={<ReportDetail />} />
            <Route path="/findings/:id" element={<FindingLayout />}>
              <Route index element={<StageRedirect />} />
              <Route path="extract" element={<ExtractStage />} />
              <Route path="goal" element={<GoalStage />} />
              <Route path="retest" element={<RetestStage />} />
              <Route path="verdict" element={<VerdictStage />} />
            </Route>
            <Route path="/retest-sessions/:id" element={<RetestSessionRoute />} />
            <Route path="/settings" element={<Settings />} />
          </Routes>
        </main>

        <footer className="border-t border-line">
          <div className="mx-auto flex max-w-[64rem] flex-wrap items-center gap-x-2 gap-y-1 px-5 py-4 font-mono text-[11px] text-faint">
            <span className="text-dim">revalid</span>
            <span aria-hidden="true">·</span>
            <span>AI-driven revalidation of pentest findings</span>
            <span aria-hidden="true">·</span>
            <span>retests only ever hit allowlisted lab targets</span>
          </div>
        </footer>
      </div>

      {drawerOpen && (
        <div className="fixed inset-0 z-40 lg:hidden">
          <button
            type="button"
            aria-label="Close navigation"
            onClick={() => {
              setDrawerOpen(false);
            }}
            className="absolute inset-0 bg-black/50 backdrop-blur-sm"
          />
          <div className="rev-drawer absolute inset-y-0 left-0 w-[280px] max-w-[85%] border-r border-line bg-ink shadow-2xl">
            <SidebarContent
              theme={theme}
              setTheme={setTheme}
              onNavigate={() => {
                setDrawerOpen(false);
              }}
            />
          </div>
        </div>
      )}
    </div>
  );
}
