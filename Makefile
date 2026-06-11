.PHONY: lint format typecheck test test-unit test-integration test-system sanity docs docs-serve thesis clean demo-ingest run

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
