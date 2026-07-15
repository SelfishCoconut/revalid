# Model/Provider Settings Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the LLM model/provider a DB-persisted, runtime-editable user setting (default local-first `ollama:qwen3.6:27b`), with a settings API + SPA panel, model discovery, and a connection test — enhancing FR-13 per ADR-0021.

**Architecture:** A single-row `settings` table becomes the source of truth for backend selection, seeded once from `REVALID_LLM_MODEL`/`OLLAMA_BASE_URL` on a fresh DB then authoritative. A new `settings.py` owns load/seed/save; `llm.build_model(cfg)` constructs the concrete Pydantic AI model (explicit `OpenAIProvider(base_url, api_key)` when a base URL is set, else native provider-with-key or a bare string). Resolution moves into the agent DI (`get_extraction_agent`/`get_plan_agent`) via `request.app.state.sessions`, so a saved change takes effect on the next agent build with no restart. A `/api/settings` CRUD + `/api/settings/probe` endpoint (which hits `{base_url}/models`) power a `/settings` SPA route.

**Tech Stack:** Python 3.12 · FastAPI · SQLAlchemy 2.0 (SQLite) · Pydantic AI (`pydantic-ai-slim[anthropic,openai]>=2.9.0`) · httpx · React/TS/Vite/Tailwind · @tanstack/react-query · vitest.

## Global Constraints

- `mypy --strict` must pass repo-wide (src + tests). Full type hints; Google docstrings on public API.
- Ruff lint + format, line length 100. The write-hook strips imports added before their first use — add each import together with its first usage in the same edit.
- Complexity gate: xenon max absolute C. Refactor, never suppress.
- Backend coverage ≥ 80% on `src/`; new modules (`settings.py`, `llm.build_model`) should reach ~100%.
- Tests use Pydantic AI `TestModel`/`FunctionModel` (no live LLM) and `httpx.MockTransport` (no network). No test may perform real I/O in `tests/unit`/`tests/integration`.
- Frontend gates: `npm run lint` (eslint) + `npm run typecheck` (tsc) + `npm run build` (vite) + `npm run test` (vitest) all green; the two-tier coverage floor (whole-app regression floor + per-file 100% pins on owned modules) must hold.
- Conventional Commits; every commit ends with the `Co-Authored-By: Claude <noreply@anthropic.com>` trailer.
- API keys are secrets: never returned by any GET/response model; masked as `api_key_set` + last-4 `api_key_hint` only. `revalid.db` stays gitignored.
- Precedence rule (verbatim intent): a fresh DB seeds from `REVALID_LLM_MODEL`/`OLLAMA_BASE_URL` or the code default; thereafter the stored row is authoritative and `os.environ` no longer overrides it.
- Default model `ollama:qwen3.6:27b`; default base URL `http://localhost:11434/v1`.

---

### Task 1: `Settings` domain schema + `SettingsRecord` table + `settings.py` (load/seed/save)

**Files:**
- Modify: `src/revalid/domain.py` (add `Settings`)
- Modify: `src/revalid/db.py` (add `SettingsRecord`)
- Create: `src/revalid/settings.py`
- Test: `tests/unit/test_settings.py`

**Interfaces:**
- Produces:
  - `domain.Settings(BaseModel)`: `model: str`, `base_url: str | None = None`, `api_key: str | None = None` (frozen).
  - `db.SettingsRecord` with `from_domain(Settings) -> SettingsRecord`, `to_domain() -> Settings`; singleton `id == settings.SETTINGS_ID`.
  - `settings.SETTINGS_ID: int = 1`
  - `settings.load_or_seed(session: Session) -> Settings`
  - `settings.save(session: Session, *, model: str, base_url: str | None, api_key: str | None, clear_key: bool = False) -> Settings`
- Consumes: `llm.DEFAULT_MODEL`, `llm.DEFAULT_BASE_URL` (defined in Task 2 — for Task 1, add temporary local constants and swap to the `llm` import in Task 2). To keep Task 1 self-contained and correct, define the two defaults **in `settings.py` now** and Task 2 re-homes them to `llm.py`.

- [ ] **Step 1: Add the `Settings` domain schema.** In `src/revalid/domain.py`, after the `Severity` enum / near the other models, add:

```python
class Settings(BaseModel):
    """User-configurable LLM backend selection (FR-13 / ADR-0021).

    Attributes:
        model: A Pydantic AI ``provider:model`` string (e.g. ``ollama:qwen3.6:27b``).
        base_url: Provider base URL for OpenAI-compatible backends (Ollama and
            friends); ``None`` for native providers configured from the environment.
        api_key: Provider API key, or ``None`` when supplied via the environment.
    """

    model_config = ConfigDict(frozen=True, protected_namespaces=())

    model: str = Field(min_length=1)
    base_url: str | None = None
    api_key: str | None = None
```

`ConfigDict`/`Field` are already imported in `domain.py`. `protected_namespaces=()` silences Pydantic's `model_`-namespace warning for the `model` field.

- [ ] **Step 2: Write failing tests for the record + seed/load/save.** Create `tests/unit/test_settings.py`:

```python
"""Unit tests for the persisted model/provider setting (FR-13, ADR-0021)."""

import pytest
from sqlalchemy.orm import Session

from revalid import settings as settings_mod
from revalid.db import IN_MEMORY, create_db_engine, session_factory
from revalid.llm import DEFAULT_BASE_URL, DEFAULT_MODEL


@pytest.fixture
def session() -> Session:
    engine = create_db_engine(IN_MEMORY)
    with session_factory(engine)() as s:
        yield s


def test_seed_on_empty_db_uses_local_first_default(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("REVALID_LLM_MODEL", raising=False)
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    cfg = settings_mod.load_or_seed(session)
    assert cfg.model == DEFAULT_MODEL == "ollama:qwen3.6:27b"
    assert cfg.base_url == DEFAULT_BASE_URL == "http://localhost:11434/v1"
    assert cfg.api_key is None


def test_seed_reads_env_when_present(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("REVALID_LLM_MODEL", "ollama:llama3.2")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://host:1234/v1")
    cfg = settings_mod.load_or_seed(session)
    assert cfg.model == "ollama:llama3.2"
    assert cfg.base_url == "http://host:1234/v1"


def test_load_is_idempotent_and_persists_once(session: Session) -> None:
    first = settings_mod.load_or_seed(session)
    second = settings_mod.load_or_seed(session)
    assert first == second


def test_save_updates_model_and_base_url(session: Session) -> None:
    settings_mod.load_or_seed(session)
    cfg = settings_mod.save(
        session, model="anthropic:claude-sonnet-5", base_url=None, api_key="sk-123"
    )
    assert cfg.model == "anthropic:claude-sonnet-5"
    assert cfg.base_url is None
    assert cfg.api_key == "sk-123"


def test_save_with_blank_key_keeps_existing_key(session: Session) -> None:
    settings_mod.save(session, model="anthropic:claude-sonnet-5", base_url=None, api_key="sk-123")
    cfg = settings_mod.save(
        session, model="anthropic:claude-sonnet-5", base_url=None, api_key=""
    )
    assert cfg.api_key == "sk-123"


def test_save_clear_key_removes_it(session: Session) -> None:
    settings_mod.save(session, model="x:y", base_url=None, api_key="sk-123")
    cfg = settings_mod.save(session, model="x:y", base_url=None, api_key=None, clear_key=True)
    assert cfg.api_key is None
```

