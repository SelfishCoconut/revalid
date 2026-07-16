import userEvent from "@testing-library/user-event";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { PipelineTrack } from "./PipelineTrack";

const STAGES = ["extract", "plan", "approve", "retest", "verdict"];

describe("PipelineTrack", () => {
  it("renders every stage label at a mid-pipeline state", () => {
    render(<PipelineTrack planned approved={false} retested={false} />);
    for (const stage of STAGES) {
      expect(screen.getByText(stage)).toBeInTheDocument();
    }
  });

  it("renders the full track once a verdict exists", () => {
    render(<PipelineTrack planned approved retested verdict="fixed" />);
    for (const stage of STAGES) {
      expect(screen.getByText(stage)).toBeInTheDocument();
    }
  });

  it("makes a reached stage with a back-action a clickable button (ADR-0023)", async () => {
    const onPlan = vi.fn();
    render(
      <PipelineTrack
        planned
        approved={false}
        retested={false}
        onStageBack={{ plan: onPlan }}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: /step back to plan/i }));
    expect(onPlan).toHaveBeenCalledOnce();
  });

  it("ignores a back-action on a stage that is not yet reached", () => {
    // `approve` is not reached when only planned — its handler must not wire a button.
    render(
      <PipelineTrack
        planned
        approved={false}
        retested={false}
        onStageBack={{ approve: vi.fn() }}
      />,
    );
    expect(
      screen.queryByRole("button", { name: /step back to approve/i }),
    ).not.toBeInTheDocument();
  });
});
