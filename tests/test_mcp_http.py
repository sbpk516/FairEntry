from fairentry.mcp import http_server


class Headers(dict):
    def get(self, key, default=None):
        return super().get(key, default)


def test_http_auth_allows_when_no_token(monkeypatch):
    monkeypatch.delenv("FAIRENTRY_MCP_TOKEN", raising=False)

    assert http_server.is_authorized(Headers()) is True


def test_http_auth_requires_bearer_token(monkeypatch):
    monkeypatch.setenv("FAIRENTRY_MCP_TOKEN", "secret")

    assert http_server.is_authorized(Headers()) is False
    assert http_server.is_authorized(Headers({"Authorization": "Bearer secret"})) is True
    assert http_server.is_authorized(Headers({"Authorization": "Bearer wrong"})) is False
