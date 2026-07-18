import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { Verdict } from "../api/types";
import { EvidenceView } from "./EvidenceView";

const verdict: Verdict = {
  id: 3,
  finding_id: 42,
  probe_kind: "sqli_login_bypass",
  plan_version: 2,
  status: "still_open",
  reason_code: "AUTH_BYPASS_TOKEN_RETURNED",
  rationale: "Login bypass succeeded and returned a JWT.",
  matched_indicators: ["authentication", "token"],
  source: "batch",
  session_id: null,
  actor: "executor",
  evidence: {
    request_method: "POST",
    request_url: "http://lab.local/rest/user/login",
    request_body: '{"email":"\' OR 1=1--"}',
    response_status: 200,
    response_headers: { "content-type": "application/json" },
    response_body_excerpt: '{"authentication":{"token":"eyJ..."}}',
    elapsed_ms: 123,
  },
};

describe("EvidenceView", () => {
  it("renders the verdict's request and response evidence", () => {
    render(<EvidenceView verdict={verdict} />);

    expect(
      screen.getByText("POST http://lab.local/rest/user/login"),
    ).toBeInTheDocument();
    expect(screen.getByText("200")).toBeInTheDocument();
    expect(screen.getByText("123 ms")).toBeInTheDocument();
    expect(
      screen.getByText('{"authentication":{"token":"eyJ..."}}'),
    ).toBeInTheDocument();
    expect(screen.getByText("authentication, token")).toBeInTheDocument();
  });

  it("shows a transcript note instead of a drill-down for an agentic verdict", () => {
    const agentic: Verdict = {
      ...verdict,
      source: "agentic",
      session_id: 9,
      actor: "agent",
      evidence: null,
    };
    render(<EvidenceView verdict={agentic} />);

    expect(screen.getByText(/agentic retest session/i)).toBeInTheDocument();
    expect(screen.queryByText(/http:\/\/lab\.local/)).not.toBeInTheDocument();
  });
});
