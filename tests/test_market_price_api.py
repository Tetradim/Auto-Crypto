from decimal import Decimal

from fastapi.testclient import TestClient

from autocrypto.app import create_app
from autocrypto.repository import SQLiteRepository
from autocrypto.risk import RiskConfig


def test_market_price_endpoint_triggers_paper_exit_and_audit_event(tmp_path):
    repo = SQLiteRepository(tmp_path / "market.sqlite3")
    app = create_app(repository=repo)
    client = TestClient(app)
    client.get("/ui")
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


def test_market_price_endpoint_reports_oca_canceled_siblings(tmp_path):
    repo = SQLiteRepository(tmp_path / "market_oca.sqlite3")
    app = create_app(repository=repo)
    client = TestClient(app)
    client.get("/ui")
    client.post(
        "/webhooks/tradingview",
        json={
            "signal_id": "market-oca-close",
            "symbol": "BTCUSDT",
            "side": "buy",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_pct": "5",
            "take_profit_pct": "10",
            "trailing_stop_pct": "4",
        },
    )

    response = client.post(
        "/market/price",
        json={"symbol": "BTCUSDT", "price": "110", "include_order_metadata": True},
    )

    assert response.status_code == 200
    assert response.json()["triggered"] == [
        {
            "symbol": "BTC/USDT",
            "kind": "take_profit",
            "price": "110.00000000",
            "quantity": "1.00000000",
            "oca_group": "oca-market-oca-close",
            "trigger_price": "110.00",
            "trigger_gap": "0.00000000",
            "canceled_exit_orders": [
                {
                    "kind": "stop_loss",
                    "trigger_price": "95.00",
                    "close_pct": "100",
                    "oca_group": "oca-market-oca-close",
                    "status": "canceled",
                },
                {
                    "kind": "trailing_stop",
                    "trigger_price": "105.60",
                    "close_pct": "100",
                    "oca_group": "oca-market-oca-close",
                    "status": "canceled",
                },
            ],
        }
    ]
    audit_event = client.get("/audit").json()["events"][-1]
    assert audit_event["event_type"] == "exit.triggered"
    assert audit_event["data"]["triggered"][0]["canceled_exit_orders"][0]["kind"] == "stop_loss"


def test_custom_oca_group_is_used_for_paper_bracket_legs_and_cancel_metadata(tmp_path):
    repo = SQLiteRepository(tmp_path / "custom_oca.sqlite3")
    app = create_app(repository=repo)
    client = TestClient(app)
    client.get("/ui")
    client.post(
        "/webhooks/tradingview",
        json={
            "signal_id": "grouped-entry",
            "symbol": "BTCUSDT",
            "side": "buy",
            "quote_amount": "100",
            "price": "100",
            "bracket": {
                "stop_loss_pct": "5",
                "take_profit_pct": "10",
                "oca_group": "desk-alpha-001",
            },
        },
    )

    status = client.get("/brackets/grouped-entry").json()
    closed = client.post(
        "/market/price",
        json={"symbol": "BTCUSDT", "price": "110", "include_order_metadata": True},
    ).json()

    assert {exit_order["oca_group"] for exit_order in status["active_exits"]} == {"desk-alpha-001"}
    assert closed["triggered"][0]["oca_group"] == "desk-alpha-001"
    assert closed["triggered"][0]["canceled_exit_orders"][0]["oca_group"] == "desk-alpha-001"


def test_market_price_metadata_reports_trigger_gap_for_price_overshoot(tmp_path):
    repo = SQLiteRepository(tmp_path / "trigger_gap.sqlite3")
    app = create_app(repository=repo)
    client = TestClient(app)
    client.get("/ui")
    client.post(
        "/webhooks/tradingview",
        json={
            "signal_id": "gap-entry",
            "symbol": "SOLUSDT",
            "side": "buy",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_pct": "5",
            "take_profit_pct": "10",
        },
    )

    response = client.post(
        "/market/price",
        json={"symbol": "SOLUSDT", "price": "92", "include_order_metadata": True},
    )

    assert response.status_code == 200
    triggered = response.json()["triggered"][0]
    assert triggered["kind"] == "stop_loss"
    assert triggered["trigger_price"] == "95.00"
    assert triggered["trigger_gap"] == "3.00000000"


