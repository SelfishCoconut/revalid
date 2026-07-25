import { useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import type { ManualReportInput, Severity } from "../api/types";
import { Button } from "../components/ui/Button";
import { Eyebrow, Panel, PanelHeader } from "../components/ui/Panel";
import { useCreateManualReport } from "../hooks/useReports";
import { errorMessage } from "../lib/format";
import { draftsToPayload, emptyFinding, type FindingDraft, SEVERITIES } from "../lib/manualReport";

type Mode = "form" | "json";

const FIELD =
  "w-full rounded-lg border border-line bg-panel-2/40 px-3 py-2 font-mono text-[13px] text-fg placeholder:text-faint focus:border-iris/60 focus:outline-none";

const JSON_PLACEHOLDER = `{
  "label": "My pentest report",
  "findings": [
    {
      "title": "SQL injection in login",
      "severity": "high",
      "description": "…",
      "endpoints": ["https://juice.example.com/#/login"],
      "steps_to_reproduce": "1. …\\n2. …"
    }
  ]
}`;

/**
 * Create a report by hand — the human-entry escape hatch (bypasses LLM
 * ingestion, ADR-0020). A structured form, or a pasted/uploaded JSON payload.
 * On success it navigates to the freshly-created `ready` report.
 */
export function NewReport() {
  const navigate = useNavigate();
  const create = useCreateManualReport();
  const fileRef = useRef<HTMLInputElement>(null);

  const [mode, setMode] = useState<Mode>("form");
  const [label, setLabel] = useState("");
  const [findings, setFindings] = useState<FindingDraft[]>([emptyFinding()]);
  const [jsonText, setJsonText] = useState("");
  const [localError, setLocalError] = useState("");
  // Off by default: this door is the LLM-free path, and staying that way unless
  // asked is the point of the flag (FR-19, issue #233).
  const [enrich, setEnrich] = useState(false);

  function patchFinding(index: number, patch: Partial<FindingDraft>) {
    setFindings((current) => current.map((f, i) => (i === index ? { ...f, ...patch } : f)));
  }

  function submit(payload: ManualReportInput) {
    setLocalError("");
    create.mutate(
      // The toggle wins over anything pasted into the JSON, so what the operator
      // sees checked is what actually runs.
      { ...payload, enrich },
      {
        onSuccess: (report) => {
          navigate(`/reports/${String(report.id)}`);
        },
      },
    );
  }

  function submitForm() {
    const payload = draftsToPayload(label, findings);
    if (!payload.label) {
      setLocalError("Give the report a name.");
      return;
    }
    if (payload.findings.some((f) => !f.title)) {
      setLocalError("Every finding needs a title.");
      return;
    }
    submit(payload);
  }

  function submitJson() {
    let parsed: unknown;
    try {
      parsed = JSON.parse(jsonText);
    } catch (error) {
      setLocalError(`Invalid JSON: ${errorMessage(error)}`);
      return;
    }
    submit(parsed as ManualReportInput);
  }

  function loadFile(file: File | undefined) {
    if (!file) return;
    file
      .text()
      .then((text) => {
        setJsonText(text);
        setLocalError("");
      })
      .catch(() => {
        setLocalError("Could not read that file.");
      });
  }

  const message = localError || (create.isError ? errorMessage(create.error) : "");

  return (
    <div className="rev-rise space-y-6">
      <Panel>
        <PanelHeader
          eyebrow="Create report manually"
          aside={
            <div className="flex rounded-lg border border-line p-0.5">
              {(["form", "json"] as const).map((value) => (
                <button
                  key={value}
                  type="button"
                  onClick={() => {
                    setMode(value);
                    setLocalError("");
                  }}
                  className={`rounded-md px-3 py-1 font-mono text-[11px] font-semibold uppercase tracking-[0.12em] transition-colors ${
                    mode === value ? "bg-iris text-onaccent" : "text-dim hover:text-fg"
                  }`}
                >
                  {value === "form" ? "Form" : "JSON"}
                </button>
              ))}
            </div>
          }
        />

        <div className="space-y-5 px-4 py-4 sm:px-6">
          <p className="text-sm text-dim">
            Enter findings by hand (or paste/upload JSON) to bypass LLM ingestion — useful
            for large reports or small local models. The report lands <span className="text-fg">ready</span>{" "}
            and drives the same plan → approve → retest flow as an extracted one.
          </p>

          <label className="block space-y-1.5">
            <Eyebrow>Report name</Eyebrow>
            <input
              className={FIELD}
              value={label}
              onChange={(event) => {
                setLabel(event.target.value);
              }}
              placeholder="e.g. Acme Corp — Q3 web app pentest"
            />
          </label>

          {mode === "form" ? (
            <div className="space-y-4">
              {findings.map((finding, index) => (
                <fieldset
                  key={index}
                  className="space-y-3 rounded-lg border border-line bg-panel-2/20 p-3.5"
                >
                  <div className="flex items-center justify-between">
                    <Eyebrow>Finding {index + 1}</Eyebrow>
                    {findings.length > 1 && (
                      <button
                        type="button"
                        onClick={() => {
                          setFindings((current) => current.filter((_, i) => i !== index));
                        }}
                        className="font-mono text-[11px] text-faint transition-colors hover:text-danger-fg"
                      >
                        Remove
                      </button>
                    )}
                  </div>

                  <div className="grid gap-3 sm:grid-cols-[1fr_9rem]">
                    <input
                      className={FIELD}
                      value={finding.title}
                      onChange={(event) => {
                        patchFinding(index, { title: event.target.value });
                      }}
                      placeholder="Title (e.g. IDOR: access another user's basket)"
                    />
                    <select
                      className={FIELD}
                      value={finding.severity}
                      onChange={(event) => {
                        patchFinding(index, { severity: event.target.value as Severity });
                      }}
                      aria-label={`Finding ${String(index + 1)} severity`}
                    >
                      {SEVERITIES.map((severity) => (
                        <option key={severity} value={severity}>
                          {severity}
                        </option>
                      ))}
                    </select>
                  </div>

                  <textarea
                    className={FIELD}
                    rows={2}
                    value={finding.description}
                    onChange={(event) => {
                      patchFinding(index, { description: event.target.value });
                    }}
                    placeholder="Description"
                  />

                  <div className="grid gap-3 sm:grid-cols-2">
                    <textarea
                      className={FIELD}
                      rows={3}
                      value={finding.endpoints}
                      onChange={(event) => {
                        patchFinding(index, { endpoints: event.target.value });
                      }}
                      placeholder={"Domain(s), one per line\nhttps://juice.example.com/#/basket"}
                    />
                    <textarea
                      className={FIELD}
                      rows={3}
                      value={finding.steps}
                      onChange={(event) => {
                        patchFinding(index, { steps: event.target.value });
                      }}
                      placeholder={"Steps to reproduce (one per line)\n1. Log in\n2. Increment the id"}
                    />
                  </div>
                </fieldset>
              ))}

              <Button
                variant="ghost"
                onClick={() => {
                  setFindings((current) => [...current, emptyFinding()]);
                }}
              >
                + Add finding
              </Button>
            </div>
          ) : (
            <div className="space-y-3">
              <div className="flex items-center justify-between">
                <Eyebrow>Findings JSON</Eyebrow>
                <Button variant="ghost" onClick={() => fileRef.current?.click()}>
                  Upload .json
                </Button>
                <input
                  ref={fileRef}
                  type="file"
                  accept="application/json,.json"
                  aria-label="Findings JSON file"
                  className="hidden"
                  onChange={(event) => {
                    loadFile(event.target.files?.[0]);
                    event.target.value = "";
                  }}
                />
              </div>
              <textarea
                className={`${FIELD} min-h-[16rem]`}
                value={jsonText}
                onChange={(event) => {
                  setJsonText(event.target.value);
                }}
                placeholder={JSON_PLACEHOLDER}
                spellCheck={false}
              />
              <p className="font-mono text-[11px] text-faint">
                Shape: {"{ label, findings: [{ title, severity, description, endpoints[], steps_to_reproduce }] }"}
              </p>
            </div>
          )}

          {message && (
            <p role="alert" className="text-sm text-danger-fg">
              {message}
            </p>
          )}

          <div className="flex flex-wrap items-center justify-end gap-3 border-t border-line pt-4">
            <label className="mr-auto flex items-start gap-2.5 text-sm">
              <input
                type="checkbox"
                checked={enrich}
                onChange={(event) => {
                  setEnrich(event.target.checked);
                }}
                className="mt-0.5 h-4 w-4 shrink-0 accent-iris"
              />
              <span>
                <span className="text-fg">Infer CVSS &amp; ATT&amp;CK</span>
                <span className="block text-[12px] text-faint">
                  One model call per finding. Off keeps this door LLM-free — instant and free.
                </span>
              </span>
            </label>
            <Button
              variant="ghost"
              onClick={() => {
                navigate("/");
              }}
            >
              Cancel
            </Button>
            <Button
              variant="positive"
              disabled={create.isPending}
              onClick={mode === "form" ? submitForm : submitJson}
            >
              {create.isPending ? "Creating…" : "Create report"}
            </Button>
          </div>
        </div>
      </Panel>
    </div>
  );
}
