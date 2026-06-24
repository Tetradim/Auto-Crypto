import hashlib
import hmac
import json

from fastapi.testclient import TestClient

from autocrypto.app import create_app


def test_signed_webhook_rejects_missing_or_bad_signature_then_accepts_valid_signature():
    app = create_app(webhook_secret="top-secret")
    client = TestClient(app)
    payload = {
        "symbol": "BTC/USDT",
        "side": "buy",
        "quote_amount": "25",
        "price": "50000",
        "stop_loss_pct": "2",
    }
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    timestamp = "2000000000"

    missing = client.post(
        "/webhooks/tradingview",
        content=body,
        headers={"content-type": "application/json"},
    )
    assert missing.status_code == 401

    bad = client.post(
        "/webhooks/tradingview",
        content=body,
        headers={
            "content-type": "application/json",
            "x-auto-crypto-timestamp": timestamp,
            "x-auto-crypto-signature": "sha256=bad",
        },
    )
    assert bad.status_code == 401

    digest = hmac.new(b"top-secret", timestamp.encode("utf-8") + b"." + body, hashlib.sha256).hexdigest()
    accepted = client.post(
        "/webhooks/tradingview",
        content=body,
        headers={
            "content-type": "application/json",
            "x-auto-crypto-timestamp": timestamp,
            "x-auto-crypto-signature": f"sha256={digest}",
        },
    )

    assert accepted.status_code == 200
    assert accepted.json()["status"] == "accepted"


def test_control_halt_requires_signature_when_secret_is_configured():
    app = create_app(webhook_secret="top-secret")
    client = TestClient(app)

    response = client.post("/control/halt", json={"reason": "test"})

    assert response.status_code == 401
    assert client.get("/control/status").json()["halted"] is False


def test_operator_state_change_requires_session_or_signature_when_secret_is_not_configured():
    app = create_app()
    client = TestClient(app)

    response = client.post("/control/halt", json={"reason": "unsigned local mutation"})

    assert response.status_code == 401
    assert client.get("/control/status").json()["halted"] is False


def test_operator_signal_submission_requires_signature_when_secret_is_configured():
    app = create_app(webhook_secret="top-secret")
    client = TestClient(app)

    response = client.post(
        "/signals/submit",
        json={
            "symbol": "BTC/USDT",
            "side": "buy",
            "quote_amount": "25",
            "price": "50000",
            "stop_loss_pct": "2",
        },
    )

    assert response.status_code == 401
    assert client.get("/orders").json()["orders"] == []


def test_operator_ui_session_cookie_authorizes_browser_state_change_without_hmac():
    app = create_app(webhook_secret="top-secret")
    client = TestClient(app)

    ui = client.get("/ui")
    response = client.post("/control/halt", json={"reason": "operator UI"})

    assert ui.status_code == 200
    cookie = ui.headers["set-cookie"].lower()
    assert "auto_crypto_operator_session=" in cookie
    assert "httponly" in cookie
    assert "samesite=strict" in cookie
    assert response.status_code == 200
    assert response.json() == {"halted": True, "reason": "operator UI"}


def test_operator_ui_session_cookie_rejects_cross_origin_state_change():
    app = create_app(webhook_secret="top-secret")
    client = TestClient(app)
    client.get("/ui")

    response = client.post(
        "/control/halt",
        json={"reason": "cross origin"},
        headers={"origin": "https://malicious.example"},
    )

    assert response.status_code == 403
    assert client.get("/control/status").json()["halted"] is False


def test_webhook_rejects_operator_ui_session_cookie_without_hmac():
    app = create_app(webhook_secret="top-secret")
    client = TestClient(app)
    client.get("/ui")

    response = client.post(
        "/webhooks/tradingview",
        json={
            "symbol": "BTC/USDT",
            "side": "buy",
            "quote_amount": "25",
            "price": "50000",
            "stop_loss_pct": "2",
        },
    )

    assert response.status_code == 401
