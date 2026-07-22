import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { SeverityMeter } from "./SeverityMeter";

describe("SeverityMeter", () => {
  it("shows the empty-state hint and five zero readouts with no findings", () => {
    render(
      <SeverityMeter counts={{ critical: 0, high: 0, medium: 0, low: 0, info: 0 }} />,
    );
    expect(screen.getByText(/No findings yet/)).toBeInTheDocument();
    expect(screen.getAllByText("0")).toHaveLength(5);
  });

  it("renders the per-severity readouts and drops the hint once populated", () => {
    render(
      <SeverityMeter counts={{ critical: 3, high: 0, medium: 2, low: 0, info: 1 }} />,
    );
    expect(screen.getByText("3")).toBeInTheDocument();
    expect(screen.getByText("2")).toBeInTheDocument();
    expect(screen.getByText("Critical")).toBeInTheDocument();
    expect(screen.getByText("Informative")).toBeInTheDocument();
    expect(screen.queryByText(/No findings yet/)).not.toBeInTheDocument();
  });
});
