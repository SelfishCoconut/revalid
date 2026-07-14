import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { Button } from "./Button";

describe("Button", () => {
  it("defaults to type=button and the accent variant", () => {
    render(<Button>Run retest</Button>);
    const button = screen.getByRole("button", { name: "Run retest" });
    expect(button).toHaveAttribute("type", "button");
    expect(button).toHaveClass("bg-iris");
  });

  it("applies the requested variant", () => {
    render(<Button variant="positive">Approve</Button>);
    expect(screen.getByRole("button", { name: "Approve" })).toHaveClass("bg-ok");
  });

  it("merges a one-off className onto the shared recipe", () => {
    render(<Button className="ml-auto">Go</Button>);
    const button = screen.getByRole("button", { name: "Go" });
    expect(button).toHaveClass("ml-auto");
    expect(button).toHaveClass("rounded-lg");
  });

  it("fires onClick and honours disabled", async () => {
    const onClick = vi.fn();
    const { rerender } = render(<Button onClick={onClick}>Click</Button>);
    await userEvent.click(screen.getByRole("button", { name: "Click" }));
    expect(onClick).toHaveBeenCalledTimes(1);

    rerender(
      <Button onClick={onClick} disabled>
        Click
      </Button>,
    );
    await userEvent.click(screen.getByRole("button", { name: "Click" }));
    expect(onClick).toHaveBeenCalledTimes(1);
  });
});
