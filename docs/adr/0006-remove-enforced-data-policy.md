# 0006. Remove the enforced §6 data policy; Álvaro owns data handling directly

Date: 2026-07-13
Status: accepted

## Context

ADR-0001 established a §6 data-protection policy: a "hard rule" section in
CLAUDE.md forbidding real pentest/client/personal data from entering AI context
or the repo, backed by a `protect-private-data` PreToolUse hook and a quarantined
`data/private/`.

This project's working data is OWASP Juice Shop material — a deliberately
vulnerable **training** target (the evaluation fixture is a TryHackMe Juice Shop
report, lab data with no real client). Against that reality the documented
policy was largely belt-and-suspenders, and Álvaro decided he prefers to own the
data-handling judgement himself rather than have it framed as an enforced repo
rule — consistent with ADR-0005, where he took direct ownership of §6 compliance.

## Decision

We will **remove the enforced §6 data policy**:

- Delete the "Data protection (hard rule)" section from CLAUDE.md and the policy
  framing in `docs/development-plan.md`. Data handling is **Álvaro's own
  responsibility**; he decides what data is acceptable to use.
- **Retain, unchanged, the `protect-private-data` hook** as *generic security
  hygiene* (not §6 policy): it still blocks AI file tools from touching
  `data/private/`, `.env`, credential and key files. This was deliberately **not
  weakened** — an attempt to loosen it was declined. Álvaro can remove it at any
  time by deleting `.claude/hooks/protect-private-data.py` and its wiring; that
  is his call to make explicitly, not something done implicitly here.
- Keep `data/private/` gitignored, and gitignore report PDFs dropped at the repo
  root, so binaries / third-party names are not committed to the public repo by
  accident.
- The FR-06 allowlist (retests only hit allowlisted targets) is a code-level
  security control and is **unaffected** — it is not part of this policy.

This supersedes the data-protection element of ADR-0001; the rest stands.

## Alternatives considered

- **Keep the enforced policy (ADR-0001).** Rejected by Álvaro: for a lab-data
  project he prefers to own the judgement rather than an enforced rule, and the
  hook's string-matching added friction (it false-positived on commit messages).
- **Also remove the secret-file hook.** Not done here: silently stripping a guard
  against leaking `.env`/keys is a security weakening, and it was kept as generic
  hygiene. Álvaro can delete it himself if he wants — the path is documented.

## Consequences

- **Author ownership:** the §6 data judgement is unambiguously Álvaro's, matching
  the authorship stance of ADR-0005.
- **Accepted risk:** there is no longer a *documented policy* reminding a
  contributor (human or AI) not to feed real personal/client data to an AI or
  commit it. The **external regulatory §6 obligation still applies** and now
  rests entirely on Álvaro's manual diligence. Residual mitigations: the retained
  secret-file hook, the `data/private/` quarantine, and the fact that the tool's
  data is lab-only.
- **Reversible:** re-instating the policy is a docs edit; the hook was never
  removed.
