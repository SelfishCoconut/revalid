import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

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
});
