import type { ReactNode } from "react";

import { Link, useParams } from "react-router-dom";

import type { Plan } from "../api/types";
import { PlanEditor } from "../components/PlanEditor";
import { PlanHistory } from "../components/PlanHistory";
import { SeverityBadge } from "../components/SeverityBadge";
import { Spinner } from "../components/Spinner";
import { VerdictCard } from "../components/VerdictCard";
import { useFindings } from "../hooks/useFindings";
import { useGeneratePlan, usePlans } from "../hooks/usePlans";
import { useVerdicts } from "../hooks/useVerdicts";
import { errorMessage } from "../lib/format";

/** The plan the workflow acts on: newest version that is proposed or approved. */
function activePlan(plans: Plan[]): Plan | undefined {
  return plans
    .filter((plan) => plan.status === "proposed" || plan.status === "approved")
    .reduce<Plan | undefined>(
      (latest, plan) => (!latest || plan.version > latest.version ? plan : latest),
      undefined,
    );
}

function DetailBlock({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">
        {label}
      </h3>
      <div className="mt-1 text-sm text-slate-800">{children}</div>
    </div>
  );
}

export function FindingDetail() {
  const { id } = useParams();
  const findingId = Number(id);

  // No single-finding endpoint exists in the contract, so scan the full list.
  const findings = useFindings();
  const plans = usePlans(findingId);
  const verdicts = useVerdicts();
  const generate = useGeneratePlan(findingId);

  if (findings.isPending) {
    return <Spinner label="Loading finding" />;
  }
  if (findings.isError) {
    return (
      <p role="alert" className="text-sm text-red-700">
        {errorMessage(findings.error)}
      </p>
    );
  }

  const finding = findings.data.find((item) => item.id === findingId);
  if (!finding) {
    return <p className="text-sm text-slate-500">Finding not found.</p>;
  }

  const planList = plans.data ?? [];
  const current = activePlan(planList);
  const findingVerdicts = (verdicts.data ?? [])
    .filter((verdict) => verdict.finding_id === findingId)
    .sort((a, b) => b.id - a.id);

  const backLink =
    finding.report_id != null ? `/reports/${String(finding.report_id)}` : "/";

  return (
    <div className="space-y-6">
      <Link to={backLink} className="text-sm text-sky-700 hover:underline">
        ← Back to report
      </Link>

      <header className="rounded-lg border border-slate-200 bg-white p-4">
        <div className="flex flex-wrap items-center gap-3">
          <SeverityBadge severity={finding.severity} />
          <h1 className="text-lg font-semibold text-slate-800">{finding.title}</h1>
        </div>
        <div className="mt-4 grid gap-4 sm:grid-cols-2">
          <DetailBlock label="Description">{finding.description}</DetailBlock>
          <DetailBlock label="Impact">{finding.impact}</DetailBlock>
          <DetailBlock label="Attack vector">{finding.attack_vector}</DetailBlock>
          <DetailBlock label="Affected endpoints">
            {finding.affected_endpoints.length > 0 ? (
              <ul className="list-inside list-disc font-mono">
                {finding.affected_endpoints.map((endpoint) => (
                  <li key={endpoint}>{endpoint}</li>
                ))}
              </ul>
            ) : (
              "—"
            )}
          </DetailBlock>
        </div>
        {finding.reproduction_steps.length > 0 && (
          <div className="mt-4">
            <DetailBlock label="Reproduction steps">
              <ol className="list-inside list-decimal space-y-1">
                {finding.reproduction_steps.map((step, index) => (
                  <li key={index}>{step}</li>
                ))}
              </ol>
            </DetailBlock>
          </div>
        )}
      </header>

      <section className="space-y-2">
        <h2 className="text-base font-semibold text-slate-800">Plan</h2>
        {plans.isPending ? (
          <Spinner label="Loading plan" />
        ) : current ? (
          <PlanEditor findingId={findingId} plan={current} />
        ) : (
          <div className="rounded-lg border border-slate-200 bg-white p-4">
            <p className="text-sm text-slate-600">No proposed or approved plan.</p>
            <button
              type="button"
              disabled={generate.isPending}
              onClick={() => {
                generate.mutate();
              }}
              className="mt-3 rounded-md bg-slate-800 px-3 py-1.5 text-sm font-medium text-white hover:bg-slate-700 disabled:opacity-50"
            >
              {generate.isPending ? "Generating…" : "Generate plan"}
            </button>
            {generate.isError && (
              <p role="alert" className="mt-2 text-sm text-red-700">
                {errorMessage(generate.error)}
              </p>
            )}
          </div>
        )}
      </section>

      <section className="space-y-2">
        <h2 className="text-base font-semibold text-slate-800">Verdicts</h2>
        {findingVerdicts.length === 0 ? (
          <p className="text-sm text-slate-500">
            No verdicts yet — approve a plan and run the retest.
          </p>
        ) : (
          <div className="space-y-3">
            {findingVerdicts.map((verdict) => (
              <VerdictCard key={verdict.id} verdict={verdict} />
            ))}
          </div>
        )}
      </section>

      <section className="space-y-2">
        <h2 className="text-base font-semibold text-slate-800">Plan history</h2>
        <div className="rounded-lg border border-slate-200 bg-white p-4">
          <PlanHistory plans={planList} />
        </div>
      </section>
    </div>
  );
}
