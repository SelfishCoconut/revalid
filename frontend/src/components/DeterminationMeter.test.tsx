import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { DeterminationMeter } from "./DeterminationMeter";

describe("DeterminationMeter", () => {
  it("shows the empty-state hint when there are no determinations", () => {
    render(<DeterminationMeter counts={{ still_open: 0, inconclusive: 0, fixed: 0 }} />);
    expect(screen.getByText(/No determinations yet/)).toBeInTheDocument();
    expect(screen.getAllByText("0")).toHaveLength(3);
  });

  it("renders the per-status readouts and drops the empty-state hint once populated", () => {
    render(<DeterminationMeter counts={{ still_open: 2, inconclusive: 0, fixed: 1 }} />);
    expect(screen.getByText("2")).toBeInTheDocument();
    expect(screen.getByText("1")).toBeInTheDocument();
    expect(screen.getByText("still open")).toBeInTheDocument();
    expect(screen.queryByText(/No determinations yet/)).not.toBeInTheDocument();
  });
});