def test_market_price_metadata_prefers_nearest_trailing_stop_on_gap_through(tmp_path):
    repo = SQLiteRepository(tmp_path / "trailing_gap.sqlite3")
    app = create_app(repository=repo)
    client = TestClient(app)
    client.get("/ui")
    client.post(
        "/webhooks/tradingview",
        json={
            "signal_id": "gap-through-trailer",
            "symbol": "BTCUSDT",
            "side": "buy",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_pct": "10",
            "take_profit_pct": "30",
            "trailing_stop_pct": "5",
        },
    )
    client.post("/market/price", json={"symbol": "BTCUSDT", "price": "120"})

    response = client.post(
        "/market/price",
        json={"symbol": "BTCUSDT", "price": "90", "include_order_metadata": True},
    )

    assert response.status_code == 200
    triggered = response.json()["triggered"][0]
    assert triggered["kind"] == "trailing_stop"
    assert triggered["trigger_price"] == "114.00"
    assert triggered["trigger_gap"] == "24.00000000"
    assert [order["kind"] for order in triggered["canceled_exit_orders"]] == ["stop_loss", "take_profit"]


def test_market_price_exit_reduces_open_notional_for_future_risk(tmp_path):
    repo = SQLiteRepository(tmp_path / "market_risk.sqlite3")
    app = create_app(
        repository=repo,
        risk_config=RiskConfig(max_order_notional=Decimal("500"), max_open_notional=Decimal("150")),
    )
    client = TestClient(app)
    client.get("/ui")
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
    client.get("/ui")
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
    client.get("/ui")
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


def test_market_price_preview_reports_simulated_trailing_ratchet_without_mutating_state(tmp_path):
    repo = SQLiteRepository(tmp_path / "market_preview_trailing.sqlite3")
    app = create_app(repository=repo)
    client = TestClient(app)
    client.get("/ui")
    client.post(
        "/webhooks/tradingview",
        json={
            "signal_id": "preview-trail-entry",
            "symbol": "BTCUSDT",
            "side": "buy",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_pct": "5",
            "take_profit_pct": "20",
            "trailing_stop_pct": "5",
        },
    )

    preview = client.post("/market/price/preview", json={"symbol": "BTCUSDT", "price": "110"})
    state_after_preview = client.get("/ui/state").json()

    assert preview.status_code == 200
    body = preview.json()
    live_trailing = next(exit_order for exit_order in body["active_exits"] if exit_order["kind"] == "trailing_stop")
    preview_trailing = next(
        exit_order for exit_order in body["preview_active_exits"] if exit_order["kind"] == "trailing_stop"
    )
    state_trailing = next(
        exit_order for exit_order in state_after_preview["active_exits"] if exit_order["kind"] == "trailing_stop"
    )
    assert body["would_trigger"] == []
    assert live_trailing["trigger_price"] == "95.00"
    assert preview_trailing["trigger_price"] == "104.50"
    assert preview_trailing["high_water_mark"] == "110"
    assert state_trailing["trigger_price"] == "95.00"
    assert len(state_after_preview["orders"]) == 1
    assert body["trailing_ratchets"] == [
        {
            "signal_id": "preview-trail-entry",
            "before_trigger_price": "95.00",
            "after_trigger_price": "104.50",
            "trigger_change": "9.50",
            "status_before": "open",
            "status_after": "open",
        }
    ]


def test_market_price_response_reports_applied_trailing_ratchet(tmp_path):
    repo = SQLiteRepository(tmp_path / "market_ratchet.sqlite3")
    app = create_app(repository=repo)
    client = TestClient(app)
    client.get("/ui")
    client.post(
        "/webhooks/tradingview",
        json={
            "signal_id": "mark-ratchet-entry",
            "symbol": "ETHUSDT",
            "side": "buy",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_pct": "5",
            "take_profit_pct": "20",
            "trailing_stop_pct": "5",
        },
    )

    response = client.post("/market/price", json={"symbol": "ETHUSDT", "price": "110"})

    assert response.status_code == 200
    assert response.json()["triggered"] == []
    assert response.json()["trailing_ratchets"] == [
        {
            "signal_id": "mark-ratchet-entry",
            "before_trigger_price": "95.00",
            "after_trigger_price": "104.50",
            "trigger_change": "9.50",
            "status_before": "open",
            "status_after": "open",
        }
    ]