- [ ] **Step 3: Run tests to verify they fail.**

Run: `uv run pytest tests/unit/test_settings.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'revalid.settings'` (and missing `DEFAULT_BASE_URL`).

- [ ] **Step 4: Add the `SettingsRecord` table.** In `src/revalid/db.py`, import `Settings` (add to the existing `from revalid.domain import (...)` block) and add after `VerdictRecord`:

```python
class SettingsRecord(Base):
    """The single-row persisted model/provider setting (FR-13 / ADR-0021).

    One row (``id == 1``) holds the runtime backend selection. The API key is
    stored here in the gitignored SQLite file (ADR-0008) but is never returned
    by the API (write-only, masked on read).
    """

    __tablename__ = "settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    model: Mapped[str] = mapped_column(String(128))
    base_url: Mapped[str | None] = mapped_column(String(256), default=None)
    api_key: Mapped[str | None] = mapped_column(String(256), default=None)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    @classmethod
    def from_domain(cls, cfg: Settings) -> SettingsRecord:
        """Build the singleton row from a domain settings object."""
        return cls(model=cfg.model, base_url=cfg.base_url, api_key=cfg.api_key)

    def to_domain(self) -> Settings:
        """Convert this row back to a domain settings object."""
        return Settings(model=self.model, base_url=self.base_url, api_key=self.api_key)
```

(`Mapped`, `mapped_column`, `String`, `JSON`, `DateTime`, `func`, `datetime` are already imported in `db.py`.)

- [ ] **Step 5: Create `src/revalid/settings.py`.**

```python
"""Persisted, runtime-editable model/provider setting (FR-13, ADR-0021).

A single ``settings`` row is the source of truth for LLM backend selection. On
a fresh database it is seeded once from the environment (``REVALID_LLM_MODEL`` /
``OLLAMA_BASE_URL``) or the local-first default; thereafter the stored row is
authoritative and the environment no longer overrides it.
"""

from __future__ import annotations

import os

from sqlalchemy.orm import Session

from revalid.db import SettingsRecord
from revalid.domain import Settings
from revalid.llm import DEFAULT_BASE_URL, DEFAULT_MODEL

SETTINGS_ID = 1
"""Primary key of the singleton settings row."""


def _seed_from_env() -> Settings:
    """Compute the initial setting from the environment or the default."""
    model = os.environ.get("REVALID_LLM_MODEL", "").strip() or DEFAULT_MODEL
    base_url = os.environ.get("OLLAMA_BASE_URL", "").strip() or (
        DEFAULT_BASE_URL if model.startswith("ollama:") else None
    )
    return Settings(model=model, base_url=base_url, api_key=None)


def load_or_seed(session: Session) -> Settings:
    """Return the current setting, seeding the singleton row on first use.

    Args:
        session: An open SQLAlchemy session.

    Returns:
        The persisted :class:`~revalid.domain.Settings`.
    """
    record = session.get(SettingsRecord, SETTINGS_ID)
    if record is None:
        record = SettingsRecord.from_domain(_seed_from_env())
        record.id = SETTINGS_ID
        session.add(record)
        session.commit()
        session.refresh(record)
    return record.to_domain()


def save(
    session: Session,
    *,
    model: str,
    base_url: str | None,
    api_key: str | None,
    clear_key: bool = False,
) -> Settings:
    """Persist an updated setting and return it.

    The API key is *sticky*: a blank/``None`` ``api_key`` leaves the stored key
    unchanged (so the UI never has to re-enter it); ``clear_key`` explicitly
    removes it.

    Args:
        session: An open SQLAlchemy session.
        model: The Pydantic AI ``provider:model`` string.
        base_url: Provider base URL, or ``None`` for env-configured providers.
        api_key: A new key to store, or blank/``None`` to keep the existing one.
        clear_key: When true, delete the stored key.

    Returns:
        The persisted :class:`~revalid.domain.Settings`.
    """
    record = session.get(SettingsRecord, SETTINGS_ID)
    if record is None:
        record = SettingsRecord.from_domain(_seed_from_env())
        record.id = SETTINGS_ID
        session.add(record)
    record.model = model
    record.base_url = base_url or None
    if clear_key:
        record.api_key = None
    elif api_key:
        record.api_key = api_key
    session.commit()
    session.refresh(record)
    return record.to_domain()
```

- [ ] **Step 6: Run tests to verify they pass.**

Run: `uv run pytest tests/unit/test_settings.py -q`
Expected: PASS (6 passed). If `ImportError: cannot import name 'DEFAULT_BASE_URL'` — Task 2 defines it; temporarily add `DEFAULT_MODEL = "ollama:qwen3.6:27b"` and `DEFAULT_BASE_URL = "http://localhost:11434/v1"` to `llm.py` now (Task 2 finalizes them).

- [ ] **Step 7: Commit.**

