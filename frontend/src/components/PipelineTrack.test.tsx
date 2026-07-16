import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { PipelineTrack, type Stage } from "./PipelineTrack";

const STAGES = ["extract", "plan", "approve", "retest", "verdict"];

function renderTrack(props: {
  planned: boolean;
  approved: boolean;
  retested: boolean;
  verdict?: "still_open" | "fixed" | "inconclusive";
  findingId: number;
  activeStage: Stage;
}) {
  return render(
    <MemoryRouter>
      <PipelineTrack {...props} />
    </MemoryRouter>,
  );
}

describe("PipelineTrack", () => {
  it("renders every stage label at a mid-pipeline state", () => {
    renderTrack({
      planned: true,
      approved: false,
      retested: false,
      findingId: 7,
      activeStage: "plan",
    });
    for (const stage of STAGES) {
      expect(screen.getByText(stage)).toBeInTheDocument();
    }
  });

  it("colours the final node from the verdict once retested", () => {
    renderTrack({
      planned: true,
      approved: true,
      retested: true,
      verdict: "fixed",
      findingId: 7,
      activeStage: "verdict",
    });
    for (const stage of STAGES) {
      expect(screen.getByText(stage)).toBeInTheDocument();
    }
  });

  it("links reached and current stages, leaving not-yet-reached stages inert", () => {
    renderTrack({
      planned: true,
      approved: false,
      retested: false,
      findingId: 7,
      activeStage: "plan",
    });
    // extract + plan are reached and approve is the current action → navigable.
    expect(screen.getByRole("link", { name: /go to extract stage/i })).toHaveAttribute(
      "href",
      "/findings/7/extract",
    );
    expect(screen.getByRole("link", { name: /go to approve stage/i })).toBeInTheDocument();
    // retest + verdict are not yet reached → not links.
    expect(screen.queryByRole("link", { name: /go to retest stage/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /go to verdict stage/i })).not.toBeInTheDocument();
  });

  it("marks the active stage with aria-current", () => {
    renderTrack({
      planned: true,
      approved: false,
      retested: false,
      findingId: 7,
      activeStage: "plan",
    });
    expect(screen.getByRole("link", { name: /go to plan stage/i })).toHaveAttribute(
      "aria-current",
      "step",
    );
    expect(screen.getByRole("link", { name: /go to extract stage/i })).not.toHaveAttribute(
      "aria-current",
    );
  });
});
