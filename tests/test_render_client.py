import requests

import provisioning.render_client as render_client
from provisioning.render_client import (
    RenderProvisioningError,
    create_web_service,
)


class FakeResponse:

    def __init__(self, status_code, json_body=None, text=""):
        self.status_code = status_code
        self._json_body = json_body or {}
        self.text = text or str(json_body)

    def json(self):
        return self._json_body


def _configure(monkeypatch, api_key="test-key", owner_id="usr-abc123"):
    monkeypatch.setattr(render_client, "RENDER_API_KEY", api_key)
    monkeypatch.setattr(render_client, "RENDER_OWNER_ID", owner_id)


SUCCESS_BODY = {
    "service": {
        "id": "srv-xyz789",
        "name": "whatspilot-business_003",
        "dashboardUrl": "https://dashboard.render.com/web/srv-xyz789",
        "serviceDetails": {
            "url": "https://whatspilot-business-003.onrender.com",
        },
    },
    "deployId": "dep-abc111",
}


# --------------------------------------------------------
# create_web_service
# --------------------------------------------------------

def test_create_web_service_success(monkeypatch):

    _configure(monkeypatch)

    captured = {}

    def fake_post(url, headers, json, timeout):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        return FakeResponse(201, SUCCESS_BODY)

    monkeypatch.setattr(render_client.requests, "post", fake_post)

    result = create_web_service(
        "whatspilot-business_003",
        "https://github.com/samramkumarqa/whatspilot-business_003",
        env_vars=[{"key": "BUSINESS_ID", "value": "business_003"}],
    )

    assert result == {
        "id": "srv-xyz789",
        "name": "whatspilot-business_003",
        "dashboard_url": "https://dashboard.render.com/web/srv-xyz789",
        "url": "https://whatspilot-business-003.onrender.com",
        "deploy_id": "dep-abc111",
    }

    assert captured["url"] == "https://api.render.com/v1/services"
    assert captured["headers"]["Authorization"] == "Bearer test-key"

    sent = captured["json"]
    assert sent["type"] == "web_service"
    assert sent["name"] == "whatspilot-business_003"
    assert sent["ownerId"] == "usr-abc123"
    assert sent["repo"] == "https://github.com/samramkumarqa/whatspilot-business_003"
    assert sent["branch"] == "main"
    assert sent["autoDeploy"] == "yes"
    assert sent["envVars"] == [{"key": "BUSINESS_ID", "value": "business_003"}]
    assert sent["serviceDetails"]["runtime"] == "python"
    assert sent["serviceDetails"]["plan"] == "free"
    assert sent["serviceDetails"]["region"] == "oregon"
    assert sent["serviceDetails"]["healthCheckPath"] == "/health"
    assert sent["serviceDetails"]["envSpecificDetails"] == {
        "buildCommand": "pip install -r requirements.txt",
        "startCommand": (
            'uvicorn main:app --host 0.0.0.0 --port $PORT '
            '--proxy-headers --forwarded-allow-ips="*"'
        ),
    }


def test_create_web_service_defaults_to_free_plan_even_if_omitted(monkeypatch):
    """
    Render's own API defaults an omitted `plan` to "starter" (paid) - we
    always send an explicit plan, and it must default to "free" here so
    a caller who forgets to pass one doesn't accidentally get billed.
    """

    _configure(monkeypatch)

    captured = {}

    def fake_post(url, headers, json, timeout):
        captured["json"] = json
        return FakeResponse(201, SUCCESS_BODY)

    monkeypatch.setattr(render_client.requests, "post", fake_post)

    create_web_service(
        "x",
        "https://github.com/samramkumarqa/x",
        env_vars=[],
    )

    assert captured["json"]["serviceDetails"]["plan"] == "free"


def test_create_web_service_supports_generate_value_env_vars(monkeypatch):

    _configure(monkeypatch)

    captured = {}

    def fake_post(url, headers, json, timeout):
        captured["json"] = json
        return FakeResponse(201, SUCCESS_BODY)

    monkeypatch.setattr(render_client.requests, "post", fake_post)

    create_web_service(
        "x",
        "https://github.com/samramkumarqa/x",
        env_vars=[{"key": "SESSION_SECRET_KEY", "generateValue": True}],
    )

    assert captured["json"]["envVars"] == [
        {"key": "SESSION_SECRET_KEY", "generateValue": True}
    ]