```bash
git add src/revalid/domain.py src/revalid/db.py src/revalid/settings.py tests/unit/test_settings.py src/revalid/llm.py
git commit -m "feat(settings): persisted model/provider setting — schema, table, load/seed/save (ADR-0021)

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 2: Local-first default + `llm.build_model`

**Files:**
- Modify: `src/revalid/llm.py` (defaults + `build_model`)
- Modify: `tests/unit/test_llm.py` (update the default assertion)
- Test: `tests/unit/test_build_model.py`

**Interfaces:**
- Produces:
  - `llm.DEFAULT_MODEL: KnownModelName = "ollama:qwen3.6:27b"`
  - `llm.DEFAULT_BASE_URL: str = "http://localhost:11434/v1"`
  - `llm.build_model(cfg: Settings) -> Model | str`
- Consumes: `domain.Settings` (Task 1).

- [ ] **Step 1: Write failing tests for `build_model`.** Create `tests/unit/test_build_model.py`:

```python
"""Unit tests for constructing a concrete model from a Settings object (ADR-0021)."""

from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.openai import OpenAIChatModel

from revalid.domain import Settings
from revalid.llm import build_model


def test_base_url_builds_openai_compatible_model_stripping_provider_prefix() -> None:
    model = build_model(Settings(model="ollama:qwen3.6:27b", base_url="http://h:11434/v1"))
    assert isinstance(model, OpenAIChatModel)
    assert model.model_name == "qwen3.6:27b"


def test_anthropic_with_stored_key_builds_native_model() -> None:
    model = build_model(Settings(model="anthropic:claude-sonnet-5", api_key="sk-1"))
    assert isinstance(model, AnthropicModel)
    assert model.model_name == "claude-sonnet-5"


def test_no_base_url_no_key_falls_back_to_bare_string() -> None:
    model = build_model(Settings(model="anthropic:claude-sonnet-5"))
    assert model == "anthropic:claude-sonnet-5"


def test_bare_model_name_with_base_url_is_used_verbatim() -> None:
    model = build_model(Settings(model="gpt-4o", base_url="http://h/v1"))
    assert isinstance(model, OpenAIChatModel)
    assert model.model_name == "gpt-4o"
```

- [ ] **Step 2: Run tests to verify they fail.**

Run: `uv run pytest tests/unit/test_build_model.py -q`
Expected: FAIL — `ImportError: cannot import name 'build_model'`.

- [ ] **Step 3: Update defaults and add `build_model` in `src/revalid/llm.py`.** Change the default constant and add the new symbol; add the imports **with** their usage:

```python
from pydantic_ai import Agent
from pydantic_ai.models import KnownModelName, Model
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.anthropic import AnthropicProvider
from pydantic_ai.providers.openai import OpenAIProvider

from revalid.domain import Settings
```

Replace the `DEFAULT_MODEL` definition and add `DEFAULT_BASE_URL`:

```python
DEFAULT_MODEL: KnownModelName = "ollama:qwen3.6:27b"
"""Local-first default backend (ADR-0021); used to seed a fresh settings row."""

DEFAULT_BASE_URL = "http://localhost:11434/v1"
"""Default Ollama server address paired with :data:`DEFAULT_MODEL` (ADR-0021)."""
```

Add the builder (after `resolve_model`):

```python
def build_model(cfg: Settings) -> Model | str:
    """Construct a concrete Pydantic AI model from a persisted setting (ADR-0021).

    - A ``base_url`` selects an OpenAI-compatible model (Ollama or any
      OpenAI-compatible host); the ``ollama:``/``openai:`` provider prefix is
      stripped from the model name and a placeholder key is used when none is
      stored (Ollama ignores it).
    - Otherwise a native provider with a *stored* key is built explicitly; with
      no stored key the bare ``provider:model`` string is returned so Pydantic AI
      resolves credentials from the environment (backward-compatible with FR-13).

    Args:
        cfg: The persisted settings.

    Returns:
        A Pydantic AI :class:`~pydantic_ai.models.Model` instance, or the model
        string when the environment should supply credentials.
    """
    if cfg.base_url:
        name = (
            cfg.model.split(":", 1)[1]
            if cfg.model.startswith(("ollama:", "openai:"))
            else cfg.model
        )
        return OpenAIChatModel(
            name,
            provider=OpenAIProvider(base_url=cfg.base_url, api_key=cfg.api_key or "ollama"),
        )
    provider, _, name = cfg.model.partition(":")
    if provider == "anthropic" and cfg.api_key:
        return AnthropicModel(name, provider=AnthropicProvider(api_key=cfg.api_key))
    return cfg.model
```

- [ ] **Step 4: Update the existing default-pinning test.** In `tests/unit/test_llm.py`, replace `test_defaults_to_claude_when_unset` with:

```python
def test_defaults_to_local_first_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(MODEL_ENV, raising=False)
    assert resolve_model() == DEFAULT_MODEL == "ollama:qwen3.6:27b"
```

- [ ] **Step 5: Run tests to verify they pass.**

Run: `uv run pytest tests/unit/test_build_model.py tests/unit/test_llm.py -q`
Expected: PASS. (If `OpenAIChatModel.model_name` differs from expectation, run `uv run python -c "from pydantic_ai.models.openai import OpenAIChatModel; from pydantic_ai.providers.openai import OpenAIProvider; m=OpenAIChatModel('qwen3.6:27b', provider=OpenAIProvider(base_url='http://h/v1', api_key='x')); print(m.model_name)"` and adjust the assertion to the real attribute — do not change the construction.)

- [ ] **Step 6: Commit.**

```bash
git add src/revalid/llm.py tests/unit/test_build_model.py tests/unit/test_llm.py
git commit -m "feat(llm): local-first default + build_model from a Settings object (ADR-0021)

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 3: Wire the setting into the agent DI (app.py)

**Files:**
- Modify: `src/revalid/app.py` (`get_settings_dep`, `get_extraction_agent`, `get_plan_agent`, `create_app`)
- Test: `tests/integration/test_settings_wiring.py`

**Interfaces:**
- Produces: `app.get_settings_dep(request) -> Settings`; `create_app` sets `app.state.sessions`.
- Consumes: `settings.load_or_seed`, `llm.build_model`, `extract.build_extraction_agent`, `plan.build_plan_agent`.

- [ ] **Step 1: Write a failing integration test that a stored setting drives the built agent.** Create `tests/integration/test_settings_wiring.py`:

```python
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
```

- [ ] **Step 2: Run the test to verify it fails.**

