import type { ButtonHTMLAttributes } from "react";

type Variant = "accent" | "positive" | "ghost" | "danger";

const BASE =
  "inline-flex items-center gap-2 rounded-lg px-3.5 py-1.5 font-mono text-[13px] font-semibold transition-colors disabled:opacity-45";

const VARIANTS: Record<Variant, string> = {
  // Primary system action (iris = the tool's own voice).
  accent: "bg-iris text-onaccent hover:bg-iris-bright",
  // Commit/approve (reality turning green).
  positive: "bg-ok text-onaccent hover:brightness-110",
  // Neutral secondary.
  ghost: "border border-line text-dim hover:bg-panel-2 hover:text-fg",
  // Destructive/reject.
  danger: "border border-danger/40 text-danger-fg hover:bg-danger/10",
};

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
}

/**
 * The tool's one action button. Defaults to `type="button"` so a button inside a
 * form never submits by accident; pass `className` for one-off layout hooks
 * (`ml-auto`, `mt-3`) without re-spelling the shared recipe.
 */
export function Button({ variant = "accent", className, type = "button", ...props }: ButtonProps) {
  const classes = className ? `${BASE} ${VARIANTS[variant]} ${className}` : `${BASE} ${VARIANTS[variant]}`;
  return <button type={type} className={classes} {...props} />;
}
