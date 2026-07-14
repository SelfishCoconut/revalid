import { ApiError } from "../api/client";

/** Human-readable local timestamp; falls back to the raw string if unparseable. */
export function formatDateTime(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString();
}

/** Best-effort message for anything thrown by a query/mutation. */
export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.detail;
  if (error instanceof Error) return error.message;
  return "Unexpected error";
}
