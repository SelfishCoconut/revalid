import { useRef, useState } from "react";

import { errorMessage } from "../lib/format";
import { useUploadReport } from "../hooks/useReports";
import { Button } from "./ui/Button";
import { Eyebrow, Panel } from "./ui/Panel";
import { StatusBadge } from "./StatusBadge";

/**
 * PDF upload control (button + drag-and-drop). POSTs to `/api/reports` and
 * surfaces the freshly-created report, which starts life in `extracting`.
 */
export function UploadReport() {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const upload = useUploadReport();

  function submit(file: File | undefined) {
    if (file) upload.mutate(file);
  }

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
        <p className="text-sm text-dim">
          Drag a pentest PDF here, or
        </p>
        <Button
          onClick={() => inputRef.current?.click()}
          disabled={upload.isPending}
        >
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

      {upload.isError && (
        <p role="alert" className="mt-3 text-sm text-danger-fg">
          Upload failed: {errorMessage(upload.error)}
        </p>
      )}

      {upload.isSuccess && (
        <p className="mt-3 flex flex-wrap items-center gap-2 text-sm text-dim">
          <span>
            Uploaded{" "}
            <span className="font-mono font-medium text-fg">{upload.data.filename}</span>
          </span>
          <StatusBadge status={upload.data.status} />
        </p>
      )}
    </Panel>
  );
}
