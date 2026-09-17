from __future__ import annotations

import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from quantdesk.api.app import create_app
from quantdesk.api.auth import UserRole, auth_manager
from quantdesk.api.commands import durable_inbox, escape_csv_formula
from quantdesk.observability.logging import SecretRedactionFilter


@pytest.fixture(autouse=True)
def reset_inbox_and_auth(tmp_path: Path):
    auth_manager.reset()
    durable_inbox.reset()
    durable_inbox.db_path = tmp_path / "test_security_control.db"
    durable_inbox._init_db()


def create_authenticated_client(role: UserRole = UserRole.OPERATOR) -> tuple[TestClient, dict[str, str]]:
    app = create_app()
    client = TestClient(app, base_url="http://127.0.0.1:8000")

    # Bootstrap admin
    token = auth_manager.bootstrap_token
    assert token is not None
    auth_manager.bootstrap(token, "adminPassword123!")

    # Add user
    username = f"user_{role.value}"
    auth_manager.add_user(username, "password123!", role)

    # Login to acquire session and CSRF cookie
    res = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": "password123!"},
    )
    assert res.status_code == 200
    csrf_token = res.json()["csrf_token"]
    headers = {
        "x-csrf-token": csrf_token,
        "Origin": "http://127.0.0.1:8000",
    }
    return client, headers


def test_security_forged_actor():
    """Actor identity in body is ignored; server derives actor from authenticated session (§15.3)."""
    client, headers = create_authenticated_client(role=UserRole.OPERATOR)

    cmd_id = "cmd-forged-actor-test"
    res = client.post(
        "/api/v1/commands",
        headers=headers,
        json={
            "command_id": cmd_id,
            "type": "PAUSE_STRATEGY",
            "actor_id": "malicious_spoofed_user",
            "target": {"strategy_id": "imbalance-btc"},
            "payload": {},
        },
    )
    assert res.status_code == 202

    record = durable_inbox.get_record(cmd_id)
    assert record is not None
    assert record.body.get("actor_id") == "user_operator"
    assert record.body.get("actor_id") != "malicious_spoofed_user"


def test_security_viewer_mutation():
    """Viewer role cannot perform mutations; receives HTTP 403 Forbidden (§15.4)."""
    client, headers = create_authenticated_client(role=UserRole.VIEWER)

    # 1. Mutating command rejected
    res = client.post(
        "/api/v1/commands",
        headers=headers,
        json={
            "command_id": "cmd-viewer-fail",
            "type": "PAUSE_STRATEGY",
            "target": {"strategy_id": "imbalance-btc"},
        },
    )
    assert res.status_code == 403

    # 2. Backtest queue rejected
    res = client.post(
        "/api/v1/backtests",
        headers=headers,
        json={"dataset_id": "ds-1", "strategy_id": "strat-1"},
    )
    assert res.status_code == 403

    # 3. Model training rejected
    res = client.post(
        "/api/v1/models/training",
        headers=headers,
        json={"dataset_id": "ds-1", "algorithm": "lightgbm"},
    )
    assert res.status_code == 403

    # 4. Dataset import rejected
    res = client.post(
        "/api/v1/datasets/imports",
        headers=headers,
        json={"instrument_id": "BTCUSDT", "source_path": "fixtures/data.parquet"},
    )
    assert res.status_code == 403

    # 5. Read-only query permitted for viewer
    res = client.get("/api/v1/positions", headers=headers)
    assert res.status_code == 200


def test_security_csrf_rejection():
    """State-changing requests without CSRF token or with mismatch receive 403 (§15.4)."""
    client, headers = create_authenticated_client(role=UserRole.OPERATOR)

    # Missing x-csrf-token header
    headers_no_csrf = {k: v for k, v in headers.items() if k != "x-csrf-token"}
    res = client.post(
        "/api/v1/commands",
        headers=headers_no_csrf,
        json={"command_id": "cmd-csrf-fail-1", "type": "PAUSE_STRATEGY"},
    )
    assert res.status_code == 403
    assert "CSRF" in res.text

    # Mismatched CSRF token
    headers_bad_csrf = dict(headers)
    headers_bad_csrf["x-csrf-token"] = "invalid-token-12345"
    res = client.post(
        "/api/v1/commands",
        headers=headers_bad_csrf,
        json={"command_id": "cmd-csrf-fail-2", "type": "PAUSE_STRATEGY"},
    )
    assert res.status_code == 403


def test_security_origin_rejection():
    """Mutating requests from disallowed Origin receive 403 (§15.4)."""
    client, headers = create_authenticated_client(role=UserRole.OPERATOR)

    bad_origin_headers = dict(headers)
    bad_origin_headers["Origin"] = "http://evil-attacker.com"

    res = client.post(
        "/api/v1/commands",
        headers=bad_origin_headers,
        json={"command_id": "cmd-origin-fail", "type": "PAUSE_STRATEGY"},
    )
    assert res.status_code == 403
    assert "Origin" in res.text


def test_security_host_rejection():
    """Requests with untrusted Host header are rejected to prevent DNS rebinding (§15.4)."""
    client, headers = create_authenticated_client(role=UserRole.OPERATOR)

    bad_host_headers = dict(headers)
    bad_host_headers["Host"] = "evil.attacker.com"

    res = client.get("/api/v1/system", headers=bad_host_headers)
    assert res.status_code == 403
    assert "Host" in res.text


