import { useState } from "react";

import { Link } from "react-router-dom";

import type { Finding, FindingEdit, Severity } from "../../api/types";
import { useFindingStage } from "../../hooks/useFindingStage";
import { NotesThread } from "../../components/NotesThread";
import { Spinner } from "../../components/Spinner";
import { Button } from "../../components/ui/Button";
import { FindingTaxonomy } from "../../components/FindingTaxonomy";
import { Panel, PanelHeader } from "../../components/ui/Panel";
import { useEditFinding, useFindingVersions } from "../../hooks/useFindingRevision";
import { formatDate, useDateFormat } from "../../lib/dateFormat";
import { errorMessage } from "../../lib/format";

const SEVERITIES: Severity[] = ["info", "low", "medium", "high", "critical"];

const inputClass =
  "mt-1.5 w-full rounded-lg border border-line bg-panel-2 px-3 py-2 text-sm text-fg transition-colors placeholder:text-faint focus:border-iris/60 disabled:opacity-55";
const fieldLabel = "font-mono text-[10px] font-medium uppercase tracking-[0.14em] text-faint";

function toLines(values: string[]): string {
  return values.join("\n");
}

function fromLines(text: string): string[] {
  return text
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
}

/** ATT&CK technique IDs are entered comma-separated; blanks are dropped. */
function fromCsv(text: string): string[] {
  return text
    .split(",")
    .map((id) => id.trim())
    .filter(Boolean);
}

/**
 * Editable form over a finding's current content. Saving appends a new immutable
 * version (FR-16) — it never overwrites. Keyed by the finding version upstream so
 * it re-seeds once the edit lands.
 */
