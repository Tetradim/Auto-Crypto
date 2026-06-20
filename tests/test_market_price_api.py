from decimal import Decimal

from fastapi.testclient import TestClient

from autocrypto.app import create_app
from autocrypto.repository import SQLiteRepository
from autocrypto.risk import RiskConfig


def test_market_price_endpoint_triggers_paper_exit_and_audit_event(tmp_path):
    repo = SQLiteRepository(tmp_path / "market.sqlite3")
    app = create_app(repository=repo)
    client = TestClient(app)
    client.post(
        "/webhooks/tradingview",
        json={
            "symbol": "SOLUSDT",
            "side": "buy",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_pct": "2",
            "take_profit_pct": "5",
        },
    )

    response = client.post("/market/price", json={"symbol": "SOLUSDT", "price": "106"})

    assert response.status_code == 200
    assert response.json()["triggered"] == [
        {"symbol": "SOL/USDT", "kind": "take_profit", "price": "106.00000000", "quantity": "1.00000000"}
    ]
    audit_types = [event["event_type"] for event in client.get("/audit").json()["events"]]
    assert audit_types[-1] == "exit.triggered"


def test_market_price_exit_reduces_open_notional_for_future_risk(tmp_path):
    repo = SQLiteRepository(tmp_path / "market_risk.sqlite3")
    app = create_app(
        repository=repo,
        risk_config=RiskConfig(max_order_notional=Decimal("500"), max_open_notional=Decimal("150")),
    )
    client = TestClient(app)
    client.post(
        "/webhooks/tradingview",
        json={
            "signal_id": "market-risk-entry",
            "symbol": "SOLUSDT",
            "side": "buy",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_pct": "2",
            "take_profit_pct": "5",
        },
    )
    client.post("/market/price", json={"symbol": "SOLUSDT", "price": "106"})

    next_order = client.post(
        "/webhooks/tradingview",
        json={
            "signal_id": "market-risk-next",
            "symbol": "ETHUSDT",
            "side": "buy",
            "quote_amount": "100",
            "price": "3000",
            "stop_loss_pct": "2",
        },
    )

    assert next_order.json()["status"] == "accepted"


def test_market_price_stop_loss_updates_daily_pnl_and_loss_streak(tmp_path):
    repo = SQLiteRepository(tmp_path / "market_loss_streak.sqlite3")
    app = create_app(
        repository=repo,
        risk_config=RiskConfig(max_order_notional=Decimal("500"), max_consecutive_losses=1),
    )
    client = TestClient(app)
    entry = client.post(
        "/webhooks/tradingview",
        json={
            "signal_id": "loss-streak-entry",
            "symbol": "BTCUSDT",
            "side": "buy",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_pct": "2",
            "take_profit_pct": "4",
        },
    )
    exit_response = client.post("/market/price", json={"symbol": "BTCUSDT", "price": "97"})
    next_signal = client.post(
        "/webhooks/tradingview",
        json={
            "signal_id": "loss-streak-next",
            "symbol": "ETHUSDT",
            "side": "buy",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_pct": "2",
            "take_profit_pct": "4",
        },
    )

    assert entry.status_code == 200
    assert exit_response.status_code == 200
    exit_body = exit_response.json()
    assert exit_body["realized_pnl_delta"] == "-3"
    assert exit_body["daily_pnl"] == "-3"
    assert exit_body["consecutive_losses"] == 1
    assert next_signal.json()["status"] == "rejected"
    assert "consecutive_loss_limit_exceeded" in next_signal.json()["risk"]["reason_codes"]


def test_market_price_preview_reports_trigger_without_mutating_paper_state(tmp_path):
    repo = SQLiteRepository(tmp_path / "market_preview.sqlite3")
    app = create_app(repository=repo)
    client = TestClient(app)
    client.post(
        "/webhooks/tradingview",
        json={
            "signal_id": "preview-entry",
            "symbol": "BTCUSDT",
            "side": "buy",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_pct": "3",
            "take_profit_pct": "5",
        },
    )

    preview = client.post("/market/price/preview", json={"symbol": "BTCUSDT", "price": "105"})
    state_after_preview = client.get("/ui/state").json()

    assert preview.status_code == 200
    assert preview.json()["would_trigger"] == [
        {"symbol": "BTC/USDT", "kind": "take_profit", "price": "105.00000000", "quantity": "1.00000000"}
    ]
    assert state_after_preview["positions"][0]["quantity"] == "1.00000000"
    assert len(state_after_preview["orders"]) == 1
    assert state_after_preview["active_exits"]


