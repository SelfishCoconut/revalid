"""The persisted setting drives the agent the DI builds (ADR-0021)."""

import pytest

from revalid.app import create_app, get_settings_dep
from revalid.db import create_db_engine, session_factory
from revalid.settings import save

pytestmark = pytest.mark.integration


def test_di_builds_agent_from_stored_setting() -> None:
    engine = create_db_engine(":memory:")
    with session_factory(engine)() as s:
        save(s, model="ollama:llama3.2", base_url="http://h:11434/v1", api_key=None)
    app = create_app(engine=engine)

    # Resolve the settings dependency the way FastAPI would, via app.state.
    class _Req:
        def __init__(self, application: object) -> None:
            self.app = application

    cfg = get_settings_dep(_Req(app))  # type: ignore[arg-type]
    assert cfg.model == "ollama:llama3.2"
    assert cfg.base_url == "http://h:11434/v1"
