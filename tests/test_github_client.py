import requests

import provisioning.github_client as github_client
from provisioning.github_client import (
    GitHubProvisioningError,
    create_repo_from_template,
    wait_for_repo_ready,
)


class FakeResponse:

    def __init__(self, status_code, json_body=None, text=""):
        self.status_code = status_code
        self._json_body = json_body or {}
        self.text = text or str(json_body)

    def json(self):
        return self._json_body


def _configure(monkeypatch, token="test-token", owner="samramkumarqa"):
    monkeypatch.setattr(github_client, "GITHUB_TOKEN", token)
    monkeypatch.setattr(github_client, "GITHUB_OWNER", owner)
    monkeypatch.setattr(
        github_client, "GITHUB_TEMPLATE_REPO", "whatspilot-business-template"
    )


# --------------------------------------------------------
# create_repo_from_template
# --------------------------------------------------------

def test_create_repo_from_template_success(monkeypatch):

    _configure(monkeypatch)

    captured = {}

    def fake_post(url, headers, json, timeout):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        return FakeResponse(
            201,
            {
                "full_name": "samramkumarqa/whatspilot-business_003",
                "html_url": "https://github.com/samramkumarqa/whatspilot-business_003",
                "clone_url": "https://github.com/samramkumarqa/whatspilot-business_003.git",
                "default_branch": "main",
            },
        )

    monkeypatch.setattr(github_client.requests, "post", fake_post)

    result = create_repo_from_template("whatspilot-business_003")

    assert result == {
        "full_name": "samramkumarqa/whatspilot-business_003",
        "html_url": "https://github.com/samramkumarqa/whatspilot-business_003",
        "clone_url": "https://github.com/samramkumarqa/whatspilot-business_003.git",
        "default_branch": "main",
    }

    assert captured["url"] == (
        "https://api.github.com/repos/samramkumarqa/"
        "whatspilot-business-template/generate"
    )
    assert captured["json"] == {
        "owner": "samramkumarqa",
        "name": "whatspilot-business_003",
        "private": True,
    }
    assert captured["headers"]["Authorization"] == "Bearer test-token"


def test_create_repo_from_template_includes_description(monkeypatch):

    _configure(monkeypatch)

    captured = {}

    def fake_post(url, headers, json, timeout):
        captured["json"] = json
        return FakeResponse(201, {"full_name": "samramkumarqa/x"})

    monkeypatch.setattr(github_client.requests, "post", fake_post)

    create_repo_from_template("x", description="Test business repo")

    assert captured["json"]["description"] == "Test business repo"


def test_create_repo_from_template_missing_token(monkeypatch):

    _configure(monkeypatch, token=None)

    def fake_post(*args, **kwargs):
        raise AssertionError("should not make a network call without a token")

    monkeypatch.setattr(github_client.requests, "post", fake_post)

    try:
        create_repo_from_template("whatever")
        assert False, "expected GitHubProvisioningError"
    except GitHubProvisioningError as e:
        assert "GITHUB_TOKEN" in str(e)


def test_create_repo_from_template_missing_owner(monkeypatch):

    _configure(monkeypatch, owner=None)

    def fake_post(*args, **kwargs):
        raise AssertionError("should not make a network call without an owner")

    monkeypatch.setattr(github_client.requests, "post", fake_post)

    try:
        create_repo_from_template("whatever")
        assert False, "expected GitHubProvisioningError"
    except GitHubProvisioningError as e:
        assert "GITHUB_OWNER" in str(e)


def test_create_repo_from_template_name_collision(monkeypatch):

    _configure(monkeypatch)

    monkeypatch.setattr(
        github_client.requests,
        "post",
        lambda *a, **k: FakeResponse(
            422,
            {"errors": [{"message": "name already exists on this account"}]},
        ),
    )

    try:
        create_repo_from_template("whatspilot-business_002")
        assert False, "expected GitHubProvisioningError"
    except GitHubProvisioningError as e:
        assert "already exists" in str(e)


