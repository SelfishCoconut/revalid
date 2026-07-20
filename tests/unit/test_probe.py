"""Unit tests for provider model discovery / connection probe (ADR-0021)."""

import httpx

from revalid.settings import (
    ANTHROPIC_MODELS_URL,
    ANTHROPIC_VERSION,
    discover_models,
    probe_anthropic,
    probe_provider,
)


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


def test_bare_list_body_is_reachable_with_no_models_not_a_crash() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=["qwen3.6:27b"])  # non-object top-level

    result = probe_provider("http://h/v1", None, client=_client(handler))
    assert result.reachable is True
    assert result.models == ()
    assert result.error is None


def test_null_body_is_reachable_with_no_models_not_a_crash() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        # httpx.Response(json=None) would send an empty body, not a literal
        # `null`; build the body directly to exercise the real null case.
        return httpx.Response(200, content=b"null", headers={"content-type": "application/json"})

    result = probe_provider("http://h/v1", None, client=_client(handler))
    assert result.reachable is True
    assert result.models == ()
    assert result.error is None


def test_anthropic_lists_models_with_its_own_auth_headers() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == ANTHROPIC_MODELS_URL
        assert request.headers["x-api-key"] == "sk-ant-xyz"
        assert request.headers["anthropic-version"] == ANTHROPIC_VERSION
        assert "authorization" not in request.headers
        return httpx.Response(
            200, json={"data": [{"id": "claude-sonnet-5"}, {"id": "claude-opus-4-8"}]}
        )

    result = probe_anthropic("sk-ant-xyz", client=_client(handler))
    assert result.reachable is True
    assert result.models == ("claude-sonnet-5", "claude-opus-4-8")
    assert result.error is None


def test_anthropic_without_a_key_is_a_clear_error_not_a_call() -> None:
    result = probe_anthropic(None)
    assert result.reachable is False
    assert "key" in (result.error or "").lower()


def test_anthropic_http_error_reports_unreachable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "invalid key"}})

    result = probe_anthropic("sk-bad", client=_client(handler))
    assert result.reachable is False
    assert result.error is not None


def test_discover_dispatches_anthropic_to_the_anthropic_probe() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == ANTHROPIC_MODELS_URL
        return httpx.Response(200, json={"data": [{"id": "claude-sonnet-5"}]})

    result = discover_models("anthropic", None, "sk-ant-xyz", client=_client(handler))
    assert result.reachable is True
    assert result.models == ("claude-sonnet-5",)


def test_discover_dispatches_others_to_the_openai_compatible_probe() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/models")
        assert request.headers.get("authorization") == "Bearer sk-oai"
        return httpx.Response(200, json={"data": [{"id": "gpt-5.2"}]})

    result = discover_models(
        "openai", "https://api.openai.com/v1", "sk-oai", client=_client(handler)
    )
    assert result.reachable is True
    assert result.models == ("gpt-5.2",)
