import { useRef, useState } from "react";

import { errorMessage } from "../lib/format";
import { useUploadReport } from "../hooks/useReports";
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
    <div className="rounded-lg border border-slate-200 bg-white p-4">
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
        className={`flex flex-col items-center justify-center gap-2 rounded-md border-2 border-dashed px-6 py-8 text-center transition-colors ${
          dragging ? "border-sky-400 bg-sky-50" : "border-slate-300"
        }`}
      >
        <p className="text-sm text-slate-600">Drag a pentest PDF here, or</p>
        <button
          type="button"
          onClick={() => inputRef.current?.click()}
          disabled={upload.isPending}
          className="rounded-md bg-slate-800 px-3 py-1.5 text-sm font-medium text-white hover:bg-slate-700 disabled:opacity-50"
        >
          {upload.isPending ? "Uploading…" : "Choose PDF"}
        </button>
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
        <p role="alert" className="mt-3 text-sm text-red-700">
          Upload failed: {errorMessage(upload.error)}
        </p>
      )}

      {upload.isSuccess && (
        <p className="mt-3 flex items-center gap-2 text-sm text-slate-700">
          <span>
            Uploaded <span className="font-medium">{upload.data.filename}</span>
          </span>
          <StatusBadge status={upload.data.status} />
        </p>
      )}
    </div>
  );
}