def test_create_repo_from_template_other_422(monkeypatch):

    _configure(monkeypatch)

    monkeypatch.setattr(
        github_client.requests,
        "post",
        lambda *a, **k: FakeResponse(
            422,
            {"errors": [{"message": "invalid field 'name'"}]},
        ),
    )

    try:
        create_repo_from_template("bad name")
        assert False, "expected GitHubProvisioningError"
    except GitHubProvisioningError as e:
        assert "rejected the repo creation request" in str(e)


def test_create_repo_from_template_401(monkeypatch):

    _configure(monkeypatch)
    monkeypatch.setattr(
        github_client.requests, "post", lambda *a, **k: FakeResponse(401, {})
    )

    try:
        create_repo_from_template("x")
        assert False, "expected GitHubProvisioningError"
    except GitHubProvisioningError as e:
        assert "invalid or expired" in str(e)


def test_create_repo_from_template_403(monkeypatch):

    _configure(monkeypatch)
    monkeypatch.setattr(
        github_client.requests, "post", lambda *a, **k: FakeResponse(403, {})
    )

    try:
        create_repo_from_template("x")
        assert False, "expected GitHubProvisioningError"
    except GitHubProvisioningError as e:
        assert "doesn't have permission" in str(e)


def test_create_repo_from_template_404(monkeypatch):

    _configure(monkeypatch)
    monkeypatch.setattr(
        github_client.requests, "post", lambda *a, **k: FakeResponse(404, {})
    )

    try:
        create_repo_from_template("x")
        assert False, "expected GitHubProvisioningError"
    except GitHubProvisioningError as e:
        assert "wasn't found" in str(e)


def test_create_repo_from_template_unexpected_status(monkeypatch):

    _configure(monkeypatch)
    monkeypatch.setattr(
        github_client.requests,
        "post",
        lambda *a, **k: FakeResponse(500, {}, text="internal error"),
    )

    try:
        create_repo_from_template("x")
        assert False, "expected GitHubProvisioningError"
    except GitHubProvisioningError as e:
        assert "Unexpected response" in str(e)


def test_create_repo_from_template_network_error(monkeypatch):

    _configure(monkeypatch)

    def fake_post(*args, **kwargs):
        raise requests.ConnectionError("boom")

    monkeypatch.setattr(github_client.requests, "post", fake_post)

    try:
        create_repo_from_template("x")
        assert False, "expected GitHubProvisioningError"
    except GitHubProvisioningError as e:
        assert "Could not reach GitHub" in str(e)


# --------------------------------------------------------
# wait_for_repo_ready
# --------------------------------------------------------

def test_wait_for_repo_ready_succeeds_immediately(monkeypatch):

    _configure(monkeypatch)
    monkeypatch.setattr(github_client.time, "sleep", lambda s: None)

    monkeypatch.setattr(
        github_client.requests,
        "get",
        lambda *a, **k: FakeResponse(200, {"size": 42}),
    )

    assert wait_for_repo_ready("samramkumarqa/whatspilot-business_003") is True


def test_wait_for_repo_ready_times_out(monkeypatch):

    _configure(monkeypatch)
    monkeypatch.setattr(github_client.time, "sleep", lambda s: None)

    monkeypatch.setattr(
        github_client.requests,
        "get",
        lambda *a, **k: FakeResponse(200, {"size": 0}),
    )

    assert wait_for_repo_ready("samramkumarqa/x", attempts=3) is False


def test_wait_for_repo_ready_retries_past_network_errors(monkeypatch):

    _configure(monkeypatch)
    monkeypatch.setattr(github_client.time, "sleep", lambda s: None)

    calls = {"n": 0}

    def flaky_get(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] < 2:
            raise requests.ConnectionError("flaky")
        return FakeResponse(200, {"size": 10})

    monkeypatch.setattr(github_client.requests, "get", flaky_get)

    assert wait_for_repo_ready("samramkumarqa/x", attempts=5) is True
    assert calls["n"] == 2
