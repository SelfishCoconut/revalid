---
name: feature-request
description: Turn a plain-language feature request from Álvaro into a labeled, board-ready GitHub issue (and optionally a feature branch). Use for "add a feature", "new feature request", "I want X", "turn this into an issue/card", "create a card for X".
---

# Feature request → board-ready issue

Front door for Álvaro's feature requests: convert an informal request into a properly-formed GitHub issue on the Kanban board, then optionally a feature branch — so the request → issue → PR → board loop starts clean. When the issue is opened, `board.yml` automatically adds it to **Backlog**; don't hand-add it.

**No duplication (CLAUDE.md rule):** requirement/SRS text is owned by the `requirements` skill; PR creation by `commit-commands` (`commit-push-pr`). This skill *orchestrates* and calls those — it never re-implements them.

## 1. Classify the request

Decide what kind of work item it is — Álvaro's call if ambiguous, so ask when unsure:

- **Existing requirement** — already an `FR-xx`/`NFR-xx` in `docs/requirements/srs.md`. Reuse that ID + `req:FR-xx` label. (Most FRs already have issues — check `gh issue list` first to avoid a duplicate card.)
- **New product requirement** — a new behavior of the system. **Stop and invoke the `requirements` skill first** to add the FR to the SRS (source of truth; it assigns the next free, immutable ID), then return here. Never create a product-behavior issue that isn't in the SRS.
- **Infra / tooling** — CI, build, dev-env, automation. Label `infra`, no FR. Title `<scope>: <title>`.
- **Thesis** — memoir/docs work. Label `thesis`; follow the `thesis-task` issue template shape.

## 2. Draft the issue (do NOT create it yet)

Mirror `.github/ISSUE_TEMPLATE/feature.yml` so a `gh`-created issue matches a template-created one:

- **Title**: `FR-xx: <imperative title>` (features) · `<scope>: <title>` (infra/thesis), Conventional-Commit scope word.
- **Body**:
  - **Requirement ID** — `FR-xx` / `NFR-xx`, or `infra` / `thesis`.
  - **Description** — what must exist when the card is Done (copy/refine the SRS "The system shall …" statement).
  - **Acceptance criteria** — testable checkboxes lifted from the SRS entry; runnable/checkable, never vague.
  - **MoSCoW priority** — Must / Should / Could / Won't.
- **Labels**: `feature` + `req:FR-xx` (functional) · `req:nonfunctional` (NFRs) · `infra` · `thesis`.
- **Milestone**: the matching `M1`–`M5` when known.

Present the full draft to Álvaro. **Nothing is created until he approves** (Reglamento TFG 2026 §6 — Claude never opens scope-bearing items unilaterally).

## 3. Create on approval

```sh
gh issue create --title "FR-xx: <title>" --body-file <draft.md> \
  --label feature --label req:FR-xx --milestone "M1 Walking skeleton"
```

- `board.yml` moves the new issue to **Backlog** automatically.
- **Known limitation**: the local `gh` token has no `project` scope, so you **cannot** set the board's MoSCoW priority single-select field from the CLI. Keep priority in the issue body; Álvaro sets the board field in the web UI. (Only the Action's `BOARD_PAT` can write the board.)
- For a functional requirement, update the FR's **"Traces to: issue #N"** line in the SRS via the `requirements` skill.

## 4. Optional — start the branch

If Álvaro wants to begin immediately, branch off up-to-date `main`:

```sh
git switch main && git pull
git switch -c feat/fr-xx-<slug>   # prefix = Conventional-Commit type the work will use (feat/fix/chore/docs/...)
```

The PR comes later (when there's code) via the `commit-commands` skill and **must** say `Closes #<issue>` — that's what makes `board.yml` move the card to **In Progress** on PR open, and to **Verify** on green CI. `Validate` stays manual (Álvaro).

## Definition of a good card
- Traces to a requirement (or is explicitly `infra`/`thesis`).
- Acceptance criteria are runnable/checkable.
- Correctly labeled + milestoned, so the board and the FR traceability audit stay clean.
