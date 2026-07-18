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

  it("renders the explanation + command + output for an agentic verdict", () => {
    const agentic: Verdict = {
      ...verdict,
      source: "agentic",
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
    render(<EvidenceView verdict={agentic} />);

    expect(screen.getByText(/login bypass still returns a token/)).toBeInTheDocument();
    expect(screen.getByText("curl -s http://lab.local/rest/user/login")).toBeInTheDocument();
    expect(screen.getByText('{"authentication":{"token":"eyJ..."}}')).toBeInTheDocument();
  });
});