def test_bracket_preview_reports_one_signal_trigger_distance_without_mutating_state(tmp_path):
    repo = SQLiteRepository(tmp_path / "bracket_preview.sqlite3")
    app = create_app(repository=repo)
    client = TestClient(app)
    client.get("/ui")
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


def test_bracket_preview_reports_simulated_activation_snapshot_without_mutating_state(tmp_path):
    repo = SQLiteRepository(tmp_path / "bracket_preview_activation.sqlite3")
    app = create_app(repository=repo)
    client = TestClient(app)
    client.get("/ui")
    client.post(
        "/webhooks/tradingview",
        json={
            "signal_id": "preview-activation",
            "symbol": "BTCUSDT",
            "side": "buy",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_pct": "5",
            "take_profit_pct": "20",
            "trailing_stop_pct": "3",
            "trailing_activation_pct": "2",
        },
    )

    preview = client.post("/brackets/preview-activation/preview", json={"price": "103"})
    state_after_preview = client.get("/ui/state").json()

    assert preview.status_code == 200
    body = preview.json()
    live_trailing = next(exit_order for exit_order in body["active_exits"] if exit_order["kind"] == "trailing_stop")
    preview_trailing = next(
        exit_order for exit_order in body["preview_active_exits"] if exit_order["kind"] == "trailing_stop"
    )
    state_trailing = next(
        exit_order for exit_order in state_after_preview["active_exits"] if exit_order["kind"] == "trailing_stop"
    )
    assert body["would_trigger"] == []
    assert live_trailing["status"] == "pending_activation"
    assert live_trailing["computed_trailing_activation_price"] == "102.00"
    assert preview_trailing["status"] == "open"
    assert preview_trailing["trigger_price"] == "99.91"
    assert preview_trailing["trailing_activated"] == "true"
    assert state_trailing["status"] == "pending_activation"


def test_bracket_trailing_stop_preview_path_reports_step_ratchets_without_mutating_state(tmp_path):
    repo = SQLiteRepository(tmp_path / "bracket_trailing_preview_path.sqlite3")
    app = create_app(repository=repo)
    client = TestClient(app)
    client.get("/ui")
    client.post(
        "/webhooks/tradingview",
        json={
            "signal_id": "trail-path-preview",
            "symbol": "BTCUSDT",
            "side": "buy",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_pct": "5",
            "take_profit_pct": "50",
            "trailing_stop_pct": "5",
            "trailing_step_pct": "2",
        },
    )

    preview = client.post(
        "/brackets/trail-path-preview/trailing-stop/preview-path",
        json={"prices": ["101", "103"]},
    )
    state_after_preview = client.get("/ui/state").json()

    assert preview.status_code == 200
    body = preview.json()
    assert body["mutates_state"] is False
    assert body["steps"][0]["ratcheted"] is False
    assert body["steps"][0]["before"][0]["trailing_ratchet_ready_at_mark"] == "false"
    assert body["steps"][1]["ratcheted"] is True
    assert body["steps"][1]["before"][0]["next_trailing_trigger"] == "97.85"
    assert body["steps"][1]["after"][0]["trigger_price"] == "97.85"
    assert body["final_preview_trailing"][0]["trigger_price"] == "97.85"
    active_trailing = next(
        exit_order for exit_order in state_after_preview["active_exits"] if exit_order["kind"] == "trailing_stop"
    )
    assert active_trailing["trigger_price"] == "95.00"
    assert len(state_after_preview["orders"]) == 1


