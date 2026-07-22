/**
 * The retest goal is *edited* as free text — one step per line — and *stored* as
 * an ordered list of steps. These two functions are the only place that
 * transform lives, so the Goal stage and the console cannot drift apart on
 * whitespace or blank-line handling (issue #113 F2).
 */

/**
 * Parse goal-textarea text into ordered steps.
 *
 * Each line becomes one step, trimmed; blank lines are dropped so a trailing
 * newline or a spacer line never becomes an empty step the agent would have to
 * interpret.
 */
export function parseGoalSteps(text: string): string[] {
  return text
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
}

/** Render ordered steps back into textarea text, one step per line. */
export function goalStepsToText(steps: string[]): string {
  return steps.join("\n");
}