def test_bracket_preview_reports_one_signal_trigger_distance_without_mutating_state(tmp_path):
    repo = SQLiteRepository(tmp_path / "bracket_preview.sqlite3")
    app = create_app(repository=repo)
    client = TestClient(app)
    for signal_id, take_profit_pct in [("preview-lot-one", "5"), ("preview-lot-two", "10")]:
        client.post(
            "/webhooks/tradingview",
            json={
                "signal_id": signal_id,
                "symbol": "BTCUSDT",
                "side": "buy",
                "quote_amount": "100",
                "price": "100",
                "stop_loss_pct": "4",
                "take_profit_pct": take_profit_pct,
                "trailing_stop_pct": "3",
                "trailing_activation_pct": "2",
            },
        )

    preview = client.post("/brackets/preview-lot-one/preview", json={"price": "105"})
    state_after_preview = client.get("/ui/state").json()

    assert preview.status_code == 200
    body = preview.json()
    assert body["would_trigger"] == [
        {"symbol": "BTC/USDT", "kind": "take_profit", "price": "105.00000000", "quantity": "1.00000000"}
    ]
    target_exit = next(exit_order for exit_order in body["active_exits"] if exit_order["kind"] == "take_profit")
    trailing_exit = next(exit_order for exit_order in body["active_exits"] if exit_order["kind"] == "trailing_stop")
    assert target_exit["distance_to_trigger"] == "0.00"
    assert trailing_exit["trailing_activation_price"] == "102.00"
    assert state_after_preview["positions"][0]["quantity"] == "2.00000000"
    assert len(state_after_preview["orders"]) == 2


def test_bracket_cancel_endpoint_cancels_exits_and_records_audit(tmp_path):
    repo = SQLiteRepository(tmp_path / "bracket_cancel.sqlite3")
    app = create_app(repository=repo)
    client = TestClient(app)
    client.post(
        "/webhooks/tradingview",
        json={
            "signal_id": "api-cancel-bracket",
            "symbol": "BTCUSDT",
            "side": "buy",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_pct": "3",
            "take_profit_pct": "5",
            "trailing_stop_pct": "2",
        },
    )

    status_before = client.get("/brackets/api-cancel-bracket")
    cancel = client.post("/brackets/api-cancel-bracket/cancel", json={"reason": "operator tightened manually"})
    exit_attempt = client.post("/market/price", json={"symbol": "BTCUSDT", "price": "90"})
    state_after = client.get("/ui/state").json()

    assert status_before.status_code == 200
    assert len(status_before.json()["active_exits"]) == 3
    assert cancel.status_code == 200
    assert cancel.json()["status"] == "canceled"
    assert cancel.json()["order"]["exit_kind"] == "bracket_cancel"
    assert cancel.json()["order"]["canceled_exit_orders"][0]["status"] == "canceled"
    assert exit_attempt.json()["triggered"] == []
    assert state_after["positions"][0]["quantity"] == "1.00000000"
    assert state_after["active_exits"] == []
    assert [event["event_type"] for event in client.get("/audit").json()["events"]][-1] == "bracket.canceled"


def test_bracket_cancel_replays_from_persisted_order_history(tmp_path):
    db_path = tmp_path / "bracket_cancel_replay.sqlite3"
    repo = SQLiteRepository(db_path)
    app = create_app(repository=repo)
    client = TestClient(app)
    client.post(
        "/webhooks/tradingview",
        json={
            "signal_id": "replay-cancel-bracket",
            "symbol": "BTCUSDT",
            "side": "buy",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_pct": "3",
            "take_profit_pct": "5",
        },
    )
    client.post("/brackets/replay-cancel-bracket/cancel", json={"reason": "operator cancel"})

    restarted_client = TestClient(create_app(repository=SQLiteRepository(db_path)))

    assert restarted_client.get("/brackets/replay-cancel-bracket").status_code == 404
    assert restarted_client.post("/market/price", json={"symbol": "BTCUSDT", "price": "90"}).json()["triggered"] == []
    assert restarted_client.get("/ui/state").json()["positions"][0]["quantity"] == "1.00000000"