function FindingEditor({ finding }: { finding: Finding }) {
  const edit = useEditFinding(finding.id);
  const [form, setForm] = useState({
    title: finding.title,
    severity: finding.severity,
    description: finding.description,
    impact: finding.impact,
    attack_vector: finding.attack_vector,
    endpoints: toLines(finding.affected_endpoints),
    steps: toLines(finding.reproduction_steps),
    cvssVector: finding.cvss.vector,
    cvssScore: finding.cvss.base_score == null ? "" : String(finding.cvss.base_score),
    mitre: finding.mitre.techniques.join(", "),
    reason: "",
  });

  function save() {
    const body: FindingEdit = {
      title: form.title.trim(),
      severity: form.severity,
      description: form.description,
      impact: form.impact,
      attack_vector: form.attack_vector,
      affected_endpoints: fromLines(form.endpoints),
      reproduction_steps: fromLines(form.steps),
      // Always sent, even untouched: the server compares against the current
      // version, so resubmitting an inferred value unchanged keeps it marked
      // inferred rather than laundering it into an author-stated one (FR-19).
      cvss: {
        vector: form.cvssVector.trim(),
        base_score: form.cvssScore.trim() === "" ? null : Number(form.cvssScore),
      },
      mitre: { techniques: fromCsv(form.mitre) },
      reason: form.reason.trim(),
    };
    edit.mutate(body);
  }

  return (
    <Panel>
      <PanelHeader
        eyebrow="Extracted finding"
        aside={
          finding.report_id != null ? (
            <Link
              to={`/reports/${String(finding.report_id)}`}
              className="font-mono text-[11px] text-iris-fg hover:underline"
            >
              View source report →
            </Link>
          ) : undefined
        }
      />
      <div className="space-y-3 p-4">
        <div className="grid gap-3 sm:grid-cols-[1fr_9rem]">
          <label className={fieldLabel}>
            Title
            <input
              aria-label="Finding title"
              value={form.title}
              disabled={edit.isPending}
              onChange={(event) => {
                setForm((f) => ({ ...f, title: event.target.value }));
              }}
              className={inputClass}
            />
          </label>
          <label className={fieldLabel}>
            Severity
            <select
              aria-label="Finding severity"
              value={form.severity}
              disabled={edit.isPending}
              onChange={(event) => {
                setForm((f) => ({ ...f, severity: event.target.value as Severity }));
              }}
              className={inputClass}
            >
              {SEVERITIES.map((sev) => (
                <option key={sev} value={sev}>
                  {sev}
                </option>
              ))}
            </select>
          </label>
        </div>
        <label className={fieldLabel}>
          Description
          <textarea
            aria-label="Finding description"
            rows={6}
            value={form.description}
            disabled={edit.isPending}
            onChange={(event) => {
              setForm((f) => ({ ...f, description: event.target.value }));
            }}
            className={`${inputClass} resize-y`}
          />
        </label>
        <div className="grid gap-3 sm:grid-cols-2">
          <label className={fieldLabel}>
            Impact
            <textarea
              aria-label="Finding impact"
              rows={4}
              value={form.impact}
              disabled={edit.isPending}
              onChange={(event) => {
                setForm((f) => ({ ...f, impact: event.target.value }));
              }}
              className={`${inputClass} resize-y`}
            />
          </label>
          <label className={fieldLabel}>
            Attack vector
            <textarea
              aria-label="Finding attack vector"
              rows={4}
              value={form.attack_vector}
              disabled={edit.isPending}
              onChange={(event) => {
                setForm((f) => ({ ...f, attack_vector: event.target.value }));
              }}
              className={`${inputClass} resize-y`}
            />
          </label>
        </div>
        <label className={fieldLabel}>
          Affected domain(s) — one per line, host is what scopes the retest
          <textarea
            aria-label="Affected domains"
            rows={4}
            value={form.endpoints}
            disabled={edit.isPending}
            onChange={(event) => {
              setForm((f) => ({ ...f, endpoints: event.target.value }));
            }}
            className={`${inputClass} resize-y font-mono`}
          />
        </label>
        <label className={fieldLabel}>
          Reproduction steps (one per line)
          <textarea
            aria-label="Reproduction steps"
            rows={6}
            value={form.steps}
            disabled={edit.isPending}
            onChange={(event) => {
              setForm((f) => ({ ...f, steps: event.target.value }));
            }}
            className={`${inputClass} resize-y`}
          />
        </label>
        {/* Classification is editable here because it is often absent: only the
            LLM extraction path derives it, so anything ingested from a
            structured export or typed in by hand arrives with none (FR-19). */}
        <fieldset className="space-y-3 rounded-lg border border-line bg-panel-2/40 p-3">
          <legend className={`${fieldLabel} px-1`}>Classification</legend>
          <div className="rounded-md bg-panel-2/60 p-2">
            <FindingTaxonomy cvss={finding.cvss} mitre={finding.mitre} />
          </div>
          <div className="grid gap-3 sm:grid-cols-[1fr_7rem]">
            <label className={fieldLabel}>
              CVSS vector
              <input
                aria-label="CVSS vector"
                value={form.cvssVector}
                disabled={edit.isPending}
                placeholder="CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H"
                onChange={(event) => {
                  setForm((f) => ({ ...f, cvssVector: event.target.value }));
                }}
                className={`${inputClass} font-mono text-xs`}
              />
            </label>
            <label className={fieldLabel}>
              Base score
              <input
                aria-label="CVSS base score"
                type="number"
                min={0}
                max={10}
                step={0.1}
                value={form.cvssScore}
                disabled={edit.isPending}
                placeholder="0.0–10.0"
                onChange={(event) => {
                  setForm((f) => ({ ...f, cvssScore: event.target.value }));
                }}
                className={inputClass}
              />
            </label>
          </div>
          <label className={fieldLabel}>
            ATT&amp;CK techniques (comma-separated)
            <input
              aria-label="MITRE ATT&CK techniques"
              value={form.mitre}
              disabled={edit.isPending}
              placeholder="T1190, T1110"
              onChange={(event) => {
                setForm((f) => ({ ...f, mitre: event.target.value }));
              }}
              className={`${inputClass} font-mono`}
            />
          </label>
          <p className="text-[11px] text-faint">
            Values you change here are recorded as yours; the “inferred” tag is dropped.
          </p>
        </fieldset>
        <label className={fieldLabel}>
          Reason for this edit (optional)
          <input
            aria-label="Reason for edit"
            value={form.reason}
            disabled={edit.isPending}
            placeholder="e.g. corrected the affected endpoint"
            onChange={(event) => {
              setForm((f) => ({ ...f, reason: event.target.value }));
            }}
            className={inputClass}
          />
        </label>
        <div className="flex items-center gap-3">
          <Button disabled={edit.isPending || !form.title.trim()} onClick={save}>
            {edit.isPending ? "Saving…" : "Save as new version"}
          </Button>
          {edit.isError && (
            <span role="alert" className="text-sm text-danger-fg">
              {errorMessage(edit.error)}
            </span>
          )}
        </div>
      </div>
    </Panel>
  );
}

/** The finding's amend history — every version kept, extraction = v1 (FR-16). */
function VersionHistory({ findingId }: { findingId: number }) {
  const versions = useFindingVersions(findingId);
  const dateFormat = useDateFormat();
  const rows = [...(versions.data ?? [])].reverse();

  return (
    <Panel>
      <PanelHeader eyebrow="Revision history" />
      <div className="p-4">
        {versions.isPending ? (
          <Spinner label="Loading history" />
        ) : (
          <ul className="space-y-2">
            {rows.map((version) => (
              <li
                key={version.version}
                className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5 rounded-lg border border-line bg-panel-2/40 px-3 py-2"
              >
                <span className="font-mono text-sm font-semibold text-fg">v{version.version}</span>
                <span className="rounded bg-iris/12 px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-[0.14em] text-iris-fg">
                  {version.origin}
                </span>
                <span className="font-mono text-[11px] text-faint">
                  {formatDate(version.created_at, dateFormat)}
                </span>
                {version.reason && <span className="text-[13px] text-dim">— {version.reason}</span>}
              </li>
            ))}
          </ul>
        )}
      </div>
    </Panel>
  );
}

/** Stage 1 — the extracted finding: amend it (versioned) and read its source (FR-16). */
export function ExtractStage() {
  const { finding, findingId } = useFindingStage();

  return (
    <div className="space-y-6">
      <FindingEditor key={finding.version} finding={finding} />
      <VersionHistory findingId={findingId} />
      <NotesThread findingId={findingId} stage="extract" scope="all" />
    </div>
  );
}