Run: `uv run pytest tests/integration/test_settings_wiring.py -q -m integration`
Expected: FAIL — `ImportError: cannot import name 'get_settings_dep'`.

- [ ] **Step 3: Add the settings dependency and rewire the agent factories.** In `src/revalid/app.py`:

Add imports (with usage):

```python
from typing import cast

from starlette.requests import Request

from revalid.domain import Settings
from revalid.llm import build_model
from revalid.settings import load_or_seed
```

Add above `get_plan_agent`:

```python
def get_settings_dep(request: Request) -> Settings:
    """Load the persisted model/provider setting, seeding a fresh DB (ADR-0021)."""
    sessions = cast("sessionmaker[Session]", request.app.state.sessions)
    with sessions() as session:
        return load_or_seed(session)


SettingsDep = Annotated[Settings, Depends(get_settings_dep)]
```

Rewrite the two factories to build from the setting:

```python
def get_plan_agent(settings: SettingsDep) -> Agent[None, list[PlannedAction]]:
    """Yield the FR-04 plan agent built from the persisted setting (ADR-0021)."""
    return build_plan_agent(build_model(settings))


def get_extraction_agent(settings: SettingsDep) -> Agent[None, list[ExtractedFinding]]:
    """Yield the FR-03 extraction agent built from the persisted setting (ADR-0021)."""
    return build_extraction_agent(build_model(settings))
```

In `create_app`, after `sessions = session_factory(db_engine)` and after the `app` is created, register the factory on app state (place it next to where `app` is built, before returning):

```python
    app.state.sessions = sessions
```

- [ ] **Step 4: Run the test + the full existing API suite to prove no regression.**

Run: `uv run pytest tests/integration/test_settings_wiring.py tests/unit/test_reports.py tests/integration/test_reports_api.py tests/integration/test_approval_api.py tests/unit/test_retest_api.py -q`
Expected: PASS. Existing `dependency_overrides[get_extraction_agent] = lambda: agent` still short-circuits the new `SettingsDep`, so those tests are unaffected.

- [ ] **Step 5: Commit.**

```bash
git add src/revalid/app.py tests/integration/test_settings_wiring.py
git commit -m "feat(app): agent DI builds from the persisted setting via app.state (ADR-0021)

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 4: Settings CRUD API (`GET`/`PUT /api/settings`)

**Files:**
- Modify: `src/revalid/app.py` (`SettingsOut`, `SettingsUpdateIn`, `_register_settings_routes`, register in `create_app`)
- Test: `tests/integration/test_settings_api.py`

**Interfaces:**
- Produces: `GET /api/settings -> SettingsOut`; `PUT /api/settings (SettingsUpdateIn) -> SettingsOut`.
- `SettingsOut`: `model: str`, `base_url: str | None`, `api_key_set: bool`, `api_key_hint: str | None`, `updated_at`-free (derived from domain; no timestamp needed in the response — keep it minimal).
- `SettingsUpdateIn`: `model: str (min_length=1)`, `base_url: str | None = None`, `api_key: str | None = None`, `clear_key: bool = False`.

- [ ] **Step 1: Write failing endpoint tests.** Create `tests/integration/test_settings_api.py`:

```python
"""Settings CRUD API — masked key, sticky key, runtime update (ADR-0021)."""

import pytest
from fastapi.testclient import TestClient

from revalid.app import create_app
from revalid.db import create_db_engine

pytestmark = pytest.mark.integration


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app(engine=create_db_engine(":memory:")))


def test_get_returns_seeded_default_with_no_key(client: TestClient) -> None:
    body = client.get("/api/settings").json()
    assert body["model"] == "ollama:qwen3.6:27b"
    assert body["base_url"] == "http://localhost:11434/v1"
    assert body["api_key_set"] is False
    assert body["api_key_hint"] is None
    assert "api_key" not in body


def test_put_updates_model_and_masks_stored_key(client: TestClient) -> None:
    resp = client.put(
        "/api/settings",
        json={"model": "anthropic:claude-sonnet-5", "base_url": None, "api_key": "sk-secret99"},
    )
    body = resp.json()
    assert body["model"] == "anthropic:claude-sonnet-5"
    assert body["api_key_set"] is True
    assert body["api_key_hint"] == "st99"[-4:] or body["api_key_hint"] == "et99"
    assert "api_key" not in body
    # Sticky: a follow-up PUT without a key keeps it set.
    again = client.put(
        "/api/settings", json={"model": "anthropic:claude-sonnet-5", "base_url": None}
    ).json()
    assert again["api_key_set"] is True


def test_put_rejects_empty_model(client: TestClient) -> None:
    assert client.put("/api/settings", json={"model": "", "base_url": None}).status_code == 422
```

(Fix the hint assertion to the exact last-4 once implemented: for `sk-secret99` the last 4 are `t99` → 3 chars if key < 4? `sk-secret99` has 11 chars, last-4 = `it99`. Use `assert body["api_key_hint"] == "it99"`.)

- [ ] **Step 2: Run tests to verify they fail.**

Run: `uv run pytest tests/integration/test_settings_api.py -q -m integration`
Expected: FAIL — 404 (route not registered).

- [ ] **Step 3: Add the response/request models and the register function in `src/revalid/app.py`.** Near the other `*Out` models:

```python
class SettingsOut(BaseModel):
    """Public view of the model/provider setting; the key is write-only (ADR-0021)."""

    model_config = ConfigDict(protected_namespaces=())

    model: str
    base_url: str | None
    api_key_set: bool
    api_key_hint: str | None

    @classmethod
    def from_domain(cls, cfg: Settings) -> SettingsOut:
        """Build the masked view: the key becomes a boolean + last-4 hint only."""
        key = cfg.api_key or ""
        return cls(
            model=cfg.model,
            base_url=cfg.base_url,
            api_key_set=bool(key),
            api_key_hint=key[-4:] if key else None,
        )


class SettingsUpdateIn(BaseModel):
    """Settings update payload; a blank ``api_key`` keeps the stored one (ADR-0021)."""

    model_config = ConfigDict(protected_namespaces=())

    model: str = Field(min_length=1)
    base_url: str | None = None
    api_key: str | None = None
    clear_key: bool = False
