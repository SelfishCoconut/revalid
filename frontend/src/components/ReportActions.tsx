import type { Report } from "../api/types";
import { useDeleteReport, useSetReportArchived } from "../hooks/useReports";

function ArchiveIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <rect x="2" y="2.75" width="12" height="3" rx="1" stroke="currentColor" strokeWidth="1.2" />
      <path
        d="M3 5.75v6A1.5 1.5 0 0 0 4.5 13.25h7a1.5 1.5 0 0 0 1.5-1.5v-6"
        stroke="currentColor"
        strokeWidth="1.2"
      />
      <path d="M6.5 8.5h3" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" />
    </svg>
  );
}

function TrashIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <path
        d="M2.75 4.25h10.5M6 4.25V3.1A1.1 1.1 0 0 1 7.1 2h1.8A1.1 1.1 0 0 1 10 3.1v1.15M4.25 4.25l.5 8.35A1.3 1.3 0 0 0 6.05 13.8h3.9a1.3 1.3 0 0 0 1.3-1.2l.5-8.35M6.6 6.75v4M9.4 6.75v4"
        stroke="currentColor"
        strokeWidth="1.2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

const iconButton =
  "grid size-7 place-items-center rounded-md text-faint transition-colors disabled:opacity-40";

/**
 * The per-report archive + delete controls, as icon buttons. Shared by the
 * overview table and the sidebar jump-list; the parent reveals them on hover by
 * passing an `opacity-0 group-hover:opacity-100` className (#128). Clicks stop
 * propagation so they never trigger a surrounding row link.
 */
export function ReportActions({ report, className = "" }: { report: Report; className?: string }) {
  const setArchived = useSetReportArchived();
  const remove = useDeleteReport();
  const busy = setArchived.isPending || remove.isPending;
  const archiveLabel = report.archived ? "Unarchive report" : "Archive report";

  function confirmDelete() {
    const ok = window.confirm(
      `Delete "${report.filename}" and all its findings, verdicts and retest history?\n` +
        `This cannot be undone.`,
    );
    if (ok) {
      remove.mutate(report.id);
    }
  }

  return (
    <div className={`flex items-center gap-0.5 ${className}`}>
      <button
        type="button"
        disabled={busy}
        title={archiveLabel}
        aria-label={archiveLabel}
        className={`${iconButton} hover:bg-panel-2 hover:text-fg`}
        onClick={(event) => {
          event.preventDefault();
          event.stopPropagation();
          setArchived.mutate({ id: report.id, archived: !report.archived });
        }}
      >
        <ArchiveIcon />
      </button>
      <button
        type="button"
        disabled={busy}
        title="Delete report"
        aria-label="Delete report"
        className={`${iconButton} hover:bg-danger/10 hover:text-danger-fg`}
        onClick={(event) => {
          event.preventDefault();
          event.stopPropagation();
          confirmDelete();
        }}
      >
        <TrashIcon />
      </button>
    </div>
  );
}
