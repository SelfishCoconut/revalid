## What & why

Closes #<issue>. <!-- Every PR traces to a Kanban card / requirement -->

<one paragraph: what this changes and why>

## How to validate (mandatory — nothing merges without Álvaro running this)

```sh
# exact commands to run
```

**Expected output / behavior:**

<what Álvaro should see>

**Acceptance criteria** (from the requirement, tick after personally verifying):

- [ ] …
- [ ] …

## Self-review checklist

- [ ] I (Álvaro) ran the validation steps above myself and the behavior is correct
- [ ] Tests added/updated at the right pyramid level (unit / integration / system)
- [ ] Docstrings on new/changed public symbols; affected authored diagrams & docs pages updated
- [ ] No sensitive or non-synthetic data introduced (tests/data/ stays synthetic)
- [ ] Significant decisions recorded as ADR

## AI assistance

- [ ] This PR contains AI-assisted work (Claude Code) — commits carry the `Co-Authored-By` trailer and the session is in `docs/ai-usage/`