```

(`ConfigDict` and `Field` must be imported in `app.py` — add to the pydantic import line if absent.)

Add the register function (mirrors the other `_register_*_routes` closure pattern):

```python
def _register_settings_routes(router: APIRouter, sessions: sessionmaker[Session]) -> None:
    """Register the ADR-0021 model/provider settings routes."""

    def get_session() -> Iterator[Session]:
        with sessions() as session:
            yield session

    SessionDep = Annotated[Session, Depends(get_session)]  # noqa: N806

    @router.get("/settings", response_model=SettingsOut)
    def read_settings(session: SessionDep) -> SettingsOut:
        """Return the current model/provider setting (key masked)."""
        return SettingsOut.from_domain(load_or_seed(session))

    @router.put("/settings", response_model=SettingsOut)
    def update_settings(body: SettingsUpdateIn, session: SessionDep) -> SettingsOut:
        """Persist a new model/provider setting; takes effect on the next agent build."""
        cfg = save(
            session,
            model=body.model,
            base_url=body.base_url,
            api_key=body.api_key,
            clear_key=body.clear_key,
        )
        return SettingsOut.from_domain(cfg)
```

Add `from revalid.settings import load_or_seed, save` (extend the Task 3 import). Register in `create_app` alongside the others:

```python
    _register_settings_routes(api, sessions)
```

- [ ] **Step 4: Run tests to verify they pass.**

Run: `uv run pytest tests/integration/test_settings_api.py -q -m integration`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit.**

```bash
git add src/revalid/app.py tests/integration/test_settings_api.py
git commit -m "feat(api): GET/PUT /api/settings with write-only masked key (ADR-0021)

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 5: Provider probe / model discovery (`POST /api/settings/probe`)

**Files:**
- Modify: `src/revalid/settings.py` (`ProbeResult`, `probe_provider`)
- Modify: `src/revalid/app.py` (`ProbeIn`, probe route)
- Test: `tests/unit/test_probe.py`, extend `tests/integration/test_settings_api.py`

**Interfaces:**
- Produces:
  - `settings.ProbeResult(BaseModel)`: `reachable: bool`, `models: tuple[str, ...] = ()`, `error: str | None = None`.
  - `settings.probe_provider(base_url: str | None, api_key: str | None = None, *, client: httpx.Client | None = None) -> ProbeResult`
  - `POST /api/settings/probe (ProbeIn) -> ProbeResult`
- Consumes: `httpx` (already a dependency).

- [ ] **Step 1: Write failing unit tests for `probe_provider` using a MockTransport.** Create `tests/unit/test_probe.py`:

```python
"""Unit tests for provider model discovery / connection probe (ADR-0021)."""

import httpx

from revalid.settings import probe_provider


def _client(handler: object) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))  # type: ignore[arg-type]


def test_lists_models_from_openai_compatible_endpoint() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/models")
        return httpx.Response(200, json={"data": [{"id": "qwen3.6:27b"}, {"id": "qwen3:14b"}]})

    result = probe_provider("http://h:11434/v1", None, client=_client(handler))
    assert result.reachable is True
    assert result.models == ("qwen3.6:27b", "qwen3:14b")
    assert result.error is None


def test_empty_base_url_is_a_clear_error_not_a_crash() -> None:
    result = probe_provider(None, None)
    assert result.reachable is False
    assert result.error is not None


def test_unreachable_endpoint_reports_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    result = probe_provider("http://h/v1", None, client=_client(handler))
    assert result.reachable is False
    assert "refused" in (result.error or "")
```

- [ ] **Step 2: Run tests to verify they fail.**

Run: `uv run pytest tests/unit/test_probe.py -q`
Expected: FAIL — `ImportError: cannot import name 'probe_provider'`.

- [ ] **Step 3: Add `ProbeResult` + `probe_provider` to `src/revalid/settings.py`.** Add `import httpx` and `from pydantic import BaseModel` at the top (with usage), then:

```python
class ProbeResult(BaseModel):
    """Outcome of a provider connection probe / model discovery (ADR-0021)."""

    reachable: bool
    models: tuple[str, ...] = ()
    error: str | None = None


def probe_provider(
    base_url: str | None,
    api_key: str | None = None,
    *,
    client: httpx.Client | None = None,
) -> ProbeResult:
    """Probe an OpenAI-compatible provider and list its models (ADR-0021).

    Hits ``{base_url}/models`` (e.g. Ollama's OpenAI-compatible endpoint). This
    deliberately bypasses the FR-06 allowlist: the LLM host is infrastructure the
    operator configures, not a pentest target (ADR-0008).

    Args:
        base_url: The provider base URL (must already include any ``/v1`` suffix).
        api_key: Optional bearer token for hosts that require one.
        client: Injectable HTTP client (tests pass a ``MockTransport`` client).

    Returns:
        A :class:`ProbeResult`; ``reachable`` is false with an ``error`` message
        on any failure (no exception escapes).
    """
    if not base_url:
        return ProbeResult(reachable=False, error="set a base URL to discover models")
    owns = client is None
    client = client or httpx.Client(timeout=5.0)
    try:
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        response = client.get(f"{base_url.rstrip('/')}/models", headers=headers)
        response.raise_for_status()
        data = response.json().get("data", [])
        models = tuple(
            str(item["id"]) for item in data if isinstance(item, dict) and "id" in item
        )
        return ProbeResult(reachable=True, models=models)
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        return ProbeResult(reachable=False, error=str(exc))
    finally:
        if owns:
            client.close()
```

- [ ] **Step 4: Add the probe route to `src/revalid/app.py`.** Add the request model near `SettingsUpdateIn`:

```python
class ProbeIn(BaseModel):
    """Probe request: which endpoint (and optional key) to discover models from."""

    base_url: str | None = None
    api_key: str | None = None
```

Inside `_register_settings_routes`, add (no session needed):

```python
    @router.post("/settings/probe", response_model=ProbeResult)
    def probe_settings(body: ProbeIn) -> ProbeResult:
        """Discover models / test reachability for a provider base URL (ADR-0021)."""
        return probe_provider(body.base_url, body.api_key)
```

Extend the settings import: `from revalid.settings import ProbeResult, load_or_seed, probe_provider, save`.

- [ ] **Step 5: Add an endpoint test for the probe.** Append to `tests/integration/test_settings_api.py`:

