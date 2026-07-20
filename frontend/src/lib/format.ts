import { ApiError } from "../api/client";
import { formatDate, getDateFormat } from "./dateFormat";

/** Local timestamp in the operator's chosen format (default `yyyy/mm/dd HH:mm`). */
export function formatDateTime(iso: string): string {
  return formatDate(iso, getDateFormat());
}

/** Best-effort message for anything thrown by a query/mutation. */
export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.detail;
  if (error instanceof Error) return error.message;
  return "Unexpected error";
}
