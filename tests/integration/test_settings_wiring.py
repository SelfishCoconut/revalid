"""The persisted setting drives the agent the DI builds (ADR-0021)."""

import pytest

from revalid.app import create_app, get_extraction_agent, get_plan_agent, get_settings_dep
from revalid.db import create_db_engine, session_factory
from revalid.llm import agent_model_name
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

    # ADR-0021 runtime semantics: the rewired factories must build agents FROM
    # the stored setting. build_model strips the ollama:/openai: prefix, so the
    # built model_name is "llama3.2". Reverting the factory rewrite fails here.
    extraction_agent = get_extraction_agent(cfg)
    plan_agent = get_plan_agent(cfg)
    assert agent_model_name(extraction_agent) == "llama3.2"
    assert agent_model_name(plan_agent) == "llama3.2"
