import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { PipelineTrack, type Stage } from "./PipelineTrack";

const STAGES = ["extract", "goal", "retest", "verdict"];

function renderTrack(props: {
  sessionExists: boolean;
  hasVerdict: boolean;
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
      sessionExists: false,
      hasVerdict: false,
      findingId: 7,
      activeStage: "retest",
    });
    for (const stage of STAGES) {
      expect(screen.getByText(stage)).toBeInTheDocument();
    }
  });

  it("colours the final node from the verdict once retested", () => {
    renderTrack({
      sessionExists: true,
      hasVerdict: true,
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
      sessionExists: false,
      hasVerdict: false,
      findingId: 7,
      activeStage: "retest",
    });
    // extract + goal are reached and retest is the current action → navigable.
    expect(screen.getByRole("link", { name: /go to extract stage/i })).toHaveAttribute(
      "href",
      "/findings/7/extract",
    );
    expect(screen.getByRole("link", { name: /go to goal stage/i })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /go to retest stage/i })).toBeInTheDocument();
    // verdict is not yet reached → not a link.
    expect(screen.queryByRole("link", { name: /go to verdict stage/i })).not.toBeInTheDocument();
  });

  it("marks the active stage with aria-current", () => {
    renderTrack({
      sessionExists: false,
      hasVerdict: false,
      findingId: 7,
      activeStage: "retest",
    });
    expect(screen.getByRole("link", { name: /go to retest stage/i })).toHaveAttribute(
      "aria-current",
      "step",
    );
    expect(screen.getByRole("link", { name: /go to extract stage/i })).not.toHaveAttribute(
      "aria-current",
    );
  });
});
