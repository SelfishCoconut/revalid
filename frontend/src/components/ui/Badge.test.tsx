import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Badge } from "./Badge";

describe("Badge", () => {
  it("renders the label with the tone's pill classes", () => {
    render(<Badge tone="danger" label="still open" />);
    const badge = screen.getByText("still open");
    expect(badge).toHaveClass("text-danger-fg");
    expect(badge).toHaveClass("font-medium");
  });

  it("upper-cases and letter-spaces the caps emphasis", () => {
    render(<Badge tone="high" label="high" emphasis="caps" />);
    expect(screen.getByText("high")).toHaveClass("uppercase");
  });
});
