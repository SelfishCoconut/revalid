import type { ReactNode } from "react";

import { Link, useParams } from "react-router-dom";

import type { Plan } from "../api/types";
import { PipelineTrack } from "../components/PipelineTrack";
import { PlanEditor } from "../components/PlanEditor";
import { PlanHistory } from "../components/PlanHistory";
import { SeverityBadge } from "../components/SeverityBadge";
import { Spinner } from "../components/Spinner";
import { VerdictCard } from "../components/VerdictCard";
import { Eyebrow, Panel, PanelHeader } from "../components/ui/Panel";
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
      <Eyebrow>{label}</Eyebrow>
      <div className="mt-1.5 text-sm leading-relaxed text-dim">{children}</div>
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
      <p role="alert" className="text-sm text-danger-fg">
        {errorMessage(findings.error)}
      </p>
    );
  }

  const finding = findings.data.find((item) => item.id === findingId);
  if (!finding) {
    return <p className="text-sm text-faint">Finding not found.</p>;
  }

  const planList = plans.data ?? [];
  const current = activePlan(planList);
  const findingVerdicts = (verdicts.data ?? [])
    .filter((verdict) => verdict.finding_id === findingId)
    .sort((a, b) => b.id - a.id);

  const backLink =
    finding.report_id != null ? `/reports/${String(finding.report_id)}` : "/";

  return (
    <div className="rev-rise space-y-6">
      <Link
        to={backLink}
        className="inline-flex items-center gap-1.5 font-mono text-[12px] text-faint transition-colors hover:text-dim"
      >
        <span aria-hidden="true">←</span>
        Back to report
      </Link>

      <Panel className="overflow-hidden">
        <div className="p-5">
          <div className="flex flex-wrap items-center gap-3">
            <SeverityBadge severity={finding.severity} />
            <h1 className="text-xl font-semibold tracking-tight text-fg">
              {finding.title}
            </h1>
          </div>
          <div className="mt-5 grid gap-5 sm:grid-cols-2">
            <DetailBlock label="Description">{finding.description}</DetailBlock>
            <DetailBlock label="Impact">{finding.impact}</DetailBlock>
            <DetailBlock label="Attack vector">{finding.attack_vector}</DetailBlock>
            <DetailBlock label="Affected endpoints">
              {finding.affected_endpoints.length > 0 ? (
                <ul className="space-y-1 font-mono text-[13px] text-dim">
                  {finding.affected_endpoints.map((endpoint) => (
                    <li key={endpoint} className="break-all">
                      {endpoint}
                    </li>
                  ))}
                </ul>
              ) : (
                "—"
              )}
            </DetailBlock>
          </div>
          {finding.reproduction_steps.length > 0 && (
            <div className="mt-5">
              <DetailBlock label="Reproduction steps">
                <ol className="mt-1 list-inside list-decimal space-y-1 marker:font-mono marker:text-faint">
                  {finding.reproduction_steps.map((step, index) => (
                    <li key={index}>{step}</li>
                  ))}
                </ol>
              </DetailBlock>
            </div>
          )}
        </div>
        <div className="border-t border-line bg-panel-2/30 px-4 py-4">
          <PipelineTrack
            planned={planList.length > 0}
            approved={
              planList.some((plan) => plan.status === "approved") ||
              findingVerdicts.length > 0
            }
            retested={findingVerdicts.length > 0}
            verdict={findingVerdicts[0]?.status}
          />
        </div>
      </Panel>

      {plans.isPending ? (
        <Panel className="p-4">
          <Spinner label="Loading plan" />
        </Panel>
      ) : current ? (
        <PlanEditor findingId={findingId} plan={current} />
      ) : (
        <Panel>
          <PanelHeader eyebrow="Retest plan" />
          <div className="p-4">
            <p className="text-sm text-dim">
              No plan yet. Generate one to propose gated retest actions.
            </p>
            <button
              type="button"
              disabled={generate.isPending}
              onClick={() => {
                generate.mutate();
              }}
              className="mt-3 rounded-lg bg-iris px-3.5 py-1.5 font-mono text-[13px] font-semibold text-onaccent transition-colors hover:bg-iris-bright disabled:opacity-45"
            >
              {generate.isPending ? "Generating…" : "Generate plan"}
            </button>
            {generate.isError && (
              <p role="alert" className="mt-2 text-sm text-danger-fg">
                {errorMessage(generate.error)}
              </p>
            )}
          </div>
        </Panel>
      )}

      <Panel>
        <PanelHeader
          eyebrow="Verdicts"
          aside={
            <span className="font-mono text-[11px] text-faint">
              {findingVerdicts.length} recorded
            </span>
          }
        />
        <div className="p-4">
          {findingVerdicts.length === 0 ? (
            <p className="text-sm text-faint">
              No verdicts yet. Approve the plan and run the retest.
            </p>
          ) : (
            <div className="space-y-3">
              {findingVerdicts.map((verdict) => (
                <VerdictCard key={verdict.id} verdict={verdict} />
              ))}
            </div>
          )}
        </div>
      </Panel>

      <Panel>
        <PanelHeader eyebrow="Plan history" />
        <PlanHistory plans={planList} />
      </Panel>
    </div>
  );
}
