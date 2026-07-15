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
