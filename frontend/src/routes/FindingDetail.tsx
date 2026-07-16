import { useState, type ReactNode } from "react";

import { Link, useParams } from "react-router-dom";

import { InstructionsField } from "../components/InstructionsField";
import { PipelineTrack } from "../components/PipelineTrack";
import { PlanEditor } from "../components/PlanEditor";
import { PlanHistory } from "../components/PlanHistory";
import { SeverityBadge } from "../components/SeverityBadge";
import { Spinner } from "../components/Spinner";
import { VerdictCard } from "../components/VerdictCard";
import { Button } from "../components/ui/Button";
import { Eyebrow, Panel, PanelHeader } from "../components/ui/Panel";
import { useFindings } from "../hooks/useFindings";
import { useGeneratePlan, usePlans } from "../hooks/usePlans";
import { useVerdicts } from "../hooks/useVerdicts";
import { errorMessage } from "../lib/format";
import { currentPlan } from "../lib/selectors";

function DetailBlock({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <Eyebrow>{label}</Eyebrow>
      <div className="mt-1.5 text-sm leading-relaxed text-dim">{children}</div>
    </div>
  );
}

/**
 * Shown when there is no live plan to edit — either none has been generated yet
 * or the last attempt `failed`. Kicks off async generation (ADR-0022); the POST
 * returns at once and the page polls until the reserved version settles.
 */
function PlanStartPanel({
  findingId,
  failedError,
}: {
  findingId: number;
  failedError: string | null;
}) {
  const generate = useGeneratePlan(findingId);
  const [instructions, setInstructions] = useState("");
  return (
    <Panel>
      <PanelHeader eyebrow="Retest plan" />
      <div className="space-y-3 p-4">
        {failedError !== null ? (
          <p className="text-sm text-danger-fg">Plan generation failed: {failedError}</p>
        ) : (
          <p className="text-sm text-dim">
            No plan yet. Generate one to propose gated retest actions.
          </p>
        )}
        <InstructionsField
          value={instructions}
          onChange={setInstructions}
          disabled={generate.isPending}
        />
        <Button
          disabled={generate.isPending}
          onClick={() => {
            generate.mutate(instructions);
          }}
        >
          {generate.isPending
            ? "Generating…"
            : failedError !== null
              ? "Try again"
              : "Generate plan"}
        </Button>
        {generate.isError && (
          <p role="alert" className="text-sm text-danger-fg">
            {errorMessage(generate.error)}
          </p>
        )}
      </div>
    </Panel>
  );
}

/** In-flight generation: the background task is proposing actions (ADR-0022). */
function PlanGeneratingPanel() {
  return (
    <Panel>
      <PanelHeader eyebrow="Retest plan" />
      <div className="p-4">
        <Spinner label="Generating retest plan…" />
        <p className="mt-2 text-sm text-faint">
          The model is proposing gated retest actions. This runs in the background — you can
          leave this page and the plan will be here when you return.
        </p>
      </div>
    </Panel>
  );
}

export function FindingDetail() {
  const { id } = useParams();
  const findingId = Number(id);

  // No single-finding endpoint exists in the contract, so scan the full list.
  const findings = useFindings();
  const plans = usePlans(findingId);
  const verdicts = useVerdicts();

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
  const current = currentPlan(planList);
  // "Planned" means a plan actually reached the proposed stage — an in-flight
  // `generating` or a `failed` attempt has not, so the pipeline still shows
  // `plan` as the current action.
  const hasPlan = planList.some(
    (plan) => plan.status !== "generating" && plan.status !== "failed",
  );
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
            planned={hasPlan}
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
      ) : current?.status === "generating" ? (
        <PlanGeneratingPanel />
      ) : current && (current.status === "proposed" || current.status === "approved") ? (
        <PlanEditor findingId={findingId} plan={current} />
      ) : (
        <PlanStartPanel
          findingId={findingId}
          failedError={current?.status === "failed" ? current.error : null}
        />
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
