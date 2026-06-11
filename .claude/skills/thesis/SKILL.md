---
name: thesis
description: Conventions and build for the thesis memoir (English, ESII XeLaTeX template). Use when writing or editing thesis chapters, building the PDF, or checking page budget / template compliance.
---

# Thesis (memoria) work

English. ESII template in `thesis/`, XeLaTeX, main font Carlito (Calibri-metric substitute — never change it). Build: `make thesis` (latexmk -xelatex). Recommended max **80 pages** from chapter 1 through bibliography (appendices excluded).

## Structure & template

- Chapters live in `thesis/chapters/chN.tex`, included from `TFG.tex`. Document metadata in `thesis/include/opciones.tex`.
- Use the template's lists where relevant: `\listofalgorithms`, `\listofcodes` (comment out in `TFG.tex` if unused).
- Bibliography: `thesis/bib/ref.bib`, BibTeX. Every claim sourced; MITRE ATT&CK and the proposal's references are mandatory citations.

## Writing rules (derived from the tribunal's Anexo I rubric)

- Clear academic English; one idea per paragraph; keep a visible thread (each chapter opens with what it covers and why it follows from the previous one).
- EVERY figure, table, algorithm and listing must be referenced AND discussed in the body text (`Figure~\ref{...} shows…`). Unreferenced floats are rubric violations.
- Original content must be clearly distinguishable from sourced content; cite everything external.
- Content describing the work: relate it to the degree and the effort invested (rubric item); quantify where possible (flow metrics, evaluation results).

## Page budget

After building, check the PDF page count from chapter 1 to bibliography. Warn at 70+, alarm at 78+. Appendices are free.

## Compliance

The methodology chapter must contain the AI-usage declaration — generate/update it with the `ai-declaration` skill, never by hand. Drafts get reviewed by the `thesis-reviewer` agent before Álvaro's pass.
