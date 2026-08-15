"""
Tests for provisioning/orchestrator.py's provision_business() - the glue
between github_client.py and render_client.py that actually gets called
from api/businesses.py's create_business()/provision_business_route().

Both clients are mocked here (see test_github_client.py/test_render_client.py
for their own direct HTTP-mocked coverage) - this file is about the
orchestration logic itself: call order, what gets recorded on failure at
each step, and that a failure never raises out of provision_business().
"""

import provisioning.orchestrator as orchestrator
from crm.customer_mapping import register_business, list_businesses
from provisioning.github_client import GitHubProvisioningError
from provisioning.render_client import RenderProvisioningError


FAKE_REPO = {
    "full_name": "samramkumarqa/whatspilot-business_001",
    "html_url": "https://github.com/samramkumarqa/whatspilot-business_001",
    "clone_url": "https://github.com/samramkumarqa/whatspilot-business_001.git",
    "default_branch": "main",
}

FAKE_SERVICE = {
    "id": "srv-abc123",
    "name": "whatspilot-business_001",
    "dashboard_url": "https://dashboard.render.com/web/srv-abc123",
    "url": "https://whatspilot-business_001.onrender.com",
    "deploy_id": "dep-xyz789",
}


def test_provision_business_success_updates_row(isolated_db, monkeypatch):

    register_business("u1", "+14155550000")

    monkeypatch.setattr(
        orchestrator, "create_repo_from_template", lambda *a, **k: FAKE_REPO
    )
    monkeypatch.setattr(orchestrator, "wait_for_repo_ready", lambda *a, **k: True)
    monkeypatch.setattr(
        orchestrator, "create_web_service", lambda *a, **k: FAKE_SERVICE
    )

    result = orchestrator.provision_business("u1", "business_001")

    assert result["provisioning_status"] == "live"
    assert result["provisioning_error"] is None
    assert result["github_repo_url"] == FAKE_REPO["html_url"]
    assert result["render_service_id"] == FAKE_SERVICE["id"]
    assert result["render_service_url"] == FAKE_SERVICE["url"]

    row = next(b for b in list_businesses() if b["user_id"] == "u1")
    assert row["provisioning_status"] == "live"
    assert row["github_repo_url"] == FAKE_REPO["html_url"]
    assert row["render_service_url"] == FAKE_SERVICE["url"]


def test_provision_business_uses_business_id_in_repo_name(isolated_db, monkeypatch):

    register_business("u1", "+14155550000")

    captured = {}

    def fake_create_repo(repo_name, **kwargs):
        captured["repo_name"] = repo_name
        return FAKE_REPO

    monkeypatch.setattr(orchestrator, "create_repo_from_template", fake_create_repo)
    monkeypatch.setattr(orchestrator, "wait_for_repo_ready", lambda *a, **k: True)
    monkeypatch.setattr(
        orchestrator, "create_web_service", lambda *a, **k: FAKE_SERVICE
    )

    orchestrator.provision_business("u1", "business_007")

    assert captured["repo_name"] == "whatspilot-business_007"


def test_provision_business_passes_business_id_env_var(isolated_db, monkeypatch):

    register_business("u1", "+14155550000")

    monkeypatch.setattr(
        orchestrator, "create_repo_from_template", lambda *a, **k: FAKE_REPO
    )
    monkeypatch.setattr(orchestrator, "wait_for_repo_ready", lambda *a, **k: True)

    captured = {}

    def fake_create_service(name, repo_url, env_vars):
        captured["env_vars"] = env_vars
        return FAKE_SERVICE

    monkeypatch.setattr(orchestrator, "create_web_service", fake_create_service)

    orchestrator.provision_business("u1", "business_001")

    keys = {item["key"] for item in captured["env_vars"]}
    assert "BUSINESS_ID" in keys
    business_id_entry = next(
        item for item in captured["env_vars"] if item["key"] == "BUSINESS_ID"
    )
    assert business_id_entry["value"] == "business_001"

    # SESSION_SECRET_KEY should be Render-generated, not a literal value
    # this app invents itself.
    session_key_entry = next(
        item for item in captured["env_vars"] if item["key"] == "SESSION_SECRET_KEY"
    )
    assert session_key_entry.get("generateValue") is True
    assert "value" not in session_key_entry


def test_provision_business_github_failure_records_failed_status(isolated_db, monkeypatch):

    register_business("u1", "+14155550000")

    def fake_create_repo(*a, **k):
        raise GitHubProvisioningError("name already exists")

    monkeypatch.setattr(orchestrator, "create_repo_from_template", fake_create_repo)

    called = {"wait": False, "render": False}
    monkeypatch.setattr(
        orchestrator, "wait_for_repo_ready",
        lambda *a, **k: called.__setitem__("wait", True) or True
    )
    monkeypatch.setattr(
        orchestrator, "create_web_service",
        lambda *a, **k: called.__setitem__("render", True) or FAKE_SERVICE
    )

    result = orchestrator.provision_business("u1", "business_001")

    assert result["provisioning_status"] == "failed"
    assert "GitHub" in result["provisioning_error"]
    assert "name already exists" in result["provisioning_error"]
    assert result["github_repo_url"] is None
    assert result["render_service_id"] is None

    # A GitHub failure should short-circuit - never touch Render at all.
    assert called["wait"] is False
    assert called["render"] is False

    row = next(b for b in list_businesses() if b["user_id"] == "u1")
    assert row["provisioning_status"] == "failed"