```python
def test_probe_endpoint_reports_unreachable_localhost(client: TestClient) -> None:
    # No Ollama in CI: the probe must return a structured "unreachable", never 500.
    body = client.post(
        "/api/settings/probe", json={"base_url": "http://127.0.0.1:1/v1"}
    ).json()
    assert body["reachable"] is False
    assert body["error"]
```

- [ ] **Step 6: Run tests to verify they pass.**

Run: `uv run pytest tests/unit/test_probe.py tests/integration/test_settings_api.py -q`
Expected: PASS.

- [ ] **Step 7: Commit.**

```bash
git add src/revalid/settings.py src/revalid/app.py tests/unit/test_probe.py tests/integration/test_settings_api.py
git commit -m "feat(api): POST /api/settings/probe — model discovery + connection test (ADR-0021)

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 6: Frontend API client, types, and hooks

**Files:**
- Modify: `frontend/src/api/types.ts`
- Modify: `frontend/src/api/client.ts`
- Modify: `frontend/src/hooks/queryKeys.ts`
- Create: `frontend/src/hooks/useSettings.ts`
- Test: `frontend/src/hooks/useSettings.test.tsx` (or client-level test)

**Interfaces:**
- Produces: `Settings`, `SettingsUpdate`, `ProbeResult`, `ProbeInput` types; `getSettings`, `updateSettings`, `probeProvider` client fns; `useSettings`, `useUpdateSettings`, `useProbeProvider` hooks; `queryKeys.settings`.

- [ ] **Step 1: Add types.** In `frontend/src/api/types.ts`:

```ts
export interface Settings {
  model: string;
  base_url: string | null;
  api_key_set: boolean;
  api_key_hint: string | null;
}

export interface SettingsUpdate {
  model: string;
  base_url: string | null;
  api_key?: string | null;
  clear_key?: boolean;
}

export interface ProbeInput {
  base_url: string | null;
  api_key?: string | null;
}

export interface ProbeResult {
  reachable: boolean;
  models: string[];
  error: string | null;
}
```

- [ ] **Step 2: Add client functions.** In `frontend/src/api/client.ts`, extend the type import and add a section:

```ts
// --- Settings ------------------------------------------------------------

export function getSettings(): Promise<Settings> {
  return request<Settings>("/settings");
}

export function updateSettings(body: SettingsUpdate): Promise<Settings> {
  return request<Settings>("/settings", jsonInit("PUT", body));
}

export function probeProvider(body: ProbeInput): Promise<ProbeResult> {
  return request<ProbeResult>("/settings/probe", jsonInit("POST", body));
}
```

Add `ProbeInput, ProbeResult, Settings, SettingsUpdate` to the `import type { … } from "./types";` list.

- [ ] **Step 3: Add the query key + hooks.** In `frontend/src/hooks/queryKeys.ts` add `settings: ["settings"] as const,` to the object. Create `frontend/src/hooks/useSettings.ts`:

```ts
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { getSettings, probeProvider, updateSettings } from "../api/client";
import type { ProbeInput, ProbeResult, Settings, SettingsUpdate } from "../api/types";
import { queryKeys } from "./queryKeys";

/** The current model/provider setting (key is masked). */
export function useSettings() {
  return useQuery({ queryKey: queryKeys.settings, queryFn: getSettings });
}

/** Persist a new setting; on success refresh the settings query. */
export function useUpdateSettings() {
  const client = useQueryClient();
  return useMutation<Settings, Error, SettingsUpdate>({
    mutationFn: updateSettings,
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: queryKeys.settings });
    },
  });
}

/** Probe a provider base URL for reachability + model list (the Test button). */
export function useProbeProvider() {
  return useMutation<ProbeResult, Error, ProbeInput>({ mutationFn: probeProvider });
}
```

- [ ] **Step 4: Write a hook/client test.** Create `frontend/src/hooks/useSettings.test.tsx` (mirror the existing `test/utils` render helper + fetch mocking style used in `NewReport.test.tsx`):

```tsx
import { waitFor } from "@testing-library/react";
import { renderHook } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { useSettings } from "./useSettings";

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

afterEach(() => vi.restoreAllMocks());

test("useSettings fetches the masked setting", async () => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(
      JSON.stringify({ model: "ollama:qwen3.6:27b", base_url: "http://h/v1", api_key_set: false, api_key_hint: null }),
      { status: 200, headers: { "Content-Type": "application/json" } },
    ),
  );
  const { result } = renderHook(() => useSettings(), { wrapper });
  await waitFor(() => expect(result.current.data?.model).toBe("ollama:qwen3.6:27b"));
});
```

- [ ] **Step 5: Run the frontend tests + typecheck.**

Run: `cd frontend && npm run test -- --run useSettings && npm run typecheck`
Expected: PASS. (If the repo's test util already exposes a `renderWithClient`, use it instead of the inline wrapper to match convention.)

- [ ] **Step 6: Commit.**

```bash
git add frontend/src/api/types.ts frontend/src/api/client.ts frontend/src/hooks/queryKeys.ts frontend/src/hooks/useSettings.ts frontend/src/hooks/useSettings.test.tsx
git commit -m "feat(ui): settings API client, types, and react-query hooks (ADR-0021)

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 7: Settings route UI + Sidebar link

**Files:**
- Create: `frontend/src/routes/Settings.tsx`
- Modify: `frontend/src/App.tsx` (route)
- Modify: `frontend/src/components/Sidebar.tsx` (nav link + gear icon)
- Test: `frontend/src/routes/Settings.test.tsx`

**Interfaces:**
- Consumes: `useSettings`, `useUpdateSettings`, `useProbeProvider` (Task 6), existing `ui/Button`, `ui/Panel`/`Eyebrow`, `lib/format` `errorMessage`.

- [ ] **Step 1: Write a failing component test.** Create `frontend/src/routes/Settings.test.tsx`:

