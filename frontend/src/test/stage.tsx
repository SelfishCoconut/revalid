import type { ReactElement } from "react";

import { Outlet, Route, Routes } from "react-router-dom";

import type { Finding } from "../api/types";
import type { FindingStageContext } from "../hooks/useFindingStage";
import { renderWithProviders } from "./utils";

const BASE_FINDING: Finding = {
  id: 7,
  report_id: 3,
  version: 1,
  title: "SQLi login",
  severity: "high",
  description: "auth bypass",
  impact: "",
  attack_vector: "",
  affected_endpoints: [],
  reproduction_steps: [],
  cvss: { vector: "", base_score: null, inferred: false },
  mitre: { techniques: [], inferred: false },
  raw: {},
};

/** A finding-stage Outlet context with sensible defaults for stage-page tests. */
export function stageContext(overrides: Partial<FindingStageContext> = {}): FindingStageContext {
  return {
    finding: BASE_FINDING,
    findingId: 7,
    sessions: [],
    latestSession: undefined,
    verdicts: [],
    currentStage: "goal",
    ...overrides,
  };
}

/** Render a stage page inside a parent route that supplies the Outlet context. */
export function renderStage(ui: ReactElement, ctx: FindingStageContext) {
  return renderWithProviders(
    <Routes>
      <Route element={<Outlet context={ctx} />}>
        <Route index element={ui} />
      </Route>
    </Routes>,
    "/",
  );
}
