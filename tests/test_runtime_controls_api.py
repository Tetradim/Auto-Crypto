from fastapi.testclient import TestClient

from autocrypto.app import create_app
from autocrypto.repository import SQLiteRepository


def _entry_payload(signal_id="entry"):
    return {
        "signal_id": signal_id,
        "symbol": "BTCUSDT",
        "side": "buy",
        "quote_amount": "100",
        "price": "100",
        "stop_loss_price": "98",
        "take_profit_price": "104",
        "market_type": "swap",
    }


def test_protection_rule_blocks_signal_preview_and_intake(tmp_path):
    repo = SQLiteRepository(tmp_path / "controls.sqlite3")
    client = TestClient(create_app(repository=repo))
    client.get("/ui")
    rule = {
        "rule_id": "btc-close-only",
        "mode": "close_only",
        "scope": "symbol",
        "target": "BTCUSDT",
        "reason": "manual de-risk",
    }

    created = client.post("/protections/rules", json=rule)
    preview = client.post("/signals/preview", json=_entry_payload("preview-blocked"))
    submitted = client.post("/webhooks/tradingview", json=_entry_payload("submit-blocked"))

    assert created.status_code == 200
    assert preview.json()["execution"]["next_status"] == "rejected"
    assert preview.json()["protections"]["mode"] == "close_only"
    assert submitted.json()["status"] == "rejected"
    assert "protection_close_only" in submitted.json()["risk"]["reason_codes"]
    assert [event.event_type for event in repo.list_audit()] == [
        "protection.rule_set",
        "signal.received",
        "order.rejected",
    ]


def test_runtime_control_rejection_does_not_claim_signal_id(tmp_path):
    repo = SQLiteRepository(tmp_path / "retry_controls.sqlite3")
    client = TestClient(create_app(repository=repo))
    client.get("/ui")
    client.post(
        "/protections/rules",
        json={
            "rule_id": "temporary-block",
            "mode": "no_new_entries",
            "scope": "symbol",
            "target": "BTCUSDT",
        },
    )

    blocked = client.post("/webhooks/tradingview", json=_entry_payload("retry-after-control"))
    client.delete("/protections/rules/temporary-block")
    retried = client.post("/webhooks/tradingview", json=_entry_payload("retry-after-control"))

    assert blocked.json()["status"] == "rejected"
    assert retried.json()["status"] == "accepted"
    assert len(repo.list_orders()) == 1


def test_reentry_cooldown_blocks_new_entry_after_exit_across_restart(tmp_path):
    db_path = tmp_path / "cooldown.sqlite3"
    first = TestClient(create_app(repository=SQLiteRepository(db_path)))
    first.get("/ui")
    first.post("/runtime/config", json={"reentry_cooldown_seconds": 120})
    accepted = first.post("/webhooks/tradingview", json=_entry_payload("cooldown-entry"))
    closed = first.post(
        "/brackets/cooldown-entry/close",
        json={"price": "101", "reason": "test close"},
    )

    second = TestClient(create_app(repository=SQLiteRepository(db_path)))
    blocked = second.post("/webhooks/tradingview", json=_entry_payload("cooldown-reentry"))
    reduce_only = second.post(
        "/webhooks/tradingview",
        json={
            "signal_id": "cooldown-exit-allowed",
            "symbol": "BTCUSDT",
            "side": "close_long",
            "quote_amount": "10",
            "price": "101",
        },
    )

    assert accepted.json()["status"] == "accepted"
    assert closed.status_code == 200
    assert blocked.json()["status"] == "rejected"
    assert "reentry_cooldown_active" in blocked.json()["risk"]["reason_codes"]
    assert reduce_only.json()["status"] != "rejected" or "reentry_cooldown_active" not in reduce_only.json()["risk"]["reason_codes"]


def test_reentry_cooldown_is_recorded_for_reduce_only_webhook_exit(tmp_path):
    db_path = tmp_path / "webhook_exit_cooldown.sqlite3"
    client = TestClient(create_app(repository=SQLiteRepository(db_path)))
    client.get("/ui")
    client.post("/runtime/config", json={"reentry_cooldown_seconds": 120})
    entry = client.post("/webhooks/tradingview", json=_entry_payload("webhook-cooldown-entry"))
    close = client.post(
        "/webhooks/tradingview",
        json={
            "signal_id": "webhook-cooldown-close",
            "symbol": "BTCUSDT",
            "side": "close_long",
            "quote_amount": "100",
            "price": "101",
        },
    )

    blocked = client.post("/webhooks/tradingview", json=_entry_payload("webhook-cooldown-reentry"))

    assert entry.json()["status"] == "accepted"
    assert close.json()["status"] == "accepted"
    assert blocked.json()["status"] == "rejected"
    assert "reentry_cooldown_active" in blocked.json()["risk"]["reason_codes"]


def test_stressed_market_state_queues_approval_instead_of_rejecting(tmp_path):
    repo = SQLiteRepository(tmp_path / "market_state_approval.sqlite3")
    client = TestClient(create_app(repository=repo))
    payload = {
        **_entry_payload("stressed-state-approval"),
        "market_state": {
            "volatility_pct": "9",
            "spread_bps": "10",
            "depth_notional": "100000",
            "data_stale_seconds": 5,
            "exchange_status": "ok",
        },
    }

    preview = client.post("/signals/preview", json=payload)
    submitted = client.post("/webhooks/tradingview", json=payload)

    assert preview.json()["execution"]["next_status"] == "approval_required"
    assert preview.json()["execution"]["would_place_order"] is False
    assert submitted.json()["status"] == "approval_required"
    assert repo.list_pending_approvals()[0]["signal_id"] == "stressed-state-approval"


def test_futures_signal_missing_price_is_rejected_with_explicit_reason():
    client = TestClient(create_app())
    payload = {
        "signal_id": "missing-futures-price",
        "symbol": "BTCUSDT",
        "side": "buy",
        "quote_amount": "100",
        "stop_loss_pct": "2",
        "market_type": "swap",
    }

    response = client.post("/signals/preview", json=payload)
    body = response.json()

    assert response.status_code == 200
    assert body["execution"]["next_status"] == "rejected"
    assert body["futures_risk"]["approved"] is False
    assert "futures_price_required" in body["risk"]["reason_codes"]


def test_signal_preview_combines_market_state_and_futures_risk():
    client = TestClient(create_app())
    payload = {
        **_entry_payload("futures-state-block"),
        "leverage": "25",
        "funding_rate_bps": "15",
        "minutes_to_funding": 5,
        "market_state": {
            "volatility_pct": "4",
            "spread_bps": "10",
            "depth_notional": "100000",
            "data_stale_seconds": 60,
            "exchange_status": "ok",
        },
    }

    response = client.post("/signals/preview", json=payload)
    body = response.json()

    assert response.status_code == 200
    assert body["execution"]["next_status"] == "rejected"
    assert body["futures_risk"]["approved"] is False
    assert "max_leverage_exceeded" in body["futures_risk"]["reason_codes"]
    assert body["market_state"]["name"] == "halted"
    assert body["advisory_risk"]["level"] in {"high", "extreme"}
    assert body["advisory_risk"]["hard_gate"] is False
    assert "market_state_no_new_entries" in body["risk"]["reason_codes"]
