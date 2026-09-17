from __future__ import annotations

from fastapi.testclient import TestClient

from quantdesk.api.app import create_app
from quantdesk.api.auth import UserRole, auth_manager


def test_durable_command_is_applied_once(case):
    """Primary gate test per §15.3 and Task 13 of IMPLEMENTATION_PLAN.md."""
    r = case("api_duplicate_command", crash_after_engine_apply=True)
    assert r["http_initial_status"] == 202
    assert r["engine_apply_count"] == 1
    assert r["same_id_different_body_status"] == 409
    assert r["recovered_command_status"] == "APPLIED"


def test_liveness_and_readiness():
    app = create_app()
    client = TestClient(app)

    # 1. Minimal unauthenticated liveness
    res_live = client.get("/health/live")
    assert res_live.status_code == 200
    assert res_live.json() == {"status": "ok"}

    # 2. Readiness with demo role header
    res_ready = client.get("/health/ready", headers={"x-quantdesk-role": "viewer"})
    assert res_ready.status_code == 200
    data = res_ready.json()
    assert "checks" in data


def test_auth_bootstrap_and_rbac():
    app = create_app()
    client = TestClient(app)

    # 1. Bootstrap admin
    token = auth_manager.bootstrap_token
    assert token is not None

    res_bs = client.post(
        "/api/v1/auth/bootstrap",
        json={"bootstrap_token": token, "admin_password": "super-secure-password"},
    )
    assert res_bs.status_code == 200
    assert res_bs.json()["username"] == "admin"
    assert res_bs.json()["role"] == "admin"

    # 2. Duplicate bootstrap must be rejected
    res_bs_dup = client.post(
        "/api/v1/auth/bootstrap",
        json={"bootstrap_token": token, "admin_password": "super-secure-password"},
    )
    assert res_bs_dup.status_code == 400

    # 3. Create viewer and operator users
    auth_manager.add_user("test_viewer", "viewer-pwd-123", UserRole.VIEWER)
    auth_manager.add_user("test_operator", "operator-pwd-123", UserRole.OPERATOR)

    # 4. Login as viewer
    res_v_login = client.post(
        "/api/v1/auth/login",
        json={"username": "test_viewer", "password": "viewer-pwd-123"},
    )
    assert res_v_login.status_code == 200
    assert res_v_login.json()["role"] == "viewer"
    csrf_token = res_v_login.json()["csrf_token"]

    # 5. Viewer cannot submit commands (RBAC §15.4)
    cmd_body = {
        "command_id": "viewer-cmd-1",
        "type": "PAUSE_STRATEGY",
        "target": {"strategy_id": "strat1"},
    }
    res_v_cmd = client.post(
        "/api/v1/commands",
        json=cmd_body,
        headers={"x-csrf-token": csrf_token},
    )
    assert res_v_cmd.status_code == 403
    assert "Permission denied" in res_v_cmd.json()["error"]["message"]

    # 6. Login as operator
    res_op_login = client.post(
        "/api/v1/auth/login",
        json={"username": "test_operator", "password": "operator-pwd-123"},
    )
    assert res_op_login.status_code == 200
    op_csrf = res_op_login.json()["csrf_token"]

    # 7. Operator can submit commands
    cmd_body_op = {
        "command_id": "op-cmd-1",
        "type": "PAUSE_STRATEGY",
        "target": {"strategy_id": "strat1"},
    }
    res_op_cmd = client.post(
        "/api/v1/commands",
        json=cmd_body_op,
        headers={"x-csrf-token": op_csrf},
    )
    assert res_op_cmd.status_code == 202
    assert res_op_cmd.json()["status"] == "QUEUED"


def test_host_and_origin_security():
    app = create_app()
    client = TestClient(app)

    # 1. Disallowed Host header (DNS rebinding protection §15.4)
    res_bad_host = client.get("/health/live", headers={"host": "evil-domain.com"})
    assert res_bad_host.status_code == 403
    assert "not permitted" in res_bad_host.json()["error"]["message"]

    # 2. Allowed host passes
    res_good_host = client.get("/health/live", headers={"host": "127.0.0.1:8000"})
    assert res_good_host.status_code == 200


def test_command_idempotency_and_cas():
    app = create_app()
    client = TestClient(app)

    cmd_id = "cas-cmd-test-1"
    body = {
        "command_id": cmd_id,
        "type": "PAUSE_STRATEGY",
        "target": {"strategy_id": "strat-btc"},
        "expected_state_version": "1",
    }

    # Initial submit with matching version
    res1 = client.post("/api/v1/commands", json=body, headers={"x-quantdesk-role": "operator"})
    assert res1.status_code == 202

    # Duplicate submit with identical body -> 202 idempotency
    res_dup = client.post("/api/v1/commands", json=body, headers={"x-quantdesk-role": "operator"})
    assert res_dup.status_code == 202

    # Duplicate submit with changed body -> 409 Conflict
    body_diff = {
        "command_id": cmd_id,
        "type": "RESUME_STRATEGY",
        "target": {"strategy_id": "strat-btc"},
    }
    res_diff = client.post(
        "/api/v1/commands", json=body_diff, headers={"x-quantdesk-role": "operator"}
    )
    assert res_diff.status_code == 409

    # CAS version mismatch
    body_mismatch = {
        "command_id": "cas-cmd-test-2",
        "type": "PAUSE_STRATEGY",
        "target": {"strategy_id": "strat-btc"},
        "expected_state_version": "999",  # Wrong version!
    }
    res_cas = client.post(
        "/api/v1/commands", json=body_mismatch, headers={"x-quantdesk-role": "operator"}
    )
    assert res_cas.status_code == 409


def test_read_models_and_artifact_confinement():
    app = create_app()
    client = TestClient(app)
    headers = {"x-quantdesk-role": "viewer"}

    # Positions, orders, fills, balances, risk, strategies
    assert client.get("/api/v1/positions", headers=headers).status_code == 200
    assert client.get("/api/v1/orders", headers=headers).status_code == 200
    assert client.get("/api/v1/fills", headers=headers).status_code == 200
    assert client.get("/api/v1/balances", headers=headers).status_code == 200
    assert client.get("/api/v1/risk", headers=headers).status_code == 200
    assert client.get("/api/v1/strategies", headers=headers).status_code == 200
    assert client.get("/api/v1/orders/ord-1/trace", headers=headers).status_code == 200

    # Path traversal attack on artifact download (§15.4)
    res_traversal = client.get("/api/v1/artifacts/../../etc/passwd/download", headers=headers)
    assert res_traversal.status_code in (403, 404)