def test_bracket_trailing_stop_preview_path_reports_activation_without_mutating_state(tmp_path):
    repo = SQLiteRepository(tmp_path / "bracket_trailing_activation_path.sqlite3")
    app = create_app(repository=repo)
    client = TestClient(app)
    client.get("/ui")
    client.post(
        "/webhooks/tradingview",
        json={
            "signal_id": "trail-activation-path",
            "symbol": "ETHUSDT",
            "side": "buy",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_pct": "5",
            "take_profit_pct": "50",
            "trailing_stop_pct": "4",
            "trailing_activation_pct": "3",
        },
    )

    preview = client.post(
        "/brackets/trail-activation-path/trailing-stop/preview-path",
        json={"marks": ["102", "104"]},
    )
    state_after_preview = client.get("/ui/state").json()

    assert preview.status_code == 200
    body = preview.json()
    assert body["steps"][0]["activated"] is False
    assert body["steps"][0]["after"][0]["status"] == "pending_activation"
    assert body["steps"][1]["activated"] is True
    assert body["steps"][1]["after"][0]["status"] == "open"
    assert body["steps"][1]["after"][0]["trigger_price"] == "99.84"
    active_trailing = next(
        exit_order for exit_order in state_after_preview["active_exits"] if exit_order["kind"] == "trailing_stop"
    )
    assert active_trailing["status"] == "pending_activation"
    assert active_trailing["trigger_price"] == "96.00"


def test_bracket_list_includes_remaining_risk_summary(tmp_path):
    repo = SQLiteRepository(tmp_path / "bracket_summary.sqlite3")
    app = create_app(repository=repo)
    client = TestClient(app)
    client.get("/ui")
    client.post(
        "/webhooks/tradingview",
        json={
            "signal_id": "summary-entry",
            "symbol": "BTCUSDT",
            "side": "buy",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_pct": "5",
            "take_profit_pct": "10",
        },
    )

    listed = client.get("/brackets").json()["brackets"][0]
    status = client.get("/brackets/summary-entry").json()

    assert listed["summary"] == {
        "remaining_notional": "100",
        "protective_exit_kind": "stop_loss",
        "protective_trigger_price": "95.00",
        "protective_distance_pct": "5.00",
        "worst_case_loss": "5.00",
        "protective_locked_pnl": "-5.00",
        "first_target_price": "110.00",
        "first_target_reward": "10.00",
        "first_target_reward_risk_ratio": "2",
        "total_target_reward": "10.00",
        "total_target_reward_risk_ratio": "2",
    }
    assert status["summary"] == listed["summary"]


def test_bracket_breakeven_endpoint_moves_protective_exits_and_records_audit(tmp_path):
    repo = SQLiteRepository(tmp_path / "bracket_breakeven.sqlite3")
    app = create_app(repository=repo)
    client = TestClient(app)
    client.get("/ui")
    client.post(
        "/webhooks/tradingview",
        json={
            "signal_id": "api-breakeven",
            "symbol": "BTCUSDT",
            "side": "buy",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_pct": "5",
            "take_profit_pct": "10",
            "trailing_stop_pct": "4",
        },
    )

    amended = client.post("/brackets/api-breakeven/breakeven", json={"reason": "lock risk"})
    loosened = client.post("/brackets/api-breakeven/breakeven", json={"reason": "already locked"})
    active_exits = amended.json()["active_exits"]

    assert amended.status_code == 200
    assert amended.json()["order"]["exit_kind"] == "bracket_breakeven"
    assert [(exit_order["kind"], exit_order["trigger_price"]) for exit_order in active_exits] == [
        ("stop_loss", "100.00"),
        ("take_profit", "110.00"),
        ("trailing_stop", "100.00"),
    ]
    assert loosened.status_code == 409
    assert repo.list_orders()[-1]["exit_kind"] == "bracket_breakeven"
    assert repo.list_audit()[-1].event_type == "bracket.breakeven_amended"


def test_bracket_cancel_endpoint_cancels_exits_and_records_audit(tmp_path):
    repo = SQLiteRepository(tmp_path / "bracket_cancel.sqlite3")
    app = create_app(repository=repo)
    client = TestClient(app)
    client.get("/ui")
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
    client.get("/ui")
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
    restarted_client.get("/ui")

    assert restarted_client.get("/brackets/replay-cancel-bracket").status_code == 404
    assert restarted_client.post("/market/price", json={"symbol": "BTCUSDT", "price": "90"}).json()["triggered"] == []
    assert restarted_client.get("/ui/state").json()["positions"][0]["quantity"] == "1.00000000"
