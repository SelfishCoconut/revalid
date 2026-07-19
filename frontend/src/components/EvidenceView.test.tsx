import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { Verdict } from "../api/types";
import { EvidenceView } from "./EvidenceView";

const verdict: Verdict = {
  id: 3,
  finding_id: 42,
  status: "still_open",
  reason_code: "AUTH_BYPASS_TOKEN_RETURNED",
  rationale: "Login bypass succeeded and returned a JWT.",
  matched_indicators: ["authentication", "token"],
  session_id: 9,
  actor: "agent",
  evidence: {
    explanation: "login bypass still returns a token",
    command: "curl -s http://lab.local/rest/user/login",
    output: '{"authentication":{"token":"eyJ..."}}',
    exit_code: 0,
    elapsed_ms: 42,
  },
};

describe("EvidenceView", () => {
  it("renders the explanation + command + output for an agentic verdict", () => {
    render(<EvidenceView verdict={verdict} />);

    expect(screen.getByText(/login bypass still returns a token/)).toBeInTheDocument();
    expect(screen.getByText("curl -s http://lab.local/rest/user/login")).toBeInTheDocument();
    expect(screen.getByText('{"authentication":{"token":"eyJ..."}}')).toBeInTheDocument();
    expect(screen.getByText("0")).toBeInTheDocument();
    expect(screen.getByText("42 ms")).toBeInTheDocument();
  });

  it("renders nothing when the verdict has no evidence", () => {
    const { container } = render(<EvidenceView verdict={{ ...verdict, evidence: null }} />);
    expect(container).toBeEmptyDOMElement();
  });
});
