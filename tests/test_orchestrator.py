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
from crm.customer_mapping import register_business, list_businesses, get_business
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


def test_provision_business_database_url_falls_back_when_no_restricted_role(
    isolated_db, monkeypatch
):
    """
    Before provisioning/setup_business_portal_role.py has been run,
    BUSINESS_PORTAL_DATABASE_URL is unset - new deployments should still
    get a working DATABASE_URL (falling back to this admin app's own),
    not a blank/None value.
    """

    register_business("u1", "+14155550000")

    monkeypatch.setattr(orchestrator, "DATABASE_URL", "postgresql://owner-conn")
    monkeypatch.setattr(orchestrator, "BUSINESS_PORTAL_DATABASE_URL", None)

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

    db_url_entry = next(
        item for item in captured["env_vars"] if item["key"] == "DATABASE_URL"
    )
    assert db_url_entry["value"] == "postgresql://owner-conn"


def test_provision_business_uses_restricted_database_url_when_configured(
    isolated_db, monkeypatch
):
    """
    Once BUSINESS_PORTAL_DATABASE_URL is set (after running
    provisioning/setup_business_portal_role.py), new deployments should
    get that least-privilege connection string instead of the admin
    app's own full-access DATABASE_URL.
    """

    register_business("u1", "+14155550000")

    monkeypatch.setattr(orchestrator, "DATABASE_URL", "postgresql://owner-conn")
    monkeypatch.setattr(
        orchestrator, "BUSINESS_PORTAL_DATABASE_URL", "postgresql://restricted-conn"
    )

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

    db_url_entry = next(
        item for item in captured["env_vars"] if item["key"] == "DATABASE_URL"
    )
    assert db_url_entry["value"] == "postgresql://restricted-conn"


def test_provision_business_falls_back_to_shared_number_when_none_set(
    isolated_db, monkeypatch
):
    """
    A business registered without its own twilio_whatsapp_number (the
    default for every business today) should still get a working
    TWILIO_WHATSAPP_NUMBER env var - falling back to the admin app's
    shared Sandbox number, same as before this field existed.
    """

    register_business("u1", "+14155550000")

    monkeypatch.setattr(orchestrator, "TWILIO_WHATSAPP_NUMBER", "+14155238886")

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

    number_entry = next(
        item for item in captured["env_vars"]
        if item["key"] == "TWILIO_WHATSAPP_NUMBER"
    )
    assert number_entry["value"] == "+14155238886"


def test_provision_business_uses_own_number_when_set(isolated_db, monkeypatch):
    """
    A business that already has its own twilio_whatsapp_number on file
    (set at registration, see api/businesses.py's RegisterBusinessRequest)
    should get that number in its deployment's env vars instead of the
    shared Sandbox number.
    """

    register_business(
        "u1", "+14155550000", twilio_whatsapp_number="+14155559999"
    )

    monkeypatch.setattr(orchestrator, "TWILIO_WHATSAPP_NUMBER", "+14155238886")

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

    number_entry = next(
        item for item in captured["env_vars"]
        if item["key"] == "TWILIO_WHATSAPP_NUMBER"
    )
    assert number_entry["value"] == "+14155559999"


def test_provision_business_falls_back_to_shared_groq_key_when_none_set(
    isolated_db, monkeypatch
):
    """
    A business registered without its own groq_api_key (the default for
    every business today) should still get a working GROQ_API_KEY env
    var - falling back to the admin app's shared key, same as before
    this field existed.
    """

    register_business("u1", "+14155550000")

    monkeypatch.setattr(orchestrator, "GROQ_API_KEY", "shared-groq-key")

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

    key_entry = next(
        item for item in captured["env_vars"] if item["key"] == "GROQ_API_KEY"
    )
    assert key_entry["value"] == "shared-groq-key"


def test_provision_business_uses_own_groq_key_when_set(isolated_db, monkeypatch):
    """
    A business that already has its own groq_api_key on file (set at
    registration, see api/businesses.py's RegisterBusinessRequest) should
    get that key in its deployment's env vars instead of the shared one.
    """

    register_business(
        "u1", "+14155550000",
        groq_api_key="gsk_own1234567890abcdef1234"
    )

    monkeypatch.setattr(orchestrator, "GROQ_API_KEY", "shared-groq-key")

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

    key_entry = next(
        item for item in captured["env_vars"] if item["key"] == "GROQ_API_KEY"
    )
    assert key_entry["value"] == "gsk_own1234567890abcdef1234"


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


def test_provision_business_failure_preserves_existing_render_service(
    isolated_db, monkeypatch
):
    """
    Regression test: a failed provisioning attempt used to unconditionally
    write render_service_id/render_service_url as None, even for a
    business that was already live with a real, still-running Render
    service on file - see get_business()'s docstring for why. This
    simulates a business that's already live, then a subsequent attempt
    (e.g. a race with a concurrent retry, before api/businesses.py's own
    409 guard existed) that fails at the Render step - the previously
    good render_service_id/url must survive that failure untouched.
    """

    register_business("u1", "+14155550000")

    # Simulate this business already being live with a real Render
    # service on file, same as a normal successful provision_business()
    # call would have recorded.
    from crm.customer_mapping import update_provisioning_result
    update_provisioning_result(
        "u1",
        provisioning_status="live",
        provisioning_error=None,
        github_repo_url=FAKE_REPO["html_url"],
        render_service_id=FAKE_SERVICE["id"],
        render_service_url=FAKE_SERVICE["url"],
    )

    monkeypatch.setattr(
        orchestrator, "create_repo_from_template", lambda *a, **k: FAKE_REPO
    )
    monkeypatch.setattr(orchestrator, "wait_for_repo_ready", lambda *a, **k: True)

    def fake_create_service(*a, **k):
        raise RenderProvisioningError("name already exists")

    monkeypatch.setattr(orchestrator, "create_web_service", fake_create_service)

    result = orchestrator.provision_business("u1", "business_001")

    assert result["provisioning_status"] == "failed"
    # The already-good values must survive, not be nulled out.
    assert result["render_service_id"] == FAKE_SERVICE["id"]
    assert result["render_service_url"] == FAKE_SERVICE["url"]

    row = get_business("u1")
    assert row["render_service_id"] == FAKE_SERVICE["id"]
    assert row["render_service_url"] == FAKE_SERVICE["url"]


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
