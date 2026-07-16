/**
 * Optional operator-guidance textarea for plan (re)generation (FR-04, ADR-0023).
 * The text is woven into the generation prompt and recorded in the plan's
 * lineage; it steers what the model proposes but never bypasses the FR-06 gate.
 */
export function InstructionsField({
  value,
  onChange,
  disabled = false,
  id = "plan-instructions",
}: {
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
  id?: string;
}) {
  return (
    <label htmlFor={id} className="block">
      <span className="font-mono text-[10px] font-medium uppercase tracking-[0.14em] text-faint">
        Extra guidance (optional)
      </span>
      <textarea
        id={id}
        rows={2}
        value={value}
        disabled={disabled}
        onChange={(event) => {
          onChange(event.target.value);
        }}
        placeholder="e.g. also check /admin for IDOR, and try the basket endpoint"
        className="mt-1 w-full resize-y rounded-lg border border-line bg-panel-2 px-2.5 py-1.5 text-[13px] text-fg transition-colors placeholder:text-faint focus:border-iris/60 disabled:opacity-55"
      />
    </label>
  );
}
