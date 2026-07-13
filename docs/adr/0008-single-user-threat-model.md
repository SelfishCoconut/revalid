# 0008. Single trusted-user threat model: drop the security-auditor agent and PDF bomb-hardening

Date: 2026-07-13
Status: accepted

## Context

ADR-0002 highlighted an unusual property of `revalid`: it parses untrusted
documents (pentest reports) and turns their content into executed actions. That
framing justified a bespoke `security-auditor` agent and defensive input
hardening. During PR #42 that agent found and fixed a real decompression-bomb
DoS in the PDF parser — a crafted sub-1 MB file that OOM-killed the interpreter,
and a single page that hung pdfminer for minutes — by bounding input size, page
count, extracted-text size, and wall-clock time.

Álvaro has now fixed the operating model explicitly: **`revalid` is a local,
single-user lab tool that he runs against reports he controls.** The operator is
trusted; inputs are trusted; the app binds to 127.0.0.1 only (NFR-03) and is
never exposed to other users or the network. Treating the operator as a
potential adversary — and carrying the code and the review step that implies —
is unnecessary ceremony for this tool. This is a right-sizing decision in the
same spirit as ADR-0004 (process), ADR-0005 (removed the ai-compliance-auditor
agent), and ADR-0006 (removed the enforced data policy).

## Decision

We will adopt a **single trusted-user threat model** and simplify accordingly:

- **Remove the `security-auditor` agent** (`.claude/agents/security-auditor.md`)
  and its references in `docs/development-plan.md`. No dedicated adversarial-input
  review step. General review (the code-review plugin, `doc-curator`) and the
  automated CI security jobs (Bandit, CodeQL, Gitleaks, pip-audit) remain — those
  are cheap and automatic; what goes is the bespoke human-style security audit.
- **Remove the PDF bomb-hardening** from `read_pdf`: the input-size, page-count,
  cumulative-text, and wall-clock-deadline bounds, and the broad fail-closed
  `except`. Revert to the simpler parser (as of commit `8fc7d28`).
- **Keep** the minimal fail-closed handling: a non-PDF, a structurally corrupt
  PDF, and a no-text PDF still raise a clear `PdfError`. That satisfies FR-01
  acceptance #2 ("rejected with a clear error, not a crash") and is basic
  robustness against an *honestly* malformed file — not an anti-adversary measure.
- **Keep** the FR-06 target allowlist / SSRF guard. It is a code-level
  correctness control (the retest executor must not hit the wrong host) valuable
  regardless of threat model, and is explicitly out of scope here — same stance as
  ADR-0006.
- **Close** the subprocess-isolation follow-up (#43) as not planned.

**Threat model of record:** single trusted local operator; reports and config are
trusted input; localhost-only; no multi-user or network exposure. We do **not**
defend against input crafted to attack the tool itself.

## Alternatives considered

- **Keep the hardening and the agent.** Rejected by Álvaro: unnecessary
  complexity for a single-user local tool. The demonstrated bomb is only
  reachable by feeding the tool a file crafted to attack it, which is outside the
  operating model.
- **Keep the bounds, drop only the agent.** Rejected: the bounds add resource
  constants and a `SIGALRM`/threading path whose sole justification was the
  adversarial model now dropped — cleaner to remove both together.
- **Also remove the corrupt-PDF fail-closed handling.** Not done: FR-01 requires
  rejecting a malformed PDF with a clear error instead of crashing. That is
  robustness, not security, and it is a requirement.

## Consequences

- **Simpler:** `read_pdf` is ~30 lines lighter with no `signal`/`threading` and
  no resource constants; one fewer agent to maintain; no dedicated review gate in
  the PR flow.
- **Accepted risk:** a maliciously-crafted PDF (decompression bomb / pathological
  page) can again OOM or hang the process. Acceptable because the operator
  controls the inputs and runs locally.
- **Reversal trigger (conscious, not an oversight):** if `revalid` is ever
  exposed beyond a single trusted user — a hosted/multi-user deployment, or
  accepting third-party report uploads — this decision must be revisited and the
  input bounds plus subprocess-isolated extraction reinstated.
- **Supersedes** the "Untrusted-input hardening" consequence recorded in ADR-0007;
  that ADR's pdfplumber library choice and FR-01→FR-03 seam are unaffected.
