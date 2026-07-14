/** Accessible loading indicator — an instrument working the signal. */
export function Spinner({ label = "Loading" }: { label?: string }) {
  return (
    <span
      role="status"
      aria-label={label}
      className="inline-flex items-center gap-2.5 font-mono text-[13px] text-dim"
    >
      <span
        aria-hidden="true"
        className="size-4 animate-spin rounded-full border-2 border-line-2 border-t-iris"
      />
      {label}
    </span>
  );
}
