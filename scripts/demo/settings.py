"""Demo for the FR-13 model/provider setting: DB-persisted, runtime-editable (ADR-0021).

Usage::

    uv run python scripts/demo/settings.py

Runs fully offline (in-memory DB, no network calls). Shows the setting's whole
lifecycle: seed the local-first default on first use, change it, and have
``build_model`` pick up the change on the *next* agent build — no restart.
"""

from __future__ import annotations

from revalid.db import IN_MEMORY, create_db_engine, session_factory
from revalid.llm import build_model
from revalid.settings import load_or_seed, save


def main() -> None:
    """Seed the default setting, change it, and build a model from the change."""
    engine = create_db_engine(IN_MEMORY)
    with session_factory(engine)() as session:
        seeded = load_or_seed(session)
        print(f"seeded default : {seeded.model}  base_url={seeded.base_url}")
        changed = save(session, model="ollama:qwen3:14b", base_url=seeded.base_url, api_key=None)
        print(f"after change   : {changed.model}")
        model = build_model(changed)
        print(f"built model    : {type(model).__name__} -> {getattr(model, 'model_name', model)}")


if __name__ == "__main__":
    main()
