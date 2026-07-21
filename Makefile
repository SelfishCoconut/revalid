.PHONY: lint format typecheck test test-unit test-integration test-system sanity uml docs docs-serve thesis clean demo-ingest demo-ingest-pdf demo-extract demo-audit demo-export export-schema demo-eval eval ground-truth-skeleton demo-retest-session lab-up lab-down run reset-db ui-install ui-lint ui-test build-ui dev-ui demo-ui demo-settings

lint:
	uv run ruff check src tests
	uv run ruff format --check src tests

format:
	uv run ruff format src tests
	uv run ruff check --fix src tests

typecheck:
	uv run mypy

test-unit:
	uv run pytest -m "not integration and not system" --cov --cov-report=term-missing

test-integration:
	uv run pytest -m integration --no-cov

test-system:
	uv run pytest -m system --no-cov

test: test-unit test-integration test-system

# FR-02 walking-skeleton demo: ingest the synthetic sample, print what persisted
demo-ingest:
	uv run python scripts/demo/ingest_defectdojo.py

# FR-01 demo: extract a PDF report into raw finding candidates (no LLM, no lab)
demo-ingest-pdf:
	uv run python scripts/demo/ingest_pdf.py

# FR-03 demo: LLM-extract structured findings from a PDF (Claude if ANTHROPIC_API_KEY,
# else an offline stand-in model — runs the full FR-01 -> FR-03 pipeline either way)
demo-extract:
	uv run python scripts/demo/extract_pdf.py

# FR-10 audit trail (ADR-0015): re-derive every verdict from stored evidence
# alone, no re-execution — offline
demo-audit:
	uv run python scripts/demo/audit_rederive.py

# FR-12 versioned run export (ADR-0016): build a run offline, export it as one
# versioned JSON document, and validate it against the published JSON schema
demo-export:
	uv run python scripts/demo/results_export.py

# Regenerate the published FR-12 JSON schema from the RunExport model. Run after
# changing src/revalid/export.py; the drift test (test_export.py) enforces it.
export-schema:
	uv run python -c "import json; from revalid.export import export_schema; \
p='docs/reference/schemas/run-export.schema.json'; \
open(p,'w').write(json.dumps(export_schema(), indent=2, sort_keys=True)+'\n'); \
print('wrote',p)"

# FR-15 evaluation harness demo: score a synthetic run against ground truth and
# print the verdict-reliability metrics table, fully offline
demo-eval:
	uv run python scripts/demo/evaluate_run.py

# FR-15 evaluation harness (one command → metrics table from a run export).
# Author ground truth from tests/data/eval/ground_truth.example.json; produce the
# export via the app's GET /api/export (or `make demo-export`). Exits non-zero on
# an NFR-01 miss. Usage: make eval EXPORT=run.json GROUND_TRUTH=gt.json
eval:
	uv run python scripts/evaluate.py --export "$(EXPORT)" --ground-truth "$(GROUND_TRUTH)"

# FR-15 authoring aid: emit a fill-in-the-blanks ground-truth skeleton (one entry
# per finding, titles pre-keyed) from a run export. OUT is optional (stdout if
# unset). Usage: make ground-truth-skeleton EXPORT=run.json OUT=tests/data/eval/ground_truth.json
ground-truth-skeleton:
	uv run python scripts/make_ground_truth.py --export "$(EXPORT)" $(if $(OUT),--out "$(OUT)",)

# FR-17 agentic retest session demo (ADR-0025, Slice 0): propose -> approve ->
# output -> verdict through the real orchestrator, with a FakeSandbox + scripted
# FunctionModel standing in for Docker/the lab/the LLM — fully offline. PYTHONPATH=.
# mirrors pytest's pythonpath setting so the shared tests/_retest_helpers.py
# scripted model is importable outside the test tree. A real run against the
# lab needs the `sandbox` extra + `make lab-up` — see the system test.
demo-retest-session:
	PYTHONPATH=. uv run python scripts/demo/retest_session.py

# FR-13 enhancement demo (ADR-0021): the DB-persisted model/provider setting drives
# the next agent build — seed default -> change -> build_model picks it up, offline
demo-settings:
	uv run python scripts/demo/settings.py

# Retest lab (retest-lab skill) — intentionally vulnerable targets, localhost only
lab-up:
	docker compose -f lab/docker-compose.yml up -d
	@echo "waiting for Juice Shop on http://localhost:3000 ..."
	@for i in $$(seq 1 30); do \
		if curl -sf -o /dev/null http://localhost:3000/rest/admin/application-version; then \
			echo "lab is up"; exit 0; fi; \
		sleep 2; \
	done; echo "lab did not become ready in time" >&2; exit 1

lab-down:
	docker compose -f lab/docker-compose.yml down

# Local web app — localhost only (NFR-03). Serves the built SPA at / when
# `make build-ui` has produced frontend/dist; the /api backend either way.
run:
	uv run uvicorn --factory revalid.app:create_app --host 127.0.0.1 --port 8000

# Drop the local SQLite DB so the next `make run` recreates it with the current
# schema. Needed after a model change adds a column: there are no migrations
# (ADR-0002/0008), so a pre-existing dev DB fails with e.g.
# "table verdicts has no column named actor" (ADR-0015). Safe: the DB is
# gitignored local state, never a source of truth.
reset-db:
	rm -f revalid.db revalid.db-journal revalid.db-wal revalid.db-shm
	@echo "local DB removed; the next 'make run' recreates it fresh"

# --- Frontend (React SPA, FR-11) ---
# Reproducible install of the SPA toolchain
ui-install:
	npm --prefix frontend ci

ui-lint:
	npm --prefix frontend run lint
	npm --prefix frontend run typecheck

ui-test:
	npm --prefix frontend run test

# Build the SPA into frontend/dist (then `make run` serves the whole tool at /)
build-ui: ui-install
	npm --prefix frontend run build

# SPA dev server with hot reload; proxies /api -> 127.0.0.1:8000. Run `make run`
# in another shell for the backend.
dev-ui:
	npm --prefix frontend run dev

# FR-11 acceptance demo: build the SPA and serve the whole tool on localhost so
# the full flow (upload PDF -> goal -> agentic retest -> verdicts) is operable
# from the browser alone. Prereqs for a live retest: `make lab-up` and an LLM
# backend (ANTHROPIC_API_KEY, or REVALID_LLM_MODEL=ollama:<model> + a server).
demo-ui: build-ui run

# Mechanical signals consumed by the codebase-sanity agent (see docs/development-plan.md §5)
sanity:
	uv run xenon --max-absolute C --max-modules B --max-average A src
	uv run radon cc -s -a src
	uv run vulture src --min-confidence 80 || true
	uv run pylint --disable=all --enable=duplicate-code src || true

# UML diagrams are regenerated from code on every build — never stale. The layer
# grouping, the pyreverse flags and the pruning all live in the script, so `make docs`
# and the Pages workflow cannot drift apart. See scripts/gen_uml.py for the why (#158).
uml:
	uv run python scripts/gen_uml.py

docs: uml
	uv run mkdocs build --strict

docs-serve:
	uv run mkdocs serve

thesis:
	cd thesis && latexmk -xelatex -interaction=nonstopmode -halt-on-error TFG.tex

clean:
	rm -rf site .pytest_cache .mypy_cache .ruff_cache .coverage htmlcov
	cd thesis && latexmk -C TFG.tex 2>/dev/null || true
