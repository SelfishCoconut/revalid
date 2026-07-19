import { Navigate } from "react-router-dom";

import { useFindingStage } from "../../hooks/useFindingStage";
import { RetestSession } from "../RetestSession";

/** Stage 3 — the agentic retest console for the finding's latest session (FR-17). */
export function RetestStage() {
  const { findingId, latestSession } = useFindingStage();
  if (!latestSession) {
    return <Navigate to={`/findings/${String(findingId)}/goal`} replace />;
  }
  return <RetestSession sessionId={latestSession.id} />;
}
