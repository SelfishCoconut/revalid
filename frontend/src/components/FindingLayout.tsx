import { Link, Navigate, Outlet, useLocation, useParams } from "react-router-dom";

import { useFindings } from "../hooks/useFindings";
import { useFindingSessions } from "../hooks/useFindingSessions";
import type { FindingStageContext } from "../hooks/useFindingStage";
import { useVerdicts } from "../hooks/useVerdicts";
import { errorMessage } from "../lib/format";
import { pipelineReach } from "../lib/selectors";
import { PipelineTrack, type Stage } from "./PipelineTrack";
import { SeverityBadge } from "./SeverityBadge";
import { Spinner } from "./Spinner";
import { Panel } from "./ui/Panel";

const STAGES: readonly Stage[] = ["extract", "goal", "retest", "verdict"];

function isStage(value: string | undefined): value is Stage {
  return value != null && (STAGES as readonly string[]).includes(value);
}

/**
 * Persistent chrome for the finding stage wizard (ADR-0024): the identity header
 * and the {@link PipelineTrack} stepper stay put while only the stage panel below
 * (the `<Outlet/>`) swaps as the operator walks extract → … → verdict. It loads
 * the finding, its retest sessions, and its verdicts once and shares them via
 * Outlet context so each stage page reads from one cache.
 */
export function FindingLayout() {
  const { id } = useParams();
  const findingId = Number(id);
  const location = useLocation();

  const findings = useFindings();
  const sessionsQuery = useFindingSessions(findingId);
  const verdicts = useVerdicts();

  if (findings.isPending) {
    return <Spinner label="Loading finding" />;
  }
  if (findings.isError) {
    return (
      <p role="alert" className="text-sm text-danger-fg">
        {errorMessage(findings.error)}
      </p>
    );
  }

  const finding = findings.data.find((item) => item.id === findingId);
  if (!finding) {
    return <p className="text-sm text-faint">Finding not found.</p>;
  }

  const sessions = sessionsQuery.data ?? [];
  const findingVerdicts = (verdicts.data ?? [])
    .filter((verdict) => verdict.finding_id === findingId)
    .sort((a, b) => b.id - a.id);

  const reach = pipelineReach({
    sessionExists: sessions.length > 0,
    hasVerdict: findingVerdicts.length > 0,
  });
  const currentStage = STAGES[reach.current];
  const segment = location.pathname.split("/").pop();

  // Deep-linking to a stage ahead of progress (e.g. /verdict before a session
  // exists) would strand the operator on a not-yet-actionable stage — send them
  // to the current stage instead, matching the index route (#83, ADR-0024).
  const requestedIndex = isStage(segment) ? STAGES.indexOf(segment) : -1;
  if (requestedIndex > reach.current) {
    return <Navigate to={`/findings/${String(findingId)}/${currentStage}`} replace />;
  }

  const activeStage = isStage(segment) ? segment : currentStage;

  const context: FindingStageContext = {
    finding,
    findingId,
    sessions,
    latestSession: sessions[0],
    verdicts: findingVerdicts,
    currentStage,
  };

  const backLink = finding.report_id != null ? `/reports/${String(finding.report_id)}` : "/";

  return (
    <div className="rev-rise space-y-6">
      <Link
        to={backLink}
        className="inline-flex items-center gap-1.5 font-mono text-[12px] text-faint transition-colors hover:text-dim"
      >
        <span aria-hidden="true">←</span>
        Back to report
      </Link>

      <Panel className="overflow-hidden">
        <div className="flex flex-wrap items-center gap-3 p-5">
          <SeverityBadge severity={finding.severity} />
          <h1 className="text-xl font-semibold tracking-tight text-fg">{finding.title}</h1>
          <span className="font-mono text-[11px] text-faint">v{finding.version}</span>
        </div>
        <div className="border-t border-line bg-panel-2/30 px-4 py-4">
          <PipelineTrack
            sessionExists={sessions.length > 0}
            hasVerdict={findingVerdicts.length > 0}
            verdict={findingVerdicts[0]?.status}
            findingId={findingId}
            activeStage={activeStage}
          />
        </div>
      </Panel>

      <Outlet context={context} />
    </div>
  );
}
