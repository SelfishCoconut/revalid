import { NavLink } from "react-router-dom";

import type { ReportStatus } from "../api/types";
import { useReports } from "../hooks/useReports";
import { useBackendStatus } from "../hooks/useSettings";
import type { Theme } from "../lib/theme";
import { BrandMark } from "./BrandMark";
import { ReportActions } from "./ReportActions";
import { Spinner } from "./Spinner";
import { ThemeToggle } from "./ThemeToggle";
import { Eyebrow } from "./ui/Panel";

const STATUS_DOT: Record<ReportStatus, string> = {
  extracting: "bg-iris",
  ready: "bg-ok",
  failed: "bg-danger",
};

function navItemClass({ isActive }: { isActive: boolean }): string {
  const base =
    "flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-[13px] transition-colors";
  return isActive
    ? `${base} bg-iris/12 text-fg ring-1 ring-inset ring-iris/25`
    : `${base} text-dim hover:bg-panel-2 hover:text-fg`;
}

function OverviewIcon() {
  return (
    <svg
      width="15"
      height="15"
      viewBox="0 0 16 16"
      fill="none"
      aria-hidden="true"
      className="shrink-0"
    >
      <rect x="1.5" y="1.5" width="5" height="5" rx="1.4" fill="currentColor" />
      <rect x="9.5" y="1.5" width="5" height="5" rx="1.4" stroke="currentColor" strokeWidth="1.3" />
      <rect x="1.5" y="9.5" width="5" height="5" rx="1.4" stroke="currentColor" strokeWidth="1.3" />
      <rect x="9.5" y="9.5" width="5" height="5" rx="1.4" fill="currentColor" />
    </svg>
  );
}

function SettingsIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 16 16" fill="none" aria-hidden="true" className="shrink-0">
      <circle cx="8" cy="8" r="2.2" stroke="currentColor" strokeWidth="1.3" />
      <path
        d="M8 1.5v1.6M8 12.9v1.6M14.5 8h-1.6M3.1 8H1.5M12.6 3.4l-1.1 1.1M4.5 11.5l-1.1 1.1M12.6 12.6l-1.1-1.1M4.5 4.5 3.4 3.4"
        stroke="currentColor"
        strokeWidth="1.3"
        strokeLinecap="round"
      />
    </svg>
  );
}

function ChatIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 16 16" fill="none" aria-hidden="true" className="shrink-0">
      <path
        d="M2 3.2A1.2 1.2 0 0 1 3.2 2h9.6A1.2 1.2 0 0 1 14 3.2v6.6a1.2 1.2 0 0 1-1.2 1.2H6l-3 2.6v-2.6H3.2A1.2 1.2 0 0 1 2 9.8z"
        stroke="currentColor"
        strokeWidth="1.3"
        strokeLinejoin="round"
      />
    </svg>
  );
}

/**
 * The console's left rail: identity, primary navigation, a live jump-list of
 * recent reports, and the theme control. Rendered both as the desktop sidebar
 * and inside the mobile drawer, so `onNavigate` lets the drawer close on a tap.
 */
export function SidebarContent({
  theme,
  setTheme,
  onNavigate,
}: {
  theme: Theme;
  setTheme: (theme: Theme) => void;
  onNavigate?: () => void;
}) {
  const reports = useReports();
  const recent = [...(reports.data ?? [])]
    .sort(
      (a, b) =>
        new Date(b.created_at).getTime() - new Date(a.created_at).getTime() ||
        b.id - a.id,
    )
    .slice(0, 8);

  return (
    <div className="flex h-full flex-col">
      <NavLink
        to="/"
        end
        onClick={onNavigate}
        className="flex items-center gap-3 border-b border-line px-4 py-4"
        aria-label="revalid home"
      >
        <BrandMark />
        <span className="leading-none">
          <span className="block font-mono text-[15px] font-semibold tracking-tight text-fg">
            revalid
          </span>
          <span className="mt-1 block font-mono text-[10px] uppercase tracking-[0.2em] text-faint">
            revalidation console
          </span>
        </span>
      </NavLink>

      <nav className="flex-1 space-y-6 overflow-y-auto px-3 py-5">
        <div>
          <Eyebrow className="px-2">Navigate</Eyebrow>
          <div className="mt-2">
            <NavLink to="/" end onClick={onNavigate} className={navItemClass}>
              <OverviewIcon />
              Overview
            </NavLink>
            <NavLink to="/chat" onClick={onNavigate} className={navItemClass}>
              <ChatIcon />
              Chat
            </NavLink>
            <NavLink to="/settings" onClick={onNavigate} className={navItemClass}>
              <SettingsIcon />
              Settings
            </NavLink>
          </div>
        </div>

        <div>
          <div className="flex items-center justify-between px-2">
            <Eyebrow>Reports</Eyebrow>
            <span className="font-mono text-[10px] text-faint">{recent.length}</span>
          </div>
          <div className="mt-2 space-y-0.5">
            {reports.isPending ? (
              <div className="px-2 py-1">
                <Spinner label="Loading" />
              </div>
            ) : recent.length === 0 ? (
              <p className="px-2.5 py-1.5 text-[12px] text-faint">No reports yet.</p>
            ) : (
              recent.map((report) => (
                <div key={report.id} className="group relative">
                  <NavLink
                    to={`/reports/${String(report.id)}`}
                    onClick={onNavigate}
                    className={navItemClass}
                    title={report.filename}
                  >
                    <span
                      aria-hidden="true"
                      className={`size-1.5 shrink-0 rounded-full ${STATUS_DOT[report.status]}`}
                    />
                    <span className="truncate font-mono text-[12px]">{report.filename}</span>
                  </NavLink>
                  <ReportActions
                    report={report}
                    className="absolute top-1/2 right-1 -translate-y-1/2 rounded-md bg-panel/95 opacity-0 shadow-sm ring-1 ring-line/60 transition-opacity group-hover:opacity-100 focus-within:opacity-100"
                  />
                </div>
              ))
            )}
          </div>
        </div>
      </nav>

      <div className="space-y-3 border-t border-line px-3 py-3">
        <ThemeToggle theme={theme} setTheme={setTheme} />
        <BackendPill />
      </div>
    </div>
  );
}

/**
 * Live LLM-backend status: a green (connected) / amber (checking) / red
 * (unreachable) dot beside the active model, polled every 20s. Replaces the old
 * static "localhost · single-user" line so the operator can see, at a glance,
 * that a model is configured and its provider is answering.
 */
function BackendPill() {
  const status = useBackendStatus();
  const connected = status.data?.connected ?? false;
  const model = status.data?.model ?? "—";
  const state = status.isPending ? "checking" : connected ? "connected" : "unreachable";
  const dot = status.isPending ? "bg-warn" : connected ? "bg-ok" : "bg-danger";

  return (
    <div
      className="flex items-center gap-2 px-1 font-mono text-[11px] text-faint"
      title={`LLM backend ${state} · ${model}`}
    >
      <span
        aria-hidden="true"
        className={`size-1.5 shrink-0 rounded-full ${dot} ${connected ? "rev-live" : ""}`}
      />
      <span className="truncate">{status.isPending ? "connecting…" : model}</span>
    </div>
  );
}