def test_security_malicious_path():
    """Path traversal attempts via artifacts or dataset imports are safely rejected (§15.4)."""
    client, headers = create_authenticated_client(role=UserRole.OPERATOR)

    # Traversal in artifact download
    res = client.get("/api/v1/artifacts/../../etc/passwd/download", headers=headers)
    assert res.status_code in (403, 404)

    # Traversal in dataset import
    res = client.post(
        "/api/v1/datasets/imports",
        headers=headers,
        json={"instrument_id": "BTCUSDT", "source_path": "../../etc/shadow"},
    )
    assert res.status_code == 400
    assert "traversal" in res.text.lower()


def test_security_secret_redaction():
    """SecretRedactionFilter scrubs API keys, passphrases, and bearer tokens from logs (§15.4)."""
    filter_obj = SecretRedactionFilter()

    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname="test.py",
        lineno=10,
        msg="Connecting with bg_sec_99999999 and token Bearer mysecrettoken123!",
        args=(),
        exc_info=None,
    )
    filter_obj.filter(record)
    assert "mysecrettoken123!" not in record.msg
    assert "[REDACTED]" in record.msg


def test_security_credential_write_only():
    """System and status queries never expose API keys or passwords (§15.4)."""
    client, headers = create_authenticated_client(role=UserRole.OPERATOR)

    res = client.get("/api/v1/system", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert "api_key" not in data
    assert "secret_key" not in data
    assert "password" not in data
    assert "passphrase" not in data


def test_security_private_stream_auth():
    """Unauthenticated requests to private event streams return 401 (§15.4)."""
    app = create_app()
    client = TestClient(app, base_url="http://127.0.0.1:8000")

    # Call /api/v1/events without credentials
    res = client.get("/api/v1/events")
    assert res.status_code == 401


def test_security_wrong_account_token():
    """Preview tokens bound to account A are rejected if submitted for account B (§15.3)."""
    client, headers = create_authenticated_client(role=UserRole.OPERATOR)

    # Create preview for account A
    prev_res = client.post(
        "/api/v1/command-previews",
        headers=headers,
        json={
            "type": "ENABLE_LIVE",
            "target": {"account_id": "account-A"},
            "payload": {},
        },
    )
    assert prev_res.status_code == 200
    preview = prev_res.json()
    token = preview["preview_id"]

    # Submit using token for account B
    sub_res = client.post(
        "/api/v1/commands",
        headers=headers,
        json={
            "command_id": "cmd-wrong-acc",
            "type": "ENABLE_LIVE",
            "target": {"account_id": "account-B"},
            "confirmation_token": token,
            "confirmation_text": "LIVE account-A",
        },
    )
    assert sub_res.status_code == 403
    assert "bound to target" in sub_res.text


def test_security_cas_version_conflict():
    """Stale expected_state_version returns HTTP 409 Conflict (§15.3)."""
    client, headers = create_authenticated_client(role=UserRole.OPERATOR)

    durable_inbox.set_resource_version("imbalance-btc", "42")

    res = client.post(
        "/api/v1/commands",
        headers=headers,
        json={
            "command_id": "cmd-cas-conflict",
            "type": "PAUSE_STRATEGY",
            "target": {"strategy_id": "imbalance-btc"},
            "expected_state_version": "41",  # Stale version!
        },
    )
    assert res.status_code == 409
    assert "VERSION_CONFLICT" in res.text


def test_security_double_submission_idempotency():
    """Submitting same command_id twice returns 202 if body identical, 409 if changed (§15.3)."""
    client, headers = create_authenticated_client(role=UserRole.OPERATOR)

    cmd_id = "cmd-idempotency-test"
    body_a = {
        "command_id": cmd_id,
        "type": "PAUSE_STRATEGY",
        "target": {"strategy_id": "imbalance-btc"},
    }

    # 1. Initial submission
    res1 = client.post("/api/v1/commands", headers=headers, json=body_a)
    assert res1.status_code == 202

    # 2. Duplicate submission with identical body -> returns 202
    res2 = client.post("/api/v1/commands", headers=headers, json=body_a)
    assert res2.status_code == 202

    # 3. Same ID with modified body -> returns 409 Conflict
    body_b = dict(body_a)
    body_b["type"] = "RESUME_STRATEGY"
    res3 = client.post("/api/v1/commands", headers=headers, json=body_b)
    assert res3.status_code == 409


def test_security_csv_formula_escaping():
    """Spreadsheet formula prefixes (=, +, -, @, tab, cr) are escaped with single quote (§15.4)."""
    assert escape_csv_formula("=SUM(A1:A10)") == "'=SUM(A1:A10)"
    assert escape_csv_formula("+100") == "'+100"
    assert escape_csv_formula("-50") == "'-50"
    assert escape_csv_formula("@cmd") == "'@cmd"
    assert escape_csv_formula("\tdata") == "'\tdata"
    assert escape_csv_formula("normal_text") == "normal_text"
    assert escape_csv_formula("") == ""
