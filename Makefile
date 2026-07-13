.PHONY: lint format typecheck test test-unit test-integration test-system sanity docs docs-serve thesis clean demo-ingest demo-walking-skeleton lab-up lab-down run

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

# M1 walking-skeleton demo (FR-07/FR-09): ingest -> probe -> verdict (needs the lab)
demo-walking-skeleton:
	uv run python scripts/demo/walking_skeleton.py

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

# Local web app — localhost only (NFR-03)
run:
	uv run uvicorn --factory revalid.app:create_app --host 127.0.0.1 --port 8000

# Mechanical signals consumed by the codebase-sanity agent (see docs/development-plan.md §5)
sanity:
	uv run xenon --max-absolute C --max-modules B --max-average A src
	uv run radon cc -s -a src
	uv run vulture src --min-confidence 80 || true
	uv run pylint --disable=all --enable=duplicate-code src || true

# UML/package diagrams are regenerated from code on every build — never stale
docs:
	mkdir -p docs/reference/generated
	uv run pyreverse -o mmd -d docs/reference/generated -p revalid src/revalid || true
	uv run mkdocs build --strict

docs-serve:
	uv run mkdocs serve

thesis:
	cd thesis && latexmk -xelatex -interaction=nonstopmode -halt-on-error TFG.tex

clean:
	rm -rf site .pytest_cache .mypy_cache .ruff_cache .coverage htmlcov
	cd thesis && latexmk -C TFG.tex 2>/dev/null || true