```tsx
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { renderRoute } from "../test/utils"; // use the repo's helper; see NewReport.test.tsx
import Settings from "./Settings";

afterEach(() => vi.restoreAllMocks());

test("renders the current model and saves an edit", async () => {
  const fetchMock = vi
    .spyOn(globalThis, "fetch")
    .mockResolvedValueOnce(
      new Response(
        JSON.stringify({ model: "ollama:qwen3.6:27b", base_url: "http://h/v1", api_key_set: false, api_key_hint: null }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    )
    .mockResolvedValueOnce(
      new Response(
        JSON.stringify({ model: "ollama:qwen3:14b", base_url: "http://h/v1", api_key_set: false, api_key_hint: null }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );

  renderRoute(<Settings />, "/settings");
  const modelInput = await screen.findByLabelText(/model/i);
  fireEvent.change(modelInput, { target: { value: "ollama:qwen3:14b" } });
  fireEvent.click(screen.getByRole("button", { name: /save/i }));
  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
  const putCall = fetchMock.mock.calls[1];
  expect(String(putCall[0])).toContain("/api/settings");
  expect((putCall[1] as RequestInit).method).toBe("PUT");
});
```

(Adapt `renderRoute` to the actual helper name/signature in `frontend/src/test/utils.tsx`; `NewReport.test.tsx` shows the exact call.)

- [ ] **Step 2: Run to verify it fails.**

Run: `cd frontend && npm run test -- --run Settings`
Expected: FAIL — cannot resolve `./Settings`.

- [ ] **Step 3: Create `frontend/src/routes/Settings.tsx`.** Model field is an `<input list=…>` with a `<datalist>` populated from probe results; a Test button calls the probe; the key field is a password input showing the masked hint when a key is set. Match the existing route layout/`ui` primitives (see `NewReport.tsx`).

```tsx
import { useEffect, useState } from "react";

import type { ProbeResult } from "../api/types";
import { Button } from "../components/ui/Button";
import { errorMessage } from "../lib/format";
import { useProbeProvider, useSettings, useUpdateSettings } from "../hooks/useSettings";

const KNOWN_MODELS = ["ollama:qwen3.6:27b", "ollama:qwen3:14b", "ollama:qwen3.5:9b", "anthropic:claude-sonnet-5"];

export default function Settings() {
  const settings = useSettings();
  const update = useUpdateSettings();
  const probe = useProbeProvider();

  const [model, setModel] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [probed, setProbed] = useState<ProbeResult | null>(null);

  useEffect(() => {
    if (settings.data) {
      setModel(settings.data.model);
      setBaseUrl(settings.data.base_url ?? "");
    }
  }, [settings.data]);

  const discovered = probed?.models.map((m) => (baseUrl && m.includes(":") ? m : `ollama:${m}`)) ?? [];
  const options = [...new Set([...KNOWN_MODELS, ...discovered])];

  return (
    <section className="mx-auto max-w-2xl space-y-6 p-4">
      <header>
        <h1 className="font-mono text-lg text-fg">Model &amp; provider</h1>
        <p className="text-[13px] text-dim">
          The active LLM backend. Changes persist and apply to the next extraction or plan.
        </p>
      </header>

      <label className="block text-[13px]">
        <span className="text-dim">Model</span>
        <input
          list="model-options"
          value={model}
          onChange={(e) => setModel(e.target.value)}
          className="mt-1 w-full rounded-lg border border-line bg-panel-2 px-3 py-2 font-mono text-fg"
        />
        <datalist id="model-options">
          {options.map((m) => (
            <option key={m} value={m} />
          ))}
        </datalist>
      </label>

      <label className="block text-[13px]">
        <span className="text-dim">Base URL</span>
        <input
          value={baseUrl}
          onChange={(e) => setBaseUrl(e.target.value)}
          placeholder="http://localhost:11434/v1"
          className="mt-1 w-full rounded-lg border border-line bg-panel-2 px-3 py-2 font-mono text-fg"
        />
      </label>

      <label className="block text-[13px]">
        <span className="text-dim">
          API key {settings.data?.api_key_set ? `(set ··${settings.data.api_key_hint ?? ""})` : "(none)"}
        </span>
        <input
          type="password"
          value={apiKey}
          onChange={(e) => setApiKey(e.target.value)}
          placeholder={settings.data?.api_key_set ? "leave blank to keep" : "provider key (optional)"}
          className="mt-1 w-full rounded-lg border border-line bg-panel-2 px-3 py-2 font-mono text-fg"
        />
      </label>

      <div className="flex items-center gap-3">
        <Button
          variant="secondary"
          onClick={() => probe.mutate({ base_url: baseUrl || null, api_key: apiKey || null }, { onSuccess: setProbed })}
          disabled={probe.isPending}
        >
          {probe.isPending ? "Testing…" : "Test connection"}
        </Button>
        <Button
          onClick={() =>
            update.mutate({ model, base_url: baseUrl || null, api_key: apiKey || null })
          }
          disabled={update.isPending || !model}
        >
          {update.isPending ? "Saving…" : "Save"}
        </Button>
      </div>

      {probed && (
        <p className={`text-[13px] ${probed.reachable ? "text-ok" : "text-danger"}`} role="status">
          {probed.reachable
            ? `Reachable — ${String(probed.models.length)} models`
            : `Unreachable — ${probed.error ?? "error"}`}
        </p>
      )}
      {update.isSuccess && <p className="text-[13px] text-ok">Saved.</p>}
      {update.isError && <p className="text-[13px] text-danger">{errorMessage(update.error)}</p>}
    </section>
  );
}
```

(Verify the exact `Button` variant names and `errorMessage` signature against `components/ui/Button.tsx` and `lib/format.ts`; adjust class tokens to the ones the design system actually defines.)

- [ ] **Step 4: Register the route.** In `frontend/src/App.tsx` add the import and a route inside `<Routes>`:

```tsx
import Settings from "./routes/Settings";
```

```tsx
            <Route path="/settings" element={<Settings />} />
```

- [ ] **Step 5: Add the Sidebar nav link.** In `frontend/src/components/Sidebar.tsx`, add a gear icon component and a `NavLink to="/settings"` in the "Navigate" group under Overview:

```tsx
function SettingsIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 16 16" fill="none" aria-hidden="true" className="shrink-0">
      <circle cx="8" cy="8" r="2.2" stroke="currentColor" strokeWidth="1.3" />
      <path
        d="M8 1.5v1.6M8 12.9v1.6M14.5 8h-1.6M3.1 8H1.5M12.6 3.4l-1.1 1.1M4.5 11.5l-1.1 1.1M12.6 12.6l-1.1-1.1M4.5 4.5 3.4 3.4"
        stroke="currentColor"
        strokeWidth="1.3"
        strokeLinecap="round"
      />
    </svg>
  );
}
```

