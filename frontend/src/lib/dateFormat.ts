import { useSyncExternalStore } from "react";

/** Supported timestamp layouts. Default is year-first `yyyy/mm/dd`. */
export type DateFormat = "ymd" | "dmy" | "mdy" | "iso";

const STORAGE_KEY = "revalid-date-format";
const DEFAULT: DateFormat = "ymd";

function isDateFormat(value: string | null): value is DateFormat {
  return value === "ymd" || value === "dmy" || value === "mdy" || value === "iso";
}

// A tiny external store so a format change re-renders every timestamp on the
// page at once (via useDateFormat), while non-component callers can still read
// the current value synchronously (formatDateTime).
const listeners = new Set<() => void>();

function readInitial(): DateFormat {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (isDateFormat(stored)) return stored;
  } catch {
    // localStorage unavailable (SSR/tests) — fall back to the default.
  }
  return DEFAULT;
}

let current: DateFormat = readInitial();

/** The active date format (non-reactive read). */
export function getDateFormat(): DateFormat {
  return current;
}

/** Persist and broadcast a new date format; all timestamps re-render. */
export function setDateFormat(format: DateFormat): void {
  current = format;
  try {
    localStorage.setItem(STORAGE_KEY, format);
  } catch {
    // Non-persistent this session if storage is blocked — still applied in-memory.
  }
  for (const listener of listeners) listener();
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** Subscribe a component to the active date format (re-renders on change). */
export function useDateFormat(): DateFormat {
  return useSyncExternalStore(subscribe, getDateFormat, getDateFormat);
}

function pad(value: number): string {
  return String(value).padStart(2, "0");
}

/** Format an ISO timestamp per `format`; returns the raw string if unparseable. */
export function formatDate(iso: string, format: DateFormat = current): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  const y = String(date.getFullYear());
  const mo = pad(date.getMonth() + 1);
  const d = pad(date.getDate());
  const time = `${pad(date.getHours())}:${pad(date.getMinutes())}`;
  switch (format) {
    case "dmy":
      return `${d}/${mo}/${y} ${time}`;
    case "mdy":
      return `${mo}/${d}/${y} ${time}`;
    case "iso":
      return `${y}-${mo}-${d} ${time}`;
    default:
      return `${y}/${mo}/${d} ${time}`;
  }
}

const SAMPLE = "2026-07-20T14:30:00";

/** The choices shown in Settings, each with a live example of the sample date. */
export const DATE_FORMATS: { id: DateFormat; label: string; example: string }[] = [
  { id: "ymd", label: "Year first", example: formatDate(SAMPLE, "ymd") },
  { id: "iso", label: "ISO 8601", example: formatDate(SAMPLE, "iso") },
  { id: "dmy", label: "Day first", example: formatDate(SAMPLE, "dmy") },
  { id: "mdy", label: "Month first", example: formatDate(SAMPLE, "mdy") },
];
