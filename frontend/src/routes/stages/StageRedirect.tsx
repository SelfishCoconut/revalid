import { Navigate } from "react-router-dom";

import { useFindingStage } from "../../hooks/useFindingStage";

/**
 * Index of `/findings/:id`: send the operator to the furthest actionable stage,
 * so opening a finding lands where the work is (ADR-0024).
 */
export function StageRedirect() {
  const { findingId, currentStage } = useFindingStage();
  return <Navigate to={`/findings/${String(findingId)}/${currentStage}`} replace />;
}