def test_create_web_service_missing_api_key(monkeypatch):

    _configure(monkeypatch, api_key=None)

    def fake_post(*args, **kwargs):
        raise AssertionError("should not make a network call without an API key")

    monkeypatch.setattr(render_client.requests, "post", fake_post)

    try:
        create_web_service("x", "https://github.com/samramkumarqa/x", env_vars=[])
        assert False, "expected RenderProvisioningError"
    except RenderProvisioningError as e:
        assert "RENDER_API_KEY" in str(e)


def test_create_web_service_missing_owner_id(monkeypatch):

    _configure(monkeypatch, owner_id=None)

    def fake_post(*args, **kwargs):
        raise AssertionError("should not make a network call without an owner id")

    monkeypatch.setattr(render_client.requests, "post", fake_post)

    try:
        create_web_service("x", "https://github.com/samramkumarqa/x", env_vars=[])
        assert False, "expected RenderProvisioningError"
    except RenderProvisioningError as e:
        assert "RENDER_OWNER_ID" in str(e)


def test_create_web_service_401(monkeypatch):

    _configure(monkeypatch)
    monkeypatch.setattr(
        render_client.requests, "post", lambda *a, **k: FakeResponse(401, {})
    )

    try:
        create_web_service("x", "https://github.com/samramkumarqa/x", env_vars=[])
        assert False, "expected RenderProvisioningError"
    except RenderProvisioningError as e:
        assert "invalid or expired" in str(e)


def test_create_web_service_402(monkeypatch):

    _configure(monkeypatch)
    monkeypatch.setattr(
        render_client.requests, "post", lambda *a, **k: FakeResponse(402, {})
    )

    try:
        create_web_service("x", "https://github.com/samramkumarqa/x", env_vars=[])
        assert False, "expected RenderProvisioningError"
    except RenderProvisioningError as e:
        assert "payment information" in str(e)


def test_create_web_service_404(monkeypatch):

    _configure(monkeypatch)
    monkeypatch.setattr(
        render_client.requests, "post", lambda *a, **k: FakeResponse(404, {})
    )

    try:
        create_web_service(
            "x", "https://github.com/samramkumarqa/missing-repo", env_vars=[]
        )
        assert False, "expected RenderProvisioningError"
    except RenderProvisioningError as e:
        assert "missing-repo" in str(e)
        assert "GitHub integration" in str(e)


def test_create_web_service_409(monkeypatch):

    _configure(monkeypatch)
    monkeypatch.setattr(
        render_client.requests, "post", lambda *a, **k: FakeResponse(409, {})
    )

    try:
        create_web_service(
            "whatspilot-business_002",
            "https://github.com/samramkumarqa/x",
            env_vars=[],
        )
        assert False, "expected RenderProvisioningError"
    except RenderProvisioningError as e:
        assert "already exists" in str(e)


def test_create_web_service_429(monkeypatch):

    _configure(monkeypatch)
    monkeypatch.setattr(
        render_client.requests, "post", lambda *a, **k: FakeResponse(429, {})
    )

    try:
        create_web_service("x", "https://github.com/samramkumarqa/x", env_vars=[])
        assert False, "expected RenderProvisioningError"
    except RenderProvisioningError as e:
        assert "rate-limited" in str(e)


def test_create_web_service_unexpected_status(monkeypatch):

    _configure(monkeypatch)
    monkeypatch.setattr(
        render_client.requests,
        "post",
        lambda *a, **k: FakeResponse(500, {}, text="internal error"),
    )

    try:
        create_web_service("x", "https://github.com/samramkumarqa/x", env_vars=[])
        assert False, "expected RenderProvisioningError"
    except RenderProvisioningError as e:
        assert "Unexpected response" in str(e)


def test_create_web_service_network_error(monkeypatch):

    _configure(monkeypatch)

    def fake_post(*args, **kwargs):
        raise requests.ConnectionError("boom")

    monkeypatch.setattr(render_client.requests, "post", fake_post)

    try:
        create_web_service("x", "https://github.com/samramkumarqa/x", env_vars=[])
        assert False, "expected RenderProvisioningError"
    except RenderProvisioningError as e:
        assert "Could not reach Render" in str(e)
