import { useState } from "react";

import type { Person, Report, ReportMetadata } from "../api/types";
import { useUpdateReportMetadata } from "../hooks/useReports";
import { errorMessage } from "../lib/format";
import { Button } from "./ui/Button";
import { Eyebrow, Panel } from "./ui/Panel";

const inputCls =
  "w-full rounded-lg border border-line bg-panel-2 px-2.5 py-1.5 text-[13px] text-fg transition-colors placeholder:text-faint focus:border-iris/60";
const labelCls = "block font-mono text-[10px] font-medium uppercase tracking-[0.14em] text-faint";

function emptyMetadata(): ReportMetadata {
  return { product: "", report_date: "", author: "", people: [] };
}

function ReadField({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className={labelCls}>{label}</div>
      <p className="mt-0.5 text-[13px] text-fg">
        {value.trim() ? value : <span className="text-faint">—</span>}
      </p>
    </div>
  );
}

/**
 * The report's document metadata (product / date / author / people + roles) and
 * its content hash. LLM-extracted at ingest and operator-editable (#133) — the
 * model is best-effort, so the operator fills in or corrects here.
 */
export function ReportMetadataPanel({ report }: { report: Report }) {
  const update = useUpdateReportMetadata(report.id);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<ReportMetadata>(report.metadata ?? emptyMetadata());
  const meta = report.metadata;

  function startEdit() {
    setDraft(report.metadata ?? emptyMetadata());
    setEditing(true);
  }

  function setField(key: "product" | "report_date" | "author", value: string) {
    setDraft((current) => ({ ...current, [key]: value }));
  }

  function setPerson(index: number, key: keyof Person, value: string) {
    setDraft((current) => ({
      ...current,
      people: current.people.map((person, i) => (i === index ? { ...person, [key]: value } : person)),
    }));
  }

  function addPerson() {
    setDraft((current) => ({ ...current, people: [...current.people, { name: "", role: "" }] }));
  }

  function removePerson(index: number) {
    setDraft((current) => ({
      ...current,
      people: current.people.filter((_, i) => i !== index),
    }));
  }

  function save() {
    update.mutate(draft, { onSuccess: () => setEditing(false) });
  }

  return (
    <Panel className="p-5">
      <div className="flex items-center justify-between">
        <Eyebrow>Document</Eyebrow>
        {!editing && (
          <Button variant="ghost" className="px-2.5 py-1 text-[12px]" onClick={startEdit}>
            Edit
          </Button>
        )}
      </div>

      {editing ? (
        <div className="mt-4 max-w-lg space-y-3">
          <label className={labelCls}>
            Product / target
            <input
              className={`${inputCls} mt-1`}
              value={draft.product}
              onChange={(event) => {
                setField("product", event.target.value);
              }}
            />
          </label>
          <label className={labelCls}>
            Report date
            <input
              className={`${inputCls} mt-1`}
              value={draft.report_date}
              placeholder="e.g. 2026-07-19"
              onChange={(event) => {
                setField("report_date", event.target.value);
              }}
            />
          </label>
          <label className={labelCls}>
            Author
            <input
              className={`${inputCls} mt-1`}
              value={draft.author}
              onChange={(event) => {
                setField("author", event.target.value);
              }}
            />
          </label>

          <div>
            <div className={labelCls}>People involved</div>
            <div className="mt-1 space-y-2">
              {draft.people.map((person, index) => (
                <div key={index} className="flex gap-2">
                  <input
                    className={inputCls}
                    placeholder="Name"
                    aria-label={`Person ${String(index + 1)} name`}
                    value={person.name}
                    onChange={(event) => {
                      setPerson(index, "name", event.target.value);
                    }}
                  />
                  <input
                    className={inputCls}
                    placeholder="Role"
                    aria-label={`Person ${String(index + 1)} role`}
                    value={person.role}
                    onChange={(event) => {
                      setPerson(index, "role", event.target.value);
                    }}
                  />
                  <Button
                    variant="ghost"
                    className="px-2 py-1 text-[12px]"
                    aria-label="Remove person"
                    onClick={() => {
                      removePerson(index);
                    }}
                  >
                    ✕
                  </Button>
                </div>
              ))}
              <Button variant="ghost" className="px-2.5 py-1 text-[12px]" onClick={addPerson}>
                + Add person
              </Button>
            </div>
          </div>

          <div className="flex gap-2 pt-1">
            <Button
              variant="ghost"
              onClick={() => {
                setEditing(false);
              }}
              disabled={update.isPending}
            >
              Cancel
            </Button>
            <Button onClick={save} disabled={update.isPending}>
              {update.isPending ? "Saving…" : "Save"}
            </Button>
          </div>
          {update.isError && (
            <p role="alert" className="text-[13px] text-danger-fg">
              {errorMessage(update.error)}
            </p>
          )}
        </div>
      ) : (
        <div className="mt-4 grid gap-4 sm:grid-cols-2">
          <ReadField label="Product / target" value={meta?.product ?? ""} />
          <ReadField label="Report date" value={meta?.report_date ?? ""} />
          <ReadField label="Author" value={meta?.author ?? ""} />
          <div>
            <div className={labelCls}>Document hash</div>
            <p className="mt-0.5 break-all font-mono text-[11px] text-dim">
              {report.content_hash ?? <span className="text-faint">—</span>}
            </p>
          </div>
          <div className="sm:col-span-2">
            <div className={labelCls}>People involved</div>
            {meta && meta.people.length > 0 ? (
              <ul className="mt-1 space-y-0.5">
                {meta.people.map((person, index) => (
                  <li key={index} className="text-[13px] text-fg">
                    {person.name} <span className="text-faint">— {person.role}</span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="mt-0.5 text-[13px] text-faint">—</p>
            )}
          </div>
        </div>
      )}
    </Panel>
  );
}
