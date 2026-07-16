import { useOutletContext } from "react-router-dom";

import type { Finding, Plan, Verdict } from "../api/types";
import type { Stage } from "../components/PipelineTrack";

/** Everything a stage page needs, shared through the finding layout's Outlet. */
export interface FindingStageContext {
  finding: Finding;
  findingId: number;
  plans: Plan[];
  currentPlan?: Plan;
  hasPlan: boolean;
  approved: boolean;
  retested: boolean;
  /** This finding's verdicts, newest first. */
  verdicts: Verdict[];
  /** The furthest actionable stage — where an index visit lands. */
  currentStage: Stage;
}

/** Read the shared finding context from inside a stage page (ADR-0024). */
export function useFindingStage(): FindingStageContext {
  return useOutletContext<FindingStageContext>();
}
