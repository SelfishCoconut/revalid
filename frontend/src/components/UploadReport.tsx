import { useRef, useState } from "react";

import { Link } from "react-router-dom";

import { DuplicateReportError } from "../api/client";
import type { DuplicateReport } from "../api/types";
import { useUploadReport } from "../hooks/useReports";
import { formatDate, useDateFormat } from "../lib/dateFormat";
import { errorMessage } from "../lib/format";
import { Button } from "./ui/Button";
import { Eyebrow, Panel } from "./ui/Panel";
import { StatusBadge } from "./StatusBadge";

/**
 * PDF upload control (button + drag-and-drop). POSTs to `/api/reports` and
 * surfaces the freshly-created report, which starts life in `extracting`. If the
 * bytes match an existing report the backend replies 409; we then show a warning
 * with the matches and let the operator cancel or upload anyway (#134).
 */
export function UploadReport() {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [pendingFile, setPendingFile] = useState<File | null>(null);
  const [duplicates, setDuplicates] = useState<DuplicateReport[] | null>(null);
  const dateFormat = useDateFormat();
  const upload = useUploadReport();

  function submit(file: File | undefined, force = false) {
    if (!file) return;
    setDuplicates(null);
    upload.mutate(
      { file, force },
      {
        onError: (error) => {
          if (error instanceof DuplicateReportError) {
            setPendingFile(file);
            setDuplicates(error.duplicates);
          }
        },
      },
    );
  }

  function cancelDuplicate() {
    setDuplicates(null);
    setPendingFile(null);
    upload.reset();
  }

  const genericError = upload.isError && !(upload.error instanceof DuplicateReportError);

  return (
    <Panel className="p-4">
      <div className="mb-3 flex items-center justify-between">
        <Eyebrow>Load report</Eyebrow>
        <span className="font-mono text-[11px] text-faint">PDF</span>
      </div>

      <div
        onDragOver={(event) => {
          event.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => {
          setDragging(false);
        }}
        onDrop={(event) => {
          event.preventDefault();
          setDragging(false);
          submit(event.dataTransfer.files[0]);
        }}
        className={`flex flex-col items-center justify-center gap-3 rounded-lg border border-dashed px-6 py-9 text-center transition-colors ${
          dragging
            ? "border-iris/70 bg-iris/8"
            : "border-line-2 bg-panel-2/40 hover:border-line-2/80"
        }`}
      >
        <svg
          width="30"
          height="30"
          viewBox="0 0 24 24"
          fill="none"
          aria-hidden="true"
          className="text-faint"
        >
          <path
            d="M12 15V4m0 0 4 4m-4-4-4 4"
            stroke="currentColor"
            strokeWidth="1.6"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
          <path
            d="M4 15v3a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-3"
            stroke="currentColor"
            strokeWidth="1.6"
            strokeLinecap="round"
          />
        </svg>
        <p className="text-sm text-dim">Drag a pentest PDF here, or</p>
        <Button onClick={() => inputRef.current?.click()} disabled={upload.isPending}>
          {upload.isPending ? "Uploading…" : "Choose PDF"}
        </Button>
        <input
          ref={inputRef}
          type="file"
          accept="application/pdf,.pdf"
          aria-label="Report PDF"
          className="hidden"
          onChange={(event) => {
            submit(event.target.files?.[0]);
            event.target.value = "";
          }}
        />
      </div>

      {duplicates && (
        <div role="alert" className="mt-3 rounded-lg border border-warn/40 bg-warn/10 p-3">
          <p className="text-sm font-medium text-warn-fg">This report was already uploaded.</p>
          <ul className="mt-2 space-y-1">
            {duplicates.map((dupe) => (
              <li key={dupe.id} className="text-[13px]">
                <Link
                  to={`/reports/${String(dupe.id)}`}
                  className="font-mono text-iris-fg hover:underline"
                >
                  {dupe.filename}
                </Link>
                <span className="text-faint"> · {formatDate(dupe.created_at, dateFormat)}</span>
              </li>
            ))}
          </ul>
          <div className="mt-3 flex flex-wrap gap-2">
            <Button variant="ghost" onClick={cancelDuplicate}>
              Cancel
            </Button>
            <Button
              variant="danger"
              onClick={() => {
                submit(pendingFile ?? undefined, true);
              }}
            >
              Upload anyway
            </Button>
          </div>
        </div>
      )}

      {genericError && (
        <p role="alert" className="mt-3 text-sm text-danger-fg">
          Upload failed: {errorMessage(upload.error)}
        </p>
      )}

      {upload.isSuccess && !duplicates && (
        <p className="mt-3 flex flex-wrap items-center gap-2 text-sm text-dim">
          <span>
            Uploaded <span className="font-mono font-medium text-fg">{upload.data.filename}</span>
          </span>
          <StatusBadge status={upload.data.status} />
        </p>
      )}
    </Panel>
  );
}
