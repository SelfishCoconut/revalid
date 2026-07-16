import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { RetestTerminal } from "./RetestTerminal";

describe("RetestTerminal", () => {
  it("renders its host container", () => {
    const { getByTestId } = render(<RetestTerminal lines={[]} />);
    expect(getByTestId("retest-terminal")).toBeInTheDocument();
  });

  it("does not throw as lines are appended across renders", () => {
    const { rerender } = render(<RetestTerminal lines={["$ id"]} />);
    expect(() => {
      rerender(<RetestTerminal lines={["$ id", "uid=0(root)"]} />);
    }).not.toThrow();
  });
});