def test_provision_business_render_failure_still_records_repo_url(isolated_db, monkeypatch):
    """
    A partial failure (repo created, Render call then fails) should still
    save the repo URL that was already created - so a retry or manual
    cleanup has something concrete to work with, and the repo isn't
    silently orphaned from the admin's point of view.
    """

    register_business("u1", "+14155550000")

    monkeypatch.setattr(
        orchestrator, "create_repo_from_template", lambda *a, **k: FAKE_REPO
    )
    monkeypatch.setattr(orchestrator, "wait_for_repo_ready", lambda *a, **k: True)

    def fake_create_service(*a, **k):
        raise RenderProvisioningError("payment information required")

    monkeypatch.setattr(orchestrator, "create_web_service", fake_create_service)

    result = orchestrator.provision_business("u1", "business_001")

    assert result["provisioning_status"] == "failed"
    assert "Render" in result["provisioning_error"]
    assert result["github_repo_url"] == FAKE_REPO["html_url"]
    assert result["render_service_id"] is None

    row = next(b for b in list_businesses() if b["user_id"] == "u1")
    assert row["github_repo_url"] == FAKE_REPO["html_url"]
    assert row["provisioning_status"] == "failed"


def test_provision_business_proceeds_even_if_repo_never_confirmed_ready(isolated_db, monkeypatch):
    """
    wait_for_repo_ready() returning False (content check timed out)
    should log a warning but not block provisioning from continuing to
    the Render step - see wait_for_repo_ready()'s own docstring on why
    this is a soft signal, not a hard failure.
    """

    register_business("u1", "+14155550000")

    monkeypatch.setattr(
        orchestrator, "create_repo_from_template", lambda *a, **k: FAKE_REPO
    )
    monkeypatch.setattr(orchestrator, "wait_for_repo_ready", lambda *a, **k: False)
    monkeypatch.setattr(
        orchestrator, "create_web_service", lambda *a, **k: FAKE_SERVICE
    )

    result = orchestrator.provision_business("u1", "business_001")

    assert result["provisioning_status"] == "live"


def test_provision_business_never_raises_on_failure(isolated_db, monkeypatch):

    register_business("u1", "+14155550000")

    def fake_create_repo(*a, **k):
        raise GitHubProvisioningError("boom")

    monkeypatch.setattr(orchestrator, "create_repo_from_template", fake_create_repo)

    # Should return a dict, not raise - api/businesses.py's create_business()
    # relies on this to still return a 200 with provisioning failure info
    # rather than a 500.
    result = orchestrator.provision_business("u1", "business_001")
    assert isinstance(result, dict)


def test_provision_business_retry_reuses_existing_repo(isolated_db, monkeypatch):
    """
    Retry-after-partial-failure: if a prior attempt already created the
    GitHub repo (recorded via update_provisioning_result()) but failed at
    the Render step, a retry should NOT call create_repo_from_template()
    again - repo names are deterministic
    (_repo_name_for_business()), so that used to always fail with a
    "name already exists" GitHubProvisioningError and made retry a dead
    end. It should instead reuse the repo URL already on file and go
    straight to Render.
    """

    register_business("u1", "+14155550000")

    # Simulate a prior failed attempt that got as far as creating the repo.
    from crm.customer_mapping import update_provisioning_result
    update_provisioning_result(
        "u1",
        provisioning_status="failed",
        provisioning_error="Render: payment information required",
        github_repo_url=FAKE_REPO["html_url"],
        render_service_id=None,
        render_service_url=None,
    )

    def fake_create_repo(*a, **k):
        raise AssertionError(
            "should not call create_repo_from_template() again when a "
            "repo already exists from a prior attempt"
        )

    monkeypatch.setattr(orchestrator, "create_repo_from_template", fake_create_repo)
    monkeypatch.setattr(orchestrator, "wait_for_repo_ready", lambda *a, **k: True)

    captured = {}

    def fake_create_service(name, repo_url, env_vars):
        captured["repo_url"] = repo_url
        return FAKE_SERVICE

    monkeypatch.setattr(orchestrator, "create_web_service", fake_create_service)

    result = orchestrator.provision_business("u1", "business_001")

    assert result["provisioning_status"] == "live"
    assert result["github_repo_url"] == FAKE_REPO["html_url"]
    assert captured["repo_url"] == FAKE_REPO["html_url"]


def test_provision_business_survives_wait_for_repo_ready_raising(isolated_db, monkeypatch):
    """
    wait_for_repo_ready() used to be called outside any try/except in
    provision_business() - an unexpected exception there (not just the
    requests.RequestException it already retries past internally) would
    propagate out of this function despite its docstring promising it
    never raises, leaving the business stuck at provisioning_status=
    'pending' and returning an unhandled 500 to the admin UI. Provisioning
    should still complete (with a warning logged) instead.
    """

    register_business("u1", "+14155550000")

    monkeypatch.setattr(
        orchestrator, "create_repo_from_template", lambda *a, **k: FAKE_REPO
    )

    def fake_wait(*a, **k):
        raise RuntimeError("unexpected bug in wait_for_repo_ready")

    monkeypatch.setattr(orchestrator, "wait_for_repo_ready", fake_wait)
    monkeypatch.setattr(
        orchestrator, "create_web_service", lambda *a, **k: FAKE_SERVICE
    )

    result = orchestrator.provision_business("u1", "business_001")

    assert result["provisioning_status"] == "live"
