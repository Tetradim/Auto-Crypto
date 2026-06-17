import hashlib
import hmac
import json

from fastapi.testclient import TestClient

from autocrypto.app import create_app


def test_signed_webhook_rejects_stale_timestamp_and_replayed_payload():
    app = create_app(
        webhook_secret="top-secret",
        webhook_clock=lambda: 2000.0,
        webhook_tolerance_seconds=60,
    )
    client = TestClient(app)
    payload = {
        "symbol": "BTC/USDT",
        "side": "buy",
        "quote_amount": "25",
        "price": "50000",
        "stop_loss_pct": "2",
    }
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")

    stale = client.post(
        "/webhooks/tradingview",
        content=body,
        headers=_signed_headers("top-secret", "1900", body),
    )
    assert stale.status_code == 401
    assert "stale" in stale.json()["detail"]

    first = client.post(
        "/webhooks/tradingview",
        content=body,
        headers=_signed_headers("top-secret", "2000", body),
    )
    assert first.status_code == 200

    replay = client.post(
        "/webhooks/tradingview",
        content=body,
        headers=_signed_headers("top-secret", "2000", body),
    )
    assert replay.status_code == 409
    assert "replay" in replay.json()["detail"]


def _signed_headers(secret: str, timestamp: str, body: bytes) -> dict[str, str]:
    digest = hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()
    return {
        "content-type": "application/json",
        "x-auto-crypto-timestamp": timestamp,
        "x-auto-crypto-signature": f"sha256={digest}",
    }

