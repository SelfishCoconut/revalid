import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { CvssCode, MitreMapping } from "../api/types";
import { FindingTaxonomy } from "./FindingTaxonomy";

const NO_CVSS: CvssCode = { vector: "", base_score: null, inferred: false };
const NO_MITRE: MitreMapping = { techniques: [], inferred: false };

describe("FindingTaxonomy", () => {
  it("shows a stated CVSS score and vector without an inferred badge", () => {
    render(
      <FindingTaxonomy
        cvss={{
          vector: "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H",
          base_score: 9.8,
          inferred: false,
        }}
        mitre={NO_MITRE}
      />,
    );
    expect(screen.getByText("9.8")).toBeInTheDocument();
    expect(screen.getByText(/CVSS:3.1\/AV:N/)).toBeInTheDocument();
    expect(screen.queryByText("inferred")).not.toBeInTheDocument();
  });

  it("marks a derived CVSS as inferred", () => {
    // Provenance is the whole point: an estimate must never read as a quoted fact.
    render(
      <FindingTaxonomy
        cvss={{ vector: "CVSS:3.1/AV:N", base_score: 7.5, inferred: true }}
        mitre={NO_MITRE}
      />,
    );
    expect(screen.getByText("inferred")).toBeInTheDocument();
  });

  it("lists ATT&CK techniques and marks a derived mapping", () => {
    render(
      <FindingTaxonomy
        cvss={NO_CVSS}
        mitre={{ techniques: ["T1190", "T1110"], inferred: true }}
      />,
    );
    expect(screen.getByText("T1190")).toBeInTheDocument();
    expect(screen.getByText("T1110")).toBeInTheDocument();
    expect(screen.getByText("inferred")).toBeInTheDocument();
  });

  it("renders an absent score as an em dash, never as zero", () => {
    // A fabricated 0.0 would read as "harmless" for something merely unscored.
    render(<FindingTaxonomy cvss={NO_CVSS} mitre={NO_MITRE} />);
    expect(screen.getAllByText("—")).toHaveLength(2);
    expect(screen.queryByText("0.0")).not.toBeInTheDocument();
    // Nothing was derived, so nothing claims to be.
    expect(screen.queryByText("inferred")).not.toBeInTheDocument();
  });

  it("shows a score with no vector, and a vector with no score", () => {
    const { unmount } = render(
      <FindingTaxonomy cvss={{ vector: "", base_score: 4.2, inferred: false }} mitre={NO_MITRE} />,
    );
    expect(screen.getByText("4.2")).toBeInTheDocument();
    unmount();

    render(
      <FindingTaxonomy cvss={{ vector: "CVSS:3.1/AV:L", base_score: null, inferred: false }} mitre={NO_MITRE} />,
    );
    expect(screen.getByText("CVSS:3.1/AV:L")).toBeInTheDocument();
  });
});