```tsx
            <NavLink to="/settings" onClick={onNavigate} className={navItemClass}>
              <SettingsIcon />
              Settings
            </NavLink>
```

- [ ] **Step 6: Run the frontend gates.**

Run: `cd frontend && npm run test -- --run && npm run lint && npm run typecheck && npm run build`
Expected: PASS all four (coverage floor holds).

- [ ] **Step 7: Commit.**

```bash
git add frontend/src/routes/Settings.tsx frontend/src/routes/Settings.test.tsx frontend/src/App.tsx frontend/src/components/Sidebar.tsx
git commit -m "feat(ui): /settings route with discovery, test-connection, and Sidebar link (ADR-0021)

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 8: Demo, docs, SRS, and roadmap

**Files:**
- Create: `scripts/demo/settings.py`
- Modify: `Makefile` (`demo-settings` target)
- Modify: `docs/requirements/srs.md` (FR-13 acceptance criterion)
- Modify: `.claude/skills/run/SKILL.md` (env-seeds-not-overrides note)
- Modify: `docs/roadmap.md` (M5 state line)

**Interfaces:** consumes everything above; no new runtime code.

- [ ] **Step 1: Write the offline demo script.** Create `scripts/demo/settings.py` (mirrors the other `scripts/demo/*.py` structure — in-memory DB, no network):

```python
"""Offline demo: the persisted setting drives the next agent build (ADR-0021).

Run: ``make demo-settings``. Shows seed → change model → build_model picking it
up, with no network calls.
"""

from __future__ import annotations

from revalid.db import create_db_engine, session_factory
from revalid.llm import build_model
from revalid.settings import load_or_seed, save


def main() -> None:
    engine = create_db_engine(":memory:")
    with session_factory(engine)() as session:
        seeded = load_or_seed(session)
        print(f"seeded default : {seeded.model}  base_url={seeded.base_url}")
        changed = save(session, model="ollama:qwen3:14b", base_url=seeded.base_url, api_key=None)
        print(f"after change   : {changed.model}")
        model = build_model(changed)
        print(f"built model    : {type(model).__name__} -> {getattr(model, 'model_name', model)}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Add the Makefile target.** Under the other `demo-*` targets:

```makefile
demo-settings: ## ADR-0021: persisted model/provider setting drives the next agent build (offline)
	uv run python -m scripts.demo.settings
```

(Match the exact invocation style the neighbouring `demo-*` targets use — `python scripts/demo/...` vs `python -m ...`.)

- [ ] **Step 3: Run the demo.**

Run: `make demo-settings`
Expected output (approximately):
```
seeded default : ollama:qwen3.6:27b  base_url=http://localhost:11434/v1
after change   : ollama:qwen3:14b
built model    : OpenAIChatModel -> qwen3:14b
```

- [ ] **Step 4: Add the FR-13 acceptance criterion.** In `docs/requirements/srs.md`, under FR-13 "Acceptance criteria", add:

```markdown
  - [x] The active backend is a **user-editable, DB-persisted setting** changeable at runtime (env vars seed a fresh DB; the stored row is then authoritative). Model discovery + a connection test surface in the SPA `/settings` view. (ADR-0021)
```

- [ ] **Step 5: Update the run skill doc.** In `.claude/skills/run/SKILL.md`, adjust the `REVALID_LLM_MODEL` guidance to note it **seeds a fresh DB** rather than overrides a configured setting, and point at `/settings` for runtime changes. (Keep it to one or two sentences next to the existing env-var mention.)

- [ ] **Step 6: Update the roadmap.** In `docs/roadmap.md`, add a `2026-07-15 (M5)` state line summarising ADR-0021 shipped (DB-persisted runtime model/provider setting, local-first default, `/settings` view, probe/discovery), and move the "ADR-0021" bullet out of the "Remaining epic" list.

- [ ] **Step 7: Full repo gates before PR.**

Run:
```bash
uv run mypy --strict src tests
uv run ruff check src tests && uv run ruff format --check src tests
uv run pytest -q
cd frontend && npm run lint && npm run typecheck && npm run build && npm run test -- --run
```
Expected: all green; backend coverage ≥ 80%.

- [ ] **Step 8: Commit.**

```bash
git add scripts/demo/settings.py Makefile docs/requirements/srs.md .claude/skills/run/SKILL.md docs/roadmap.md
git commit -m "docs(settings): demo-settings, FR-13 AC, run-doc precedence note, roadmap (ADR-0021)

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage** (against ADR-0021):
- DB-primary, env-seeds precedence → Task 1 (`load_or_seed`, `_seed_from_env`).
- `{model, base_url, api_key}` fields, key write-only/masked → Task 1 (schema) + Task 4 (`SettingsOut` masking).
- Local-first default → Task 2 (`DEFAULT_MODEL`/`DEFAULT_BASE_URL`) + Task 1 seed test.
- `build_model` provider construction → Task 2.
- Composition root in agent DI, no-restart → Task 3 (`get_settings_dep` via `app.state.sessions`).
- `GET`/`PUT /api/settings` → Task 4. `POST /api/settings/probe` (discovery = test, allowlist-bypass) → Task 5.
- `/settings` SPA route + Sidebar + dropdown/test/write-only key → Tasks 6–7.
- FR-13 enhancement, not a new FR → Task 8 (SRS AC).

**Placeholder scan:** demo target invocation style, `renderRoute`/test-util helper name, `Button` variant names, and design-system class tokens are the only spots flagged "verify against the actual file" — each names the concrete file to check and the fallback; no blank TBDs.

**Type consistency:** `Settings(model, base_url, api_key)` used identically across `domain`, `db`, `settings`, `llm.build_model`, `app`. `ProbeResult(reachable, models, error)` identical in `settings.py` and the TS `ProbeResult`. `save(...)` keyword args match every call site (Tasks 1, 4, 5, 8). `get_settings_dep`/`SettingsDep` defined in Task 3 before their use in the rewritten factories.

**Known accepted consequence:** `ReportRecord.model` lineage for a base-URL-built model records the bare model tag (e.g. `qwen3.6:27b`) via `agent_model_name`, dropping the `ollama:` prefix. Honest and non-breaking (existing tests inject stand-in agents); noted in ADR-0021.
